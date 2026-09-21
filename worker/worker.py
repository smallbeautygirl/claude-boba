"""Worker：出租者機器上的常駐程式。

跑在 host 上，不在容器裡 —— 把它容器化就需要掛 /var/run/docker.sock，
而那等同給該容器 root 權限，與「借用者的內容不該碰到出租者機器」衝突。
決策紀錄見 SPEC.md §11「誰啟動 job 容器」。

流程：long-poll 領單 → 起 job 容器 → 邊讀 stream-json 邊回報 → 回報結果。
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict

# 事件批次回報的間隔。太短會把 Hub 打爆，太長使用者會覺得畫面卡住。
FLUSH_INTERVAL_SECONDS = 0.5
# long-poll 的 client timeout 要比 Hub 的 hold 時間長，否則每次都是客戶端先斷。
POLL_TIMEOUT_SECONDS = 60.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    hub_url: str = "http://127.0.0.1:8787"
    # 在網頁「我的 worker」按「產生 token」拿到，貼進 .env。
    # worker 因此完全不碰出租者的 Observ 帳密。
    worker_token: str = ""
    claude_credentials: str = ""
    job_budget_usd: Decimal = Decimal(5)
    timeout_seconds: int = 600
    max_concurrency: int = 1
    available_models: str = "sonnet,haiku"
    allow_full_network: bool = False
    worker_image: str = "claude-boba-worker:2.1.278"
    job_root: Path = Path(".jobs")


settings = Settings()
HERE = Path(__file__).resolve().parent

# 啟動時量一次就好。每個 job 都跑 docker run 只為了問版本太浪費，
# 而 worker 執行期間 image 不會換。
LENDER_CLI_VERSION: str | None = None


def _cli_version() -> str | None:
    try:
        out = subprocess.run(
            ["docker", "run", "--rm", settings.worker_image, "claude", "--version"],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        return out.stdout.strip().split()[0] if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, IndexError):
        # 版本拿不到不該讓 worker 起不來 —— 它只用於版本漂移的診斷（SPEC.md §11），
        # 不是執行 job 的必要條件。
        return None


async def report_config(client: httpx.AsyncClient) -> str:
    """回報自己的設定，順便確認 token 有效。回傳 worker 名稱。"""
    global LENDER_CLI_VERSION
    LENDER_CLI_VERSION = _cli_version()
    resp = await client.post(
        "/api/worker/config",
        json={
            "allow_full_network": settings.allow_full_network,
            "available_models": [m for m in settings.available_models.split(",") if m],
            "job_budget_usd": str(settings.job_budget_usd),
            "max_concurrency": settings.max_concurrency,
            "claude_code_version": LENDER_CLI_VERSION,
        },
    )
    if resp.status_code == 401:
        sys.exit("WORKER_TOKEN 無效。請到網頁的「我的 worker」重新產生一組。")
    resp.raise_for_status()
    return resp.json()["name"]


async def run_job(client: httpx.AsyncClient, job: dict[str, Any]) -> None:
    job_id = job["job_id"]
    workdir = (settings.job_root / job_id).resolve()
    workdir.mkdir(parents=True, exist_ok=True)

    env = {
        "JOB_WORKDIR": str(workdir),
        "CLAUDE_CREDENTIALS": settings.claude_credentials,
        "TIMEOUT_SECONDS": str(settings.timeout_seconds),
        "JOB_BUDGET_USD": str(job.get("job_budget_usd", settings.job_budget_usd)),
        "AVAILABLE_MODELS": ",".join(job.get("available_models") or ["sonnet"]),
        "WORKER_IMAGE": settings.worker_image,
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }

    proc = await asyncio.create_subprocess_exec(
        str(HERE / "run-job.sh"),
        job_id,
        job["prompt"],
        job.get("model") or "sonnet",
        job.get("transcript_key") or "",
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    state = _JobState(client, job_id)
    try:
        await asyncio.gather(
            _pump(proc.stdout, state),
            _drain(proc.stderr, state),
        )
        await proc.wait()
    finally:
        await state.flush()

    await state.finish(proc.returncode or 0)
    shutil.rmtree(workdir, ignore_errors=True)


class _JobState:
    """累積事件、定期回報、記下最後的 result。"""

    def __init__(self, client: httpx.AsyncClient, job_id: str) -> None:
        self.client = client
        self.job_id = job_id
        self.buffer: list[dict[str, Any]] = []
        self.next_seq = 1
        self.result: dict[str, Any] | None = None
        self.stderr_tail: list[str] = []
        self.cancelled = False
        self._last_flush = 0.0

    async def add(self, event: dict[str, Any]) -> None:
        if event.get("type") == "result":
            self.result = event
        self.buffer.append(event)
        now = asyncio.get_running_loop().time()
        if now - self._last_flush >= FLUSH_INTERVAL_SECONDS:
            await self.flush()

    async def flush(self) -> None:
        if not self.buffer:
            return
        batch, self.buffer = self.buffer, []
        resp = await self.client.post(
            f"/api/worker/jobs/{self.job_id}/events",
            json={"from_seq": self.next_seq, "events": batch},
        )
        resp.raise_for_status()
        body = resp.json()
        self.next_seq = body["next_seq"]
        self._last_flush = asyncio.get_running_loop().time()
        # 出租者按了停止。控制指令夾在 events 的回應裡回傳（SPEC.md §9）。
        if body.get("cancel") and not self.cancelled:
            self.cancelled = True
            # 必須用非同步版本。同步的 subprocess.run 會卡住事件迴圈，
            # 而這行正好發生在「出租者按停止」那一刻 —— 最不該卡住的時候。
            killer = await asyncio.create_subprocess_exec(
                "docker",
                "kill",
                f"boba-job-{self.job_id}",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()

    async def finish(self, returncode: int) -> None:
        payload = _result_payload(
            self.result, returncode, self.cancelled, self.stderr_tail
        )
        resp = await self.client.post(
            f"/api/worker/jobs/{self.job_id}/result", json=payload
        )
        resp.raise_for_status()


def _result_payload(
    result: dict[str, Any] | None,
    returncode: int,
    cancelled: bool,
    stderr_tail: list[str],
) -> dict[str, Any]:
    # 每個分支都帶版本。版本漂移的診斷價值在**失敗時**最高，
    # 只在成功時記錄等於在最需要它的時候沒有它（SPEC.md §11 spike 3）。
    base = {"lender_cli_version": LENDER_CLI_VERSION}

    if cancelled:
        return base | {"status": "cancelled", "error_kind": "cancelled_by_lender"}
    # timeout(1) 逾時殺掉子程序時回 124。
    if returncode == 124:
        return base | {"status": "timeout", "error_kind": "timeout"}
    if result is None:
        return base | {
            "status": "failed",
            "error_kind": "no_result_event",
            "error_detail": "\n".join(stderr_tail[-20:]) or f"exit code {returncode}",
        }

    if result.get("is_error"):
        # --max-budget-usd 觸發時的確切 subtype 尚未實測（沒有便宜的方式製造它），
        # 所以先用字串比對，並保留 failed 作為預設 —— 兩者都不計債，
        # 分類錯誤只影響顯示，不影響帳。
        blob = json.dumps(result, ensure_ascii=False).lower()
        status = "over_budget" if "budget" in blob else "failed"
        return base | {
            "status": status,
            "error_kind": result.get("subtype")
            or result.get("terminal_reason")
            or "api_error",
            "error_detail": str(result.get("result"))[:4000],
            "total_cost_usd": str(result.get("total_cost_usd") or 0),
            "model_usage": result.get("modelUsage") or {},
        }

    return base | {
        "status": "succeeded",
        "result_text": result.get("result"),
        "total_cost_usd": str(result.get("total_cost_usd") or 0),
        "model_usage": result.get("modelUsage") or {},
    }


async def _pump(stream: asyncio.StreamReader | None, state: _JobState) -> None:
    if stream is None:
        return
    while line := await stream.readline():
        text = line.decode("utf-8", "replace").strip()
        if not text:
            continue
        try:
            await state.add(json.loads(text))
        except json.JSONDecodeError:
            # stream-json 理論上每行都是 JSON；不是的話當成 stderr 處理，不要中止 job。
            state.stderr_tail.append(text)


async def _drain(stream: asyncio.StreamReader | None, state: _JobState) -> None:
    if stream is None:
        return
    while line := await stream.readline():
        state.stderr_tail.append(line.decode("utf-8", "replace").rstrip())


async def main() -> None:
    if not settings.claude_credentials:
        sys.exit("CLAUDE_CREDENTIALS 未設定。見 worker/.env.example")
    if not settings.worker_token:
        sys.exit("WORKER_TOKEN 未設定。到網頁的「我的 worker」產生一組，貼進 .env。")

    async with httpx.AsyncClient(
        base_url=settings.hub_url,
        timeout=POLL_TIMEOUT_SECONDS,
        headers={"X-Worker-Token": settings.worker_token},
    ) as client:
        name = await report_config(client)
        print(f"[worker] 以 {name} 的身分開始領單", flush=True)

        while True:
            try:
                resp = await client.get("/api/worker/poll")
            except httpx.HTTPError as exc:
                print(f"[worker] 連不上 Hub：{exc}；5 秒後重試", flush=True)
                await asyncio.sleep(5)
                continue

            if resp.status_code == 204:
                continue
            resp.raise_for_status()
            job = resp.json()
            print(f"[worker] 接下 job {job['job_id']}", flush=True)
            try:
                await run_job(client, job)
            except (httpx.HTTPError, OSError, ValueError) as exc:
                # 單一 job 出事不該讓 worker 整個停掉 —— 出租者不會盯著它。
                print(f"[worker] job 失敗：{exc!r}", flush=True)
            else:
                print(f"[worker] job {job['job_id']} 結束", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
