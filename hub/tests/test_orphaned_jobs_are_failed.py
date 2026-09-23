"""worker 死掉之後，它的 job 要被 hub 結案，不能永遠「執行中」。

2026-09-23 的真實情境：worker 讀輸出撞到 64 KiB 行上限、例外拋出 run_job，result
從沒送到 hub。job ceba7702 開跑 31 分鐘後 finished_at 仍是 NULL，畫面上的秒數
一直走。hub 那時對已派出的 job 沒有任何自己的判斷。

跟 test_queue_dead_ends 一樣跑真的資料庫、包在交易裡、結束 rollback，連不到就跳過。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.config import settings
from app.models import Job, JobStatus, LendingAccount, LendingSetting, SourceType, User
from app.orphans import ERROR_KIND, sweep_once
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def session():
    eng = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        conn = await eng.connect()
    except (OperationalError, OSError) as exc:
        await eng.dispose()
        pytest.skip(f"連不到資料庫：{exc}")
    trans = await conn.begin()
    async with AsyncSession(bind=conn, expire_on_commit=False) as s:
        await _keep_existing_jobs_alive(s)
        yield s
    await trans.rollback()
    await conn.close()
    await eng.dispose()


async def _keep_existing_jobs_alive(session: AsyncSession) -> None:
    """開發資料庫裡可能真的有孤兒 job（這個測試就是為了它寫的）。

    `sweep_once()` 問的是全表，fixture 只控制得了自己建的那一筆 —— 不處理既有
    資料的話，回傳的數量會多一、「不該動的」測試會掃到別人。理由與
    test_queue_dead_ends 的 `_make_the_whole_site_dead` 相同。整個測試包在交易裡，
    結束就 rollback，真正的孤兒留給正式的清潔工。
    """
    now = datetime.now(UTC)
    for job in await session.scalars(
        select(Job).where(Job.status.in_([JobStatus.CLAIMED, JobStatus.RUNNING]))
    ):
        job.last_heartbeat_at = now
    await session.flush()


@pytest.fixture
def quiet_notify(monkeypatch):
    """結案會通知委託者；這裡不要真的打 Teams。"""
    from app import orphans

    sent: list[tuple[object, str]] = []
    monkeypatch.setattr(
        orphans.notify, "job_finished", lambda u, j, st, c: sent.append((j, st))
    )
    return sent


async def _dispatched(session, *, status: JobStatus, heartbeat_ago: int | None) -> Job:
    user = User(
        observ_user_id=uuid.uuid4().int % 2_000_000_000,
        email=f"{uuid.uuid4()}@example.com",
        display_name="委託者",
    )
    session.add(user)
    await session.flush()
    setting = LendingSetting(
        owner_user_id=user.id, available_models=["sonnet"], accepting=True
    )
    session.add(setting)
    await session.flush()
    account = LendingAccount(lending_id=setting.id, name="帳號", oauth_token_enc=b"x")
    session.add(account)
    await session.flush()

    started = datetime.now(UTC) - timedelta(minutes=40)
    job = Job(
        borrower_id=user.id,
        source_type=SourceType.PASTE,
        prompt="…",
        status=status,
        lending_id=setting.id,
        account_id=account.id,
        claimed_at=started,
        started_at=started if status is JobStatus.RUNNING else None,
        last_heartbeat_at=(
            datetime.now(UTC) - timedelta(seconds=heartbeat_ago)
            if heartbeat_ago is not None
            else None
        ),
    )
    session.add(job)
    await session.flush()
    return job


async def test_a_running_job_whose_worker_went_silent_is_failed(
    session, quiet_notify
) -> None:
    job = await _dispatched(session, status=JobStatus.RUNNING, heartbeat_ago=1800)

    assert await sweep_once(session) == 1
    assert job.status is JobStatus.FAILED
    assert job.error_kind == ERROR_KIND  # failures.py：系統問題、不計債
    # 有結束時間才算真的結案 —— 沒有的話畫面會顯示「已執行 30 分鐘」。
    assert job.finished_at is not None
    assert "沒有回報" in (job.error_detail or "")
    assert quiet_notify == [(job.id, "failed")]


async def test_a_job_that_is_still_heartbeating_is_left_alone(
    session, quiet_notify
) -> None:
    """跑了 40 分鐘但 5 秒前還有心跳 —— 那是「跑很久」，不是「死了」。"""
    job = await _dispatched(session, status=JobStatus.RUNNING, heartbeat_ago=5)

    assert await sweep_once(session) == 0
    assert job.status is JobStatus.RUNNING
    assert quiet_notify == []


async def test_a_job_from_before_the_column_existed_falls_back_to_started_at(
    session, quiet_notify
) -> None:
    """migration 不回填。既有的執行中 job 心跳是 NULL，要用開始時間判。"""
    job = await _dispatched(session, status=JobStatus.RUNNING, heartbeat_ago=None)

    assert await sweep_once(session) == 1
    assert job.status is JobStatus.FAILED


async def test_a_claimed_job_that_never_started_is_also_swept(
    session, quiet_notify
) -> None:
    """領了單、還沒開始就死掉（例如下載附件時 worker 被關）—— 一樣是孤兒。"""
    job = await _dispatched(session, status=JobStatus.CLAIMED, heartbeat_ago=None)

    assert await sweep_once(session) == 1
    assert job.status is JobStatus.FAILED


async def test_finished_jobs_are_never_touched(session, quiet_notify) -> None:
    job = await _dispatched(session, status=JobStatus.RUNNING, heartbeat_ago=1800)
    job.status = JobStatus.SUCCEEDED
    await session.flush()

    assert await sweep_once(session) == 0
    assert job.status is JobStatus.SUCCEEDED
