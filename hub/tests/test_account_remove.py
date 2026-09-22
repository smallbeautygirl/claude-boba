"""刪除 worker 的守門：**只有從來沒跑過 job 的才能刪**。

這條不是防呆，是人情債的完整性。判定若寫成「現在沒有進行中的 job」，一台跑過
50 個 job 的機器只要閒著就能被刪掉，那 50 筆的「由 X 代跑」會斷掉 —— 而那正是
債務的依據（SPEC §4.6）。

這裡跑真的資料庫，但整個測試包在一個交易裡、結束就 rollback，不留任何資料。
連不到資料庫就跳過 —— 其餘測試都不需要資料庫，不要因為這一個檔讓整套跑不動。
"""

from __future__ import annotations

import uuid

import pytest
from app.config import settings
from app.models import Job, JobStatus, SourceType, User, Worker
from app.routers.workers import delete_worker
from fastapi import HTTPException
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

# 用 anyio 的 plugin（隨 anyio 一起裝的），不是 pytest-asyncio ——
# 這個專案沒有裝後者，為了一個測試檔多一個開發相依不划算。
pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def session():
    """交易包住整個測試，最後 rollback —— 開發用的資料庫不該被測試弄髒。

    每個測試自己開一個 NullPool 的 engine：共用 `app.db.engine` 的話，它的連線池
    會留著上一個測試的事件迴圈，第二個測試就會拿到「attached to a different loop」。
    """
    eng = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        conn = await eng.connect()
    except (OperationalError, OSError) as exc:
        # 只跳過「連不到資料庫」。第一版寫成 except Exception，於是事件迴圈的
        # 錯誤被吞成 skip —— 測試看起來是綠的，其實根本沒跑到。
        await eng.dispose()
        pytest.skip(f"連不到資料庫：{exc}")
    trans = await conn.begin()
    async with AsyncSession(bind=conn, expire_on_commit=False) as s:
        yield s
    await trans.rollback()
    await conn.close()
    await eng.dispose()


async def _fixtures(session: AsyncSession) -> tuple[User, Worker]:
    user = User(
        observ_user_id=uuid.uuid4().int % 2_000_000_000,
        email=f"{uuid.uuid4()}@example.com",
        display_name="出租者",
    )
    session.add(user)
    await session.flush()
    worker = Worker(owner_user_id=user.id, name="我的電腦", token=str(uuid.uuid4()))
    session.add(worker)
    await session.flush()
    return user, worker


def _job(borrower: User, worker: Worker | None, requested: Worker | None) -> Job:
    return Job(
        borrower_id=borrower.id,
        source_type=SourceType.PASTE,
        prompt="…",
        status=JobStatus.SUCCEEDED if worker else JobStatus.QUEUED,
        worker_id=worker.id if worker else None,
        requested_worker_id=requested.id if requested else None,
    )


async def test_never_used_worker_is_deleted(session) -> None:
    user, worker = await _fixtures(session)
    await delete_worker(worker.id, user=user, session=session)
    assert await session.get(Worker, worker.id) is None


async def test_worker_that_ran_jobs_is_refused_with_the_count(session) -> None:
    user, worker = await _fixtures(session)
    for _ in range(3):
        session.add(_job(user, worker, None))
    await session.flush()

    with pytest.raises(HTTPException) as exc:
        await delete_worker(worker.id, user=user, session=session)

    assert exc.value.status_code == 409
    # 數字要在訊息裡：只說「不能刪」的話，使用者不知道自己少做了什麼才能刪。
    assert "3" in exc.value.detail
    assert await session.get(Worker, worker.id) is not None


async def test_queued_job_that_named_this_worker_also_blocks(session) -> None:
    """`requested_worker_id` 也要擋 —— 那筆 job 還沒被領，`worker_id` 還是 NULL，
    只看 `worker_id` 的話會刪掉一台正被指名的機器，讓那筆 job 指向不存在的 worker。
    """
    user, worker = await _fixtures(session)
    session.add(_job(user, None, worker))
    await session.flush()

    with pytest.raises(HTTPException) as exc:
        await delete_worker(worker.id, user=user, session=session)
    assert exc.value.status_code == 409
    assert "排隊" in exc.value.detail


async def test_someone_elses_worker_is_not_deletable(session) -> None:
    _owner, worker = await _fixtures(session)
    other = User(
        observ_user_id=uuid.uuid4().int % 2_000_000_000,
        email=f"{uuid.uuid4()}@example.com",
        display_name="別人",
    )
    session.add(other)
    await session.flush()

    with pytest.raises(HTTPException) as exc:
        await delete_worker(worker.id, user=other, session=session)
    assert exc.value.status_code == 403
    assert await session.get(Worker, worker.id) is not None
