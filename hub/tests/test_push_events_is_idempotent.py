"""worker 重送同一批事件，hub 要當作已收到，不是 500。

2026-09-23 正式站：job 跑到一半 hub 被重新部署，worker 的 POST /events 已被舊的
hub 收下並 commit，但回應沒送回來（Server disconnected）。worker 接著再送一次
同一批（from_seq 沒前進），新的 hub 撞到 ix_job_events_job_seq 的唯一鍵、回 500，
worker 把它記成 worker_error、job 失敗 —— 而 Claude 那邊其實跑得好好的。

seq 本來就是為了「去重與續傳」放進協定的（schemas.EventBatch）。已存在的 seq
要略過、回下一個該送的 seq，讓 worker 對齊；不能炸。

跑真的資料庫，交易包住、結束 rollback。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from app.config import settings
from app.models import (
    Job,
    JobEvent,
    JobStatus,
    LendingAccount,
    LendingSetting,
    SourceType,
    User,
)
from app.routers.worker import push_events
from app.schemas import EventBatch
from sqlalchemy import func, select
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
        yield s
    await trans.rollback()
    await conn.close()
    await eng.dispose()


async def _dispatched_job(session: AsyncSession) -> Job:
    user = User(
        observ_user_id=uuid.uuid4().int % 2_000_000_000,
        email=f"{uuid.uuid4()}@example.com",
        display_name="代跑者",
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
    job = Job(
        borrower_id=user.id,
        source_type=SourceType.PASTE,
        prompt="…",
        model="sonnet",
        status=JobStatus.CLAIMED,
        lending_id=setting.id,
        account_id=account.id,
    )
    session.add(job)
    await session.flush()
    return job


async def _stored(session: AsyncSession, job_id: uuid.UUID) -> tuple[int, int]:
    n, mx = (
        await session.execute(
            select(func.count(), func.max(JobEvent.seq)).where(
                JobEvent.job_id == job_id
            )
        )
    ).one()
    return n, mx


async def test_resending_the_same_batch_is_acknowledged_not_500(session) -> None:
    job = await _dispatched_job(session)
    batch = EventBatch(
        from_seq=1,
        events=[{"type": "system"}, {"type": "assistant"}, {"type": "assistant"}],
    )
    host = SimpleNamespace()

    first = await push_events(job.id, batch, host=host, session=session)
    assert first["next_seq"] == 4
    assert await _stored(session, job.id) == (3, 3)

    # 同一批再來一次（回應丟了、worker 重送）。以前這裡是 IntegrityError → 500。
    second = await push_events(job.id, batch, host=host, session=session)
    assert second["next_seq"] == 4, "重送要回同一個 next_seq，worker 才對得齊"
    assert await _stored(session, job.id) == (3, 3), "不能多存一份"


async def test_a_batch_that_overlaps_the_tail_only_appends_the_new_part(
    session,
) -> None:
    """部分重疊：worker 重送 2..4，而 2、3 已經在 —— 只補 4。"""
    job = await _dispatched_job(session)
    host = SimpleNamespace()
    await push_events(
        job.id,
        EventBatch(from_seq=1, events=[{"a": 1}, {"a": 2}, {"a": 3}]),
        host=host,
        session=session,
    )
    got = await push_events(
        job.id,
        EventBatch(from_seq=2, events=[{"a": 2}, {"a": 3}, {"a": 4}]),
        host=host,
        session=session,
    )
    assert got["next_seq"] == 5
    assert await _stored(session, job.id) == (4, 4)
