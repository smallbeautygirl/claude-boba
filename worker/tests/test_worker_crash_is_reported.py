"""worker 自己在 job 中途出錯，job 不能變成孤兒。

2026-09-23 的實例：Claude `Read` 一份 300 KB 的 HTML，stream-json 那一行超過
asyncio readline 的 64 KiB 預設上限，`_pump` 丟 ValueError。主迴圈接住、印一行、
繼續領單 —— 但 hub 從沒收到 result（畫面永遠「執行中」）、產出沒上傳、工作目錄
留在磁碟上。這裡鎖住兩件事：那一行讀得下，以及就算讀不下也要把 job 收乾淨。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import worker
from worker import STREAM_LINE_LIMIT, _pump, _report_crash


class _State:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.stderr_tail: list[str] = []

    async def add(self, event: dict) -> None:
        self.events.append(event)


async def _pump_one_line(limit: int, line: bytes, state: _State) -> None:
    # StreamReader 要在事件迴圈裡建，否則 3.12 起會有 DeprecationWarning。
    reader = asyncio.StreamReader(limit=limit)
    reader.feed_data(line)
    reader.feed_eof()
    await _pump(reader, state)


_BIG_EVENT = (
    json.dumps({"type": "user", "tool_result": "<html>" + "x" * 300_000}).encode()
    + b"\n"
)


def test_a_300kb_tool_result_line_is_read_with_our_limit() -> None:
    state = _State()
    asyncio.run(_pump_one_line(STREAM_LINE_LIMIT, _BIG_EVENT, state))
    assert len(state.events) == 1
    assert state.events[0]["type"] == "user"


def test_the_default_limit_is_the_bug() -> None:
    """記錄原本為什麼會死，免得有人把 limit 拿掉。"""
    state = _State()
    with pytest.raises(ValueError, match="exceed the limit|longer than limit"):
        asyncio.run(_pump_one_line(2**16, _BIG_EVENT, state))


class _Response:
    status_code = 200

    def raise_for_status(self) -> None:
        pass


class _Client:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict]] = []

    async def post(self, url: str, json: dict) -> _Response:
        self.posts.append((url, json))
        return _Response()


def test_crash_reports_failed_and_removes_the_workdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker.settings, "job_root", tmp_path)
    killed: list[str] = []

    async def fake_kill(job_id: str) -> None:
        killed.append(job_id)

    monkeypatch.setattr(worker, "_kill_container", fake_kill)
    workdir = tmp_path / "job-1"
    (workdir / ".home").mkdir(parents=True)
    (workdir / "產出.pdf").write_bytes(b"%PDF")

    client = _Client()
    asyncio.run(_report_crash(client, "job-1", ValueError("Separator is not found")))

    assert killed == ["job-1"]
    ((url, payload),) = client.posts
    assert url == "/api/worker/jobs/job-1/result"
    assert payload["status"] == "failed"
    assert payload["error_kind"] == "worker_error"  # hub 歸為系統問題，不計債
    assert "Separator is not found" in payload["error_detail"]
    assert not workdir.exists()


def test_crash_report_survives_the_hub_being_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """hub 不在時不能再丟一個例外出來 —— 那會把主迴圈帶走。"""
    import httpx

    monkeypatch.setattr(worker.settings, "job_root", tmp_path)

    async def fake_kill(job_id: str) -> None:
        pass

    monkeypatch.setattr(worker, "_kill_container", fake_kill)

    class _Down:
        async def post(self, url: str, json: dict) -> _Response:
            raise httpx.ConnectError("All connection attempts failed")

    (tmp_path / "job-2").mkdir()
    asyncio.run(_report_crash(_Down(), "job-2", ValueError("boom")))
    assert not (tmp_path / "job-2").exists()
