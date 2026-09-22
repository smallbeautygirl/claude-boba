"""Worker：出租者機器上的常駐程式。

跑在 host 上，不在容器裡 —— 把它容器化就需要掛 /var/run/docker.sock，
而那等同給該容器 root 權限，與「借用者的內容不該碰到出租者機器」衝突。
決策紀錄見 SPEC.md §11「誰啟動 job 容器」。

流程：long-poll 領單 → 起 job 容器 → 邊讀 stream-json 邊回報 → 回報結果。
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import shutil
import socket
import subprocess
import sys
import zipfile
import zlib
from pathlib import Path
from typing import Any

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict

# 單一 job 最多上傳幾個產出檔案。與 Hub 的 MAX_ARTIFACTS 一致。
MAX_ARTIFACTS = 50
# 事件批次回報的間隔。太短會把 Hub 打爆，太長使用者會覺得畫面卡住。
FLUSH_INTERVAL_SECONDS = 0.5
# 即使沒有事件也要定期回報一次，否則**安靜的 job 收不到停止指令** ——
# 控制指令是夾在事件回報的回應裡回來的（SPEC.md §9）。
# 而會讓人想按停止的，往往正是那種卡住不動、什麼都不輸出的 job。
CONTROL_POLL_SECONDS = 3.0
# long-poll 的 client timeout 要比 Hub 的 hold 時間長，否則每次都是客戶端先斷。
POLL_TIMEOUT_SECONDS = 60.0

# 帶 codebase 進來的 job 用這個，而不是 `settings.timeout_seconds`。
#
# timeout 在這裡**不是成本閘門** —— `--max-budget-usd` 才是，而它封在代跑者自己
# 在出借設定裡填的上限。所以拉長不會讓他多花錢；真正被佔用的是 `max_concurrency`
# 的兩個執行位之一，而那個代價落在其他委託者身上。
#
# 那 timeout 還留著做什麼：擋**不花 token 的卡死**。一個卡在 git、或卡在無限
# 迴圈 Bash 的 job 永遠撞不到 budget，會一直佔著位子。
LONG_TIMEOUT_SECONDS = 1800


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    hub_url: str = "http://127.0.0.1:8787"
    # 這台主機的身分，對應 hub 的 WORKER_SHARED_TOKEN。
    #
    # ⚠️ 2026-09-22：它識別的是**這台主機**，不是某位代跑者（SPEC §4.12）。
    # 一台主機服務所有出借帳號，憑證隨每個 job 派下來。
    worker_token: str = ""
    claude_credentials: str = ""
    timeout_seconds: int = 600
    # 這台主機同時跑幾個 job。**每個帳號**能同時跑幾個是 hub 那邊的事 ——
    # 那是帳號的物理性質，主機管不到。
    max_concurrency: int = 2
    worker_image: str = "claude-boba-worker:2.1.278"
    # 開放外網時用哪個 docker network。預設 bridge：白名單那條路走 proxy，
    # 這條路不走 —— 兩者不能共用同一個網路。
    open_network: str = "bridge"
    job_root: Path = Path(".jobs")

    # ⚠️ 出借條件（花費上限、可用 model、外網）**不再由這裡設定**。
    # 它們屬於代跑者，在網頁上設，隨每個 job 派下來 —— 主機只有一台、代跑者
    # 有很多位，讓 .env 決定等於某個人的設定被另一個人的蓋掉（SPEC §4.12）。


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
    """回報這台主機，順便確認 token 有效。回傳主機名稱。

    **不回報出借條件** —— 見 Settings 上的那段。
    """
    global LENDER_CLI_VERSION
    LENDER_CLI_VERSION = _cli_version()
    resp = await client.post(
        "/api/worker/config",
        json={
            "name": socket.gethostname()[:80],
            "max_concurrency": settings.max_concurrency,
            "claude_code_version": LENDER_CLI_VERSION,
        },
    )
    if resp.status_code == 401:
        sys.exit("WORKER_TOKEN 無效。它要跟 hub 的 WORKER_SHARED_TOKEN 一致。")
    resp.raise_for_status()
    return resp.json()["name"]


# org 層級的 skill（pptx / xlsx / docx / pdf …）由 Claude Code 在背景同步進 HOME。
# 問題是：那是背景預抓，第一次執行時來不及 —— 而每個 job 都是全新 HOME，
# 所以每個 job 都是「第一次」，那些 skill 永遠用不到，還每次白抓 4.3MB。
#
# 解法：worker 啟動時暖一個 template HOME，之後每個 job 從它複製。
# 只複製 org 同步下來的內容，不複製出租者的任何個人設定
# （見 .claude/rules/security.md 紅線 2 的明細）。
# 相對於 HOME 底下的 .claude/
_SYNCED_SUBDIRS = ("skills/synced", "plugins/synced")
# template 的內部結構與 job 工作目錄相同（<template>/.home/.claude），
# 因為同步只在「工作目錄是掛載進來的專案目錄」時才會觸發 —— 實測確認。
TEMPLATE_HOME = Path(".home-template")
_WARM_TIMEOUT_SECONDS = 180


def _template_claude() -> Path:
    return (settings.job_root / TEMPLATE_HOME).resolve() / ".home" / ".claude"


async def warm_template_home() -> bool:
    """跑一次拋棄式的 job 讓 org skill 同步下來，之後每個 job 重複使用。

    成本是啟動時一次很便宜的 haiku 呼叫。不做的話，BD/PM 最主力的
    「做簡報」「做表格」在每個 job 都會回「這個指令沒安裝」。
    """
    template = _template_claude()
    if any((template / sub).is_dir() for sub in _SYNCED_SUBDIRS):
        print("[worker] org skills template 已存在，沿用", flush=True)
        return True
    template.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        str(HERE / "warm-home.sh"),
        str((settings.job_root / TEMPLATE_HOME).resolve()),
        env={
            "CLAUDE_CREDENTIALS": settings.claude_credentials,
            "WORKER_IMAGE": settings.worker_image,
            "PATH": "/usr/bin:/bin:/usr/local/bin",
        },
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.wait_for(proc.wait(), timeout=_WARM_TIMEOUT_SECONDS)
    except TimeoutError:
        proc.kill()
    ok = any((template / sub).is_dir() for sub in _SYNCED_SUBDIRS)
    print(
        "[worker] org skills 已就緒"
        if ok
        else "[worker] org skills 暖機失敗，pptx/xlsx 這類指令在這台 worker 上會不可用",
        flush=True,
    )
    return ok


def _seed_synced(home: Path) -> None:
    """把 template 裡 org 同步下來的內容複製給這個 job。

    只複製 `_SYNCED_SUBDIRS` 這兩個目錄 —— 不是整個 .claude。
    出租者的 settings.json、個人 plugins、個人 skills 一律不進來。
    """
    template = _template_claude()
    for sub in _SYNCED_SUBDIRS:
        src = template / sub
        if src.is_dir():
            shutil.copytree(src, home / sub, dirs_exist_ok=True, symlinks=False)


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


async def _download(client: httpx.AsyncClient, url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    async with client.stream("GET", url) as resp:
        resp.raise_for_status()
        with target.open("wb") as fh:
            async for chunk in resp.aiter_bytes():
                fh.write(chunk)


async def _place_attachments(
    client: httpx.AsyncClient, workdir: Path, attachments: list[dict[str, Any]]
) -> dict[str, str]:
    """把借用者的輸入檔放進工作目錄根層，回傳 {相對路徑: 內容雜湊}。

    放根層而不是子目錄，因為 Claude 一進去就該看到它們 —— 這些是 job 的標的。

    回傳的雜湊供 `_collect_artifacts()` 判斷哪些是「原封不動的輸入」。
    """
    placed: dict[str, str] = {}
    for item in attachments:
        name = Path(item["name"]).name or "attachment"
        target = workdir / name
        # 同名就加序號，不要讓後一個蓋掉前一個 —— 使用者傳了兩份就是要兩份。
        stem, suffix, n = target.stem, target.suffix, 2
        while target.exists():
            target = workdir / f"{stem}-{n}{suffix}"
            n += 1
        await _download(client, item["url"], target)
        placed[target.name] = _digest(target)
    return placed


# 解一個 zip、摸清 repo 結構、跑一輪測試，十分鐘很容易不夠。
#
# 條件是 zip 而不是「有沒有附件」：zip 是唯一一個「慢」的原因直接寫在條件裡的。
# 一張 200 KB 的截圖也拿到 30 分鐘沒有道理，而「看指令」會漏掉帶著 repo 問一般
# 問題的那些。
_SLOW_INPUT_SUFFIXES = {".zip"}


def timeout_for_inputs(inputs: dict[str, str]) -> int:
    """這個 job 給幾秒。

    長的那個是**下限不是上限** —— 主機管理者把 TIMEOUT_SECONDS 調高，不該因為
    附件裡有 zip 就被砍回來。
    """
    slow = any(Path(name).suffix.lower() in _SLOW_INPUT_SUFFIXES for name in inputs)
    if not slow:
        return settings.timeout_seconds
    return max(settings.timeout_seconds, LONG_TIMEOUT_SECONDS)


async def _prepare_workdir(
    client: httpx.AsyncClient, job: dict[str, Any]
) -> tuple[Path, str, dict[str, str]]:
    """建好這個 job 的工作目錄，回傳 (目錄, 要 resume 的檔名)。

    每個 job 一個全新目錄，跑完刪掉。HOME 也在裡面（`.home/`），所以出租者的
    個人設定一樣不存在 —— 隔離性與先前的 tmpfs 相同，差別只在 transcript
    活得過容器，那是「接著問」的前提（SPEC.md §4.2）。
    """
    workdir = (settings.job_root / job["job_id"]).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    _seed_synced(workdir / ".home" / ".claude")

    # 站台 curated 的 commands 與 skills（worker/job-claude/）。
    # 後複製、覆蓋同名檔 —— 不讓借用者用自己的版本蓋掉團隊審過的內容。
    curated = HERE / "job-claude"
    if curated.is_dir():
        shutil.copytree(curated, workdir / ".claude", dirs_exist_ok=True)
        # README 是給我們看的，不該被 Claude 當成 job 的一部分讀進去。
        (workdir / ".claude" / "README.md").unlink(missing_ok=True)

    resume_name = ""
    if url := job.get("resume_from_url"):
        # 放進 .home/ 而不是工作目錄根層。實測：`--resume <path>` 會把續跑的
        # transcript 寫在**被 resume 檔案的同一個目錄**。放在根層的話那份
        # 300KB 的 transcript 會被當成 job 的產出檔案上傳給使用者看。
        resume_name = f"{HOME_DIR}/resume.jsonl"
        target = workdir / resume_name
        target.parent.mkdir(parents=True, exist_ok=True)
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with target.open("wb") as fh:
                async for chunk in resp.aiter_bytes():
                    fh.write(chunk)

    inputs = await _place_attachments(client, workdir, job.get("attachments") or [])
    return workdir, resume_name, inputs


# 這兩個目錄絕對不能上傳。
#   .home/  是容器的 HOME，裡面掛著出租者的 .credentials.json ——
#           上傳它等於把 Anthropic 憑證送上 S3
#   .claude/ 是我們自己複製進去的 curated skills，不是 job 的產出
HOME_DIR = ".home"
# `.git/` 不是憑證問題，是噪音問題：Claude 跑過 `git add` 之後 objects 目錄裡會多出
# 一堆 zip 裡沒有的檔案，而沒有人想一個一個下載 git 的內部檔案。它們還會吃掉
# MAX_ARTIFACTS 的名額，把真正的產出擠掉。
_NEVER_UPLOAD = {HOME_DIR, ".claude", ".git"}
_NOT_OUTPUT = {"resume.jsonl"}


# 解開的 zip 不是產出（docs/web-spec.md §3）。
#
# 委託者帶 codebase 進來的路徑是「壓成一個 zip，Claude 自己解」。解開之後那棵樹
# 對 _collect_artifacts() 而言全是新檔案 —— 不排除的話「產出的檔案」會被 50 個
# repo 檔塞滿，而 Claude 真正寫出來的東西被 MAX_ARTIFACTS 擠掉。
#
# 比對用 zip 自己的 (大小, CRC32) 而**不是路徑**：`unzip` 可能解在根層、也可能解進
# 一個子目錄，路徑對不上規則就失效了。內容一樣就是沒動過，解在哪裡都一樣。
# 改過的照樣傳回去 —— 與既有附件那條規則一致。
#
# 只讀中央目錄（`namelist` / `infolist`），不解壓縮，所以 zip bomb 在這裡不成立。
_ZIP_SUFFIX = ".zip"
_MAX_ZIP_MEMBERS = 50_000


def unpacked_fingerprints(workdir: Path) -> set[tuple[int, int]]:
    """工作目錄裡每個 zip 的成員指紋 `{(大小, CRC32)}`。沒有 zip 就是空的。"""
    seen: set[tuple[int, int]] = set()
    for path in workdir.rglob(f"*{_ZIP_SUFFIX}"):
        rel = path.relative_to(workdir)
        if rel.parts[0] in _NEVER_UPLOAD or path.is_symlink() or not path.is_file():
            continue
        try:
            with zipfile.ZipFile(path) as zf:
                for info in zf.infolist()[:_MAX_ZIP_MEMBERS]:
                    if not info.is_dir():
                        seen.add((info.file_size, info.CRC))
        except (zipfile.BadZipFile, OSError) as exc:
            # 壞掉的 zip 只代表「沒有東西可以排除」，不該讓上傳整個爆掉。
            print(f"[worker] 讀不了 {rel} 的內容清單：{exc!r}", flush=True)
    return seen


def _fingerprint(path: Path) -> tuple[int, int]:
    return (path.stat().st_size, zlib.crc32(path.read_bytes()) & 0xFFFFFFFF)


def _collect_artifacts(
    workdir: Path,
    inputs: dict[str, str] | None = None,
    unpacked: set[tuple[int, int]] | None = None,
) -> list[Path]:
    """挑出這個 job 真正產出的檔案。

    排除規則寫得保守，因為錯一次的後果是把出租者的憑證上傳到 S3：

    - 跳過 `.home/`、`.claude/` 整棵樹
    - 跳過 symlink。job 可以做一個指向 `.home/.claude/.credentials.json`
      的連結，光看副檔名是看不出來的
    - 解析後的真實路徑必須仍在 workdir 內
    """
    out: list[Path] = []
    for path in workdir.rglob("*"):
        rel = path.relative_to(workdir)
        if rel.parts[0] in _NEVER_UPLOAD or rel.name in _NOT_OUTPUT:
            continue
        if path.is_symlink() or not path.is_file():
            continue
        # 借用者自己傳進來的檔案，原封不動就不要再傳回去 —— 五份沒動過的 PDF
        # 出現在「產出的檔案」裡只是噪音。但**改過的要傳**：
        # 「幫我改這份簡報」的成果就是那個檔案，照檔名排除會讓人什麼都拿不到。
        # 用內容雜湊比對而不是 mtime：有些工具會原樣重寫檔案，mtime 會誤判。
        if inputs and (before := inputs.get(str(rel))) and _digest(path) == before:
            continue
        # 從 zip 裡原封不動解出來的，同樣是「傳進去的東西」。
        if unpacked and path.suffix.lower() != _ZIP_SUFFIX:
            try:
                if _fingerprint(path) in unpacked:
                    continue
            except OSError:
                pass
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(workdir.resolve())
        except (OSError, ValueError):
            continue
        out.append(path)
    return out


async def _upload_artifacts(
    client: httpx.AsyncClient, job_id: str, workdir: Path, inputs: dict[str, str]
) -> int:
    files = _collect_artifacts(workdir, inputs, unpacked_fingerprints(workdir))
    if not files:
        return 0

    manifest = [
        {"name": str(f.relative_to(workdir)), "size_bytes": f.stat().st_size}
        for f in files[:MAX_ARTIFACTS]
    ]
    resp = await client.post(
        f"/api/worker/jobs/{job_id}/artifacts", json={"files": manifest}
    )
    resp.raise_for_status()
    body = resp.json()
    if skipped := body.get("skipped"):
        print(f"[worker] 這些檔案太大，略過：{skipped}", flush=True)

    uploaded = 0
    for item in body.get("uploads", []):
        target = workdir / item["name"]
        try:
            put = await client.put(
                item["put_url"], content=target.read_bytes(), timeout=180.0
            )
            put.raise_for_status()
        except (httpx.HTTPError, OSError) as exc:
            print(f"[worker] 上傳 {item['name']} 失敗：{exc!r}", flush=True)
            continue
        uploaded += 1
    return uploaded


def _find_transcript(workdir: Path) -> Path | None:
    """找出這次執行留下的 transcript。

    兩個位置都要找，因為 resume 與否落點不同：

    - 一般執行：`$HOME/.claude/projects/<目錄slug>/<session>.jsonl`
    - resume：寫在**被 resume 檔案的同一個目錄**，也就是 `$HOME/` 根層。
      `projects/` 這時是空的

    只找 `projects/` 的話，續問鏈的第二層以後永遠上傳不到新的 transcript，
    於是每個後續 job 都接在鏈的最前面，中間的對話全部遺失。
    """
    home = workdir / HOME_DIR
    found = [
        f
        for f in [
            *(home / ".claude" / "projects").glob("*/*.jsonl"),
            *home.glob("*.jsonl"),
        ]
        if f.name != "resume.jsonl"
    ]
    return max(found, key=lambda f: f.stat().st_mtime) if found else None


async def _upload_transcript(client: httpx.AsyncClient, url: str, path: Path) -> bool:
    try:
        resp = await client.put(url, content=path.read_bytes(), timeout=120.0)
        resp.raise_for_status()
    except (httpx.HTTPError, OSError) as exc:
        # 上傳失敗只代表不能接著問，不該讓整個 job 算失敗 —— 結果已經跑出來了。
        print(f"[worker] transcript 上傳失敗（此 job 無法接續）：{exc!r}", flush=True)
        return False
    return True


async def _refuse(client: httpx.AsyncClient, job_id: str, why: str) -> None:
    """還沒開容器就拒掉這個 job。

    走一般的 result 回報，狀態是 failed —— 失敗不計債（SPEC §5），所以借用者
    不會為了一個從沒跑起來的 job 欠人情。error_detail 要講得出原因，不然他
    只看得到「執行失敗」，會以為工具壞了。
    """
    resp = await client.post(
        f"/api/worker/jobs/{job_id}/result",
        json={
            "status": "failed",
            "error_kind": "model_not_allowed",
            "error_detail": why,
            "lender_cli_version": LENDER_CLI_VERSION,
            "transcript_uploaded": False,
        },
    )
    resp.raise_for_status()


async def run_job(client: httpx.AsyncClient, job: dict[str, Any]) -> None:
    job_id = job["job_id"]

    # 🚨 **立刻把 token 從 dict 裡拿走。** 派單 payload 現在可能含一組一年期
    # 憑證，而 job 這個 dict 會被傳來傳去。留在裡面的話，任何一個 print(job)、
    # 任何一個把 job 放進例外訊息的地方，都會變成外洩（security.md 紅線 2）。
    # pop 之後 dict 就是安全的，只有下面這個區域變數需要小心。
    oauth_token = job.pop("oauth_token", None)

    # 出租者自己的最後一道防線。Hub 已經擋過一次，但燒的是**這台機器的**額度，
    # 所以不能只靠上游。
    #
    # CLI 那層是不會擋的：2026-09-22 spike #8 實測 `--settings availableModels`
    # 不約束 model（新舊兩條憑證路徑都一樣，它只拿那份清單擋 fast mode），而
    # run-job.sh 是把 `--model` 直接帶過去的。所以少了這裡，Hub 一有 bug 就
    # 直接變成「別人用我的帳號跑 Opus」。
    # 白名單隨 job 派下來 —— 它是**那位代跑者**的條件，不是這台主機的。
    # 舊版讀 worker/.env，而託管模型下那等於用主機管理者的設定去約束每個人。
    allowed = job.get("available_models") or []
    model = job.get("model") or "sonnet"
    if model not in allowed:
        await _refuse(client, job_id, f"這位代跑者沒有開放 {model}")
        return

    workdir, resume_name, inputs = await _prepare_workdir(client, job)

    env = {
        "JOB_WORKDIR": str(workdir),
        "CLAUDE_CREDENTIALS": settings.claude_credentials,
        "TIMEOUT_SECONDS": str(timeout_for_inputs(inputs)),
        "JOB_BUDGET_USD": str(job.get("job_budget_usd", "5")),
        "AVAILABLE_MODELS": ",".join(allowed),
        # 網路模式也是那位代跑者的條件。空字串 = 走白名單 proxy（預設）。
        "OPEN_NETWORK": settings.open_network if job.get("allow_full_network") else "",
        "WORKER_IMAGE": settings.worker_image,
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }

    # 只在有的時候放進去。run-job.sh 看到它就改走環境變數那條路，
    # 沒有就沿用掛載 .credentials.json 的舊路。
    if oauth_token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token

    proc = await asyncio.create_subprocess_exec(
        str(HERE / "run-job.sh"),
        job_id,
        job["prompt"],
        job.get("model") or "sonnet",
        resume_name,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    state = _JobState(client, job_id)
    control = asyncio.create_task(_control_loop(state))
    try:
        await asyncio.gather(
            _pump(proc.stdout, state),
            _drain(proc.stderr, state),
        )
        await proc.wait()
    finally:
        control.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await control
        await state.flush(force=True)

    uploaded = False
    put_url = job.get("transcript_put_url")
    if put_url and (found := _find_transcript(workdir)):
        uploaded = await _upload_transcript(client, put_url, found)

    try:
        n = await _upload_artifacts(client, job_id, workdir, inputs)
        if n:
            print(f"[worker] 上傳了 {n} 個產出檔案", flush=True)
    except (httpx.HTTPError, OSError) as exc:
        # 檔案上傳失敗不該讓 job 算失敗 —— 結果文字已經拿到了。
        print(f"[worker] 產出檔案上傳失敗：{exc!r}", flush=True)

    await state.finish(proc.returncode or 0, transcript_uploaded=uploaded)
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

    async def flush(self, *, force: bool = False) -> None:
        """回報累積的事件。`force` 會在沒有事件時也打一次，只為了取回控制指令。"""
        if not self.buffer and not force:
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

    async def finish(
        self, returncode: int, *, transcript_uploaded: bool = False
    ) -> None:
        payload = _result_payload(
            self.result, returncode, self.cancelled, self.stderr_tail
        )
        payload["transcript_uploaded"] = transcript_uploaded
        resp = await self.client.post(
            f"/api/worker/jobs/{self.job_id}/result", json=payload
        )
        resp.raise_for_status()


# 認證失效的跡象。只比對**講原因的欄位**，不比對整包 JSON ——
# 那正是 over_budget 誤判的成因。
_AUTH_SIGNS = (
    "oauth access token is invalid",
    "authentication_failed",
    "authentication_error",
    "failed to authenticate",
    "401",
)


def _looks_like_auth_failure(reason: str) -> bool:
    return any(sign in reason for sign in _AUTH_SIGNS)


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
        # 🚨 **不要對整包 result 做字串比對。** 原本這裡是
        # `"budget" in json.dumps(result)`，而 result 本來就有一個 `budget`
        # 欄位 —— 於是**每一個失敗的 job 都被標成「超出預算」**（2026-09-22
        # 第一個真的失敗的 job 才暴露出來：它跑了 2 秒、花 $0、死在認證，
        # 畫面卻叫使用者「換成 Haiku 再送一次」）。
        #
        # 分類錯不影響帳（都不計債），但它會把人導去一條錯的路，
        # 而那比只說「失敗了」更糟。所以只看**講原因的那幾個欄位**。
        reason = " ".join(
            str(result.get(k) or "") for k in ("subtype", "terminal_reason", "result")
        ).lower()

        if _looks_like_auth_failure(reason):
            # 代跑者的授權失效了。這跟委託者做了什麼完全無關，
            # 而且**每一個派給這個帳號的 job 都會這樣死**，所以要講得出來 ——
            # Hub 收到這個 kind 會把那個出借帳號停掉（routers/worker.py）。
            status, kind = "failed", "auth_failed"
        elif "budget" in reason:
            status, kind = "over_budget", "over_budget"
        else:
            status = "failed"
            # subtype 在失敗時仍然可能是字面上的 "success"（實測過），
            # 拿它當 error_kind 會在畫面上印出「success」。
            kind = result.get("terminal_reason") or "api_error"

        return base | {
            "status": status,
            "error_kind": kind,
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


async def _control_loop(state: _JobState) -> None:
    """定期空跑一次回報，把「出租者按了停止」拉回來。

    沒有這個迴圈，停止只在 job 還在吐事件時才有效 —— 而最需要停止的
    正是安靜卡住的那種。
    """
    while True:
        await asyncio.sleep(CONTROL_POLL_SECONDS)
        try:
            await state.flush(force=True)
        except httpx.HTTPError as exc:
            # 連不上 Hub 不該讓 job 中斷；下一輪再試。
            print(f"[worker] 控制回報失敗（將重試）：{exc!r}", flush=True)


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
    # 託管模型下憑證由 Hub 隨每個 job 派下來（一年期 token，走環境變數），
    # 所以這台主機上不需要憑證檔。舊模型（跑在代跑者自己機器）才需要。
    #
    # 兩個都沒有也讓它起來 —— 站台上這支是常駐的，它該能在還沒有任何人授權
    # 的時候就跑著等。真的派了一個沒帶 token 的 job，run-job.sh 會明確報錯，
    # 那比開機時拒絕啟動好：後者會讓「還沒有人授權」看起來像部署壞掉。
    if not settings.claude_credentials:
        print(
            "[worker] 沒有 CLAUDE_CREDENTIALS —— 只接 Hub 帶憑證下來的 job",
            flush=True,
        )
    if not settings.worker_token:
        sys.exit("WORKER_TOKEN 未設定。它要跟 hub 的 WORKER_SHARED_TOKEN 一致。")

    async with httpx.AsyncClient(
        base_url=settings.hub_url,
        timeout=POLL_TIMEOUT_SECONDS,
        headers={"X-Worker-Token": settings.worker_token},
    ) as client:
        name = await report_config(client)
        await warm_template_home()
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
