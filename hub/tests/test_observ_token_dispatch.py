"""帶 Observ token 的 job：只派給開外網的代跑者，交出去就清掉，排到過期也清掉。

2026-09-24。「查 Observ 事件」要連 Observ 與 middleware。派給關了外網的代跑者，容器裡
連不到、花了額度才失敗，而錯誤長得像「指令壞了」—— 所以收窄在兩層：提交時
（`_check_network`）與領單時（`_claim`）。token 本身只在排隊那段存在 hub 上。

跟 test_queue_dead_ends 一樣跑真的資料庫，包在一個交易裡、結束就 rollback。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from app import secrets_box
from app.config import settings
from app.expiry import sweep_once
from app.models import (
    Job,
    JobStatus,
    LendingAccount,
    LendingSetting,
    SourceType,
    User,
)
from app.routers.jobs import _check_network
from app.routers.worker import _claim
from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def key():
    old = settings.token_encryption_key
    settings.token_encryption_key = secrets_box.generate_key()
    yield
    settings.token_encryption_key = old


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
        # 開發資料庫裡有真的代跑者與排隊中的 job。這幾個測試斷言的是「站台上**只有**
        # 我建的那幾位」，所以先在這個（最後會 rollback 的）交易裡把既有的收起來。
        await s.execute(update(LendingSetting).values(accepting=False))
        await s.execute(
            update(Job)
            .where(Job.status == JobStatus.QUEUED)
            .values(status=JobStatus.EXPIRED)
        )
        yield s
    await trans.rollback()
    await conn.close()
    await eng.dispose()


async def _user(session: AsyncSession, name: str) -> User:
    user = User(
        observ_user_id=uuid.uuid4().int % 2_000_000_000,
        email=f"{uuid.uuid4()}@example.com",
        display_name=name,
    )
    session.add(user)
    await session.flush()
    return user


async def _lender(session: AsyncSession, *, network: bool) -> LendingSetting:
    owner = await _user(session, "代跑者")
    setting = LendingSetting(
        owner_user_id=owner.id,
        available_models=["sonnet", "haiku"],
        accepting=True,
        allow_full_network=network,
    )
    session.add(setting)
    await session.flush()
    session.add(
        LendingAccount(lending_id=setting.id, name="帳號", oauth_token_enc=b"x")
    )
    await session.flush()
    await session.refresh(setting, ["accounts"])
    return setting


async def _job(session: AsyncSession, borrower: User, *, token: bool) -> Job:
    job = Job(
        borrower_id=borrower.id,
        prompt="/observ-event-lookup 1111954" if token else "/pptx 做簡報",
        model="sonnet",
        source_type=SourceType.PASTE,
        status=JobStatus.QUEUED,
        observ_token_enc=secrets_box.seal("jwt") if token else None,
    )
    session.add(job)
    await session.flush()
    return job


HOST = SimpleNamespace(max_concurrency=99)


# --- 提交時 -----------------------------------------------------------------------


async def test_submit_is_refused_when_only_walled_lenders_are_online(session) -> None:
    await _lender(session, network=False)
    me = await _user(session, "委託者")
    with pytest.raises(HTTPException) as exc:
        await _check_network(None, me.id, session)
    assert exc.value.status_code == 400
    assert "外網" in exc.value.detail


async def test_submit_passes_when_someone_open_is_online(session) -> None:
    await _lender(session, network=False)
    await _lender(session, network=True)
    me = await _user(session, "委託者")
    await _check_network(None, me.id, session)


async def test_naming_a_walled_lender_is_refused_with_a_way_out(session) -> None:
    walled = await _lender(session, network=False)
    me = await _user(session, "委託者")
    with pytest.raises(HTTPException) as exc:
        await _check_network(walled.id, me.id, session)
    assert "自動" in exc.value.detail


# --- 領單時 -----------------------------------------------------------------------


async def test_claim_skips_walled_lenders_for_token_jobs(session) -> None:
    """自動派單：唯一在線的人關了外網 → 帶 token 的 job 留在隊列，不派。"""
    await _lender(session, network=False)
    me = await _user(session, "委託者")
    job = await _job(session, me, token=True)

    assert await _claim(HOST, session) is None
    await session.refresh(job)
    assert job.status == JobStatus.QUEUED


async def test_claim_picks_the_open_lender(session) -> None:
    await _lender(session, network=False)
    open_one = await _lender(session, network=True)
    me = await _user(session, "委託者")
    job = await _job(session, me, token=True)

    claimed = await _claim(HOST, session)
    assert claimed is not None
    picked_job, setting, _account = claimed
    assert picked_job.id == job.id
    assert setting.id == open_one.id
    # 清掉 token 是 poll 的事（要先過 credentials.access_token 那關）；_claim 不碰它。
    assert picked_job.observ_token_enc is not None


async def test_jobs_without_a_token_still_go_to_walled_lenders(session) -> None:
    """收窄只針對帶 token 的 job。關了外網的人照常接一般 job。"""
    await _lender(session, network=False)
    me = await _user(session, "委託者")
    job = await _job(session, me, token=False)

    claimed = await _claim(HOST, session)
    assert claimed is not None
    assert claimed[0].id == job.id


# --- 排到過期 -------------------------------------------------------------------


async def test_expiry_wipes_the_token(session, monkeypatch) -> None:
    me = await _user(session, "委託者")
    job = await _job(session, me, token=True)
    job.created_at = datetime.now(UTC) - timedelta(
        seconds=settings.job_queue_expiry_seconds + 60
    )
    await session.flush()

    from app import expiry

    monkeypatch.setattr(expiry.notify, "job_finished", lambda *a, **k: None)
    await sweep_once(session)
    await session.refresh(job)
    assert job.status == JobStatus.EXPIRED
    assert job.observ_token_enc is None
