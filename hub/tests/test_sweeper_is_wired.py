"""授權清潔工**真的有被啟動**。

`sweeper()` 寫好了、`sweep()` 也有測試，但 2026-09-22 發現它從來沒有被呼叫過 ——
`main.py` 連 import 都沒有 import `authorize`。八個生命週期測試全綠，因為它們測的是
同步的 `sweep()`，不是那個迴圈。

**測試呼叫得到內層函式，不代表外層有人啟動它。** 所以這裡驗的是 wiring：
真的跑一次 app 的 lifespan，去 `asyncio.all_tasks()` 裡找那個 task。
"""

from __future__ import annotations

import asyncio

import pytest
from app import authorize, main

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def _no_external_checks(monkeypatch):
    """把啟動時的三個外部檢查換掉 —— 這個檔案要驗的是 wiring，不是 DB 與 MinIO。"""

    async def _ok() -> None:
        return None

    monkeypatch.setattr(main, "_check_migrations", _ok)
    monkeypatch.setattr(main, "check_configured", lambda: None)
    monkeypatch.setattr(main, "ensure_bucket", lambda: None)


def _sweeper_tasks() -> list[asyncio.Task]:
    return [t for t in asyncio.all_tasks() if t.get_name() == "authorize-sweeper"]


async def test_app_startup_actually_creates_the_task(_no_external_checks) -> None:
    async with main.lifespan(main.app):
        tasks = _sweeper_tasks()
        assert tasks, "app 起來了，但沒有 authorize-sweeper 這個 task"
        assert not tasks[0].done()
        task = tasks[0]
    # 關掉時要收乾淨，不然重啟會留下孤兒 task。
    assert task.cancelled() or task.done()
    assert not _sweeper_tasks()


async def test_the_loop_really_calls_sweep(monkeypatch) -> None:
    """task 存在還不夠 —— 迴圈裡真的要有人在掃。"""
    calls = 0

    def counting_sweep() -> int:
        nonlocal calls
        calls += 1
        return 0

    monkeypatch.setattr(authorize, "sweep", counting_sweep)
    task = asyncio.create_task(authorize.sweeper(interval=0.01))
    await asyncio.sleep(0.05)
    task.cancel()
    assert calls >= 2, f"清潔工跑了 {calls} 圈，迴圈沒有在掃"
