"""兩個清潔工**真的有被啟動**。

`sweeper()` 寫好了、`sweep()` 也有測試，但 2026-09-22 發現它從來沒有被呼叫過 ——
`main.py` 連 import 都沒有 import `authorize`。八個生命週期測試全綠，因為它們測的是
同步的 `sweep()`，不是那個迴圈。

**測試呼叫得到內層函式，不代表外層有人啟動它。** 所以這裡驗的是 wiring：
真的跑一次 app 的 lifespan，去 `asyncio.all_tasks()` 裡找那些 task。

2026-09-22 又踩了一次同一個坑，這次是 job 的過期機制：`expired` 狀態、它的文案、
`job_queue_expiry_seconds` 都存在，四個地方的註解都在描述它 —— 但沒有任何程式會
去設那個狀態，所以 queued 的 job 會永遠排隊。所以這個檔案現在守兩個清潔工，
**新增背景迴圈時要在這裡加一條**。
"""

from __future__ import annotations

import asyncio

import pytest
from app import authorize, expiry, main, orphans

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


_NAMES = ("authorize-sweeper", "job-expiry-sweeper", "job-orphan-sweeper")


def _tasks(name: str) -> list[asyncio.Task]:
    return [t for t in asyncio.all_tasks() if t.get_name() == name]


@pytest.mark.parametrize("name", _NAMES)
async def test_app_startup_actually_creates_the_task(
    name: str, _no_external_checks
) -> None:
    async with main.lifespan(main.app):
        tasks = _tasks(name)
        assert tasks, f"app 起來了，但沒有 {name} 這個 task"
        assert not tasks[0].done()
        task = tasks[0]
    # 關掉時要收乾淨，不然重啟會留下孤兒 task。
    assert task.cancelled() or task.done()
    assert not _tasks(name)


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


async def test_the_expiry_loop_really_sweeps(monkeypatch) -> None:
    """task 存在還不夠 —— 迴圈裡真的要有人在掃。

    連 `SessionLocal` 一起換掉：這裡驗的是迴圈，不是資料庫。
    """
    calls = 0

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

    async def counting_sweep(_session) -> int:
        nonlocal calls
        calls += 1
        return 0

    monkeypatch.setattr(expiry, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(expiry, "sweep_once", counting_sweep)
    task = asyncio.create_task(expiry.sweeper(interval=0.01))
    await asyncio.sleep(0.05)
    task.cancel()
    assert calls >= 2, f"清潔工跑了 {calls} 圈，迴圈沒有在掃"


async def test_the_expiry_loop_survives_a_failure(monkeypatch) -> None:
    """一次例外不該讓迴圈死掉 —— 它死了之後沒有任何症狀，
    只會讓 job 再度變成永遠排隊，而那正是這次要修的東西。"""
    calls = 0

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

    async def exploding_sweep(_session) -> int:
        nonlocal calls
        calls += 1
        raise RuntimeError("資料庫剛好斷線")

    monkeypatch.setattr(expiry, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(expiry, "sweep_once", exploding_sweep)
    task = asyncio.create_task(expiry.sweeper(interval=0.01))
    await asyncio.sleep(0.05)
    assert not task.done(), "掃到一半炸了就整個停掉了"
    task.cancel()
    assert calls >= 2


async def test_the_orphan_loop_waits_out_the_warmup_then_sweeps(monkeypatch) -> None:
    """hub 剛起來時所有心跳都過期了（重啟那 60 秒 worker 打不進來）——
    開機就掃會把每個健康的 job 誤判成孤兒，所以要先等一個 timeout。"""
    calls = 0

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

    async def counting_sweep(_session) -> int:
        nonlocal calls
        calls += 1
        return 0

    monkeypatch.setattr(orphans, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(orphans, "sweep_once", counting_sweep)
    task = asyncio.create_task(orphans.sweeper(interval=0.01, warmup=0.05))
    await asyncio.sleep(0.03)
    assert calls == 0, "暖機期間就開掃了"
    await asyncio.sleep(0.07)
    task.cancel()
    assert calls >= 2, f"暖機結束後清潔工跑了 {calls} 圈，迴圈沒有在掃"
