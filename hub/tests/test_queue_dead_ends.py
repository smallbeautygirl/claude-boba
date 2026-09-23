"""派不出去的 job 要自己作廢，而死路要在提交時就擋下來。

2026-09-22 的真實情境：唯一的出借帳號授權失效，使用者送了一個 job，然後那個 job
停在「排隊中」—— 沒有錯誤、沒有期限，也沒有任何東西會來救它。

兩層都要有，因為它們擋的是不同的時間點：
  提交時  —— 送出當下就沒有人跑得動（死路，不是等待）
  過期    —— 送出**之後**才失效，提交時擋不到

這裡跑真的資料庫，整個測試包在一個交易裡、結束就 rollback。連不到就跳過。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
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
from app.routers.jobs import _check_model
from fastapi import HTTPException
from sqlalchemy import select
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


async def _fixtures(
    session: AsyncSession, *, accounts: int = 1
) -> tuple[User, LendingSetting, list[LendingAccount]]:
    user = User(
        observ_user_id=uuid.uuid4().int % 2_000_000_000,
        email=f"{uuid.uuid4()}@example.com",
        display_name="代跑者",
    )
    session.add(user)
    await session.flush()
    setting = LendingSetting(
        owner_user_id=user.id,
        available_models=["sonnet", "haiku"],
        accepting=True,
    )
    session.add(setting)
    await session.flush()
    rows = [
        LendingAccount(
            lending_id=setting.id, name=f"帳號 {i + 1}", oauth_token_enc=b"x"
        )
        for i in range(accounts)
    ]
    session.add_all(rows)
    await session.flush()
    await session.refresh(setting, ["accounts"])
    return user, setting, rows


def _job(borrower: User, account: LendingAccount) -> Job:
    return Job(
        borrower_id=borrower.id,
        source_type=SourceType.PASTE,
        prompt="…",
        status=JobStatus.SUCCEEDED,
        lending_id=account.lending_id,
        account_id=account.id,
    )


async def _queued(session, user, *, minutes_ago: int) -> Job:
    job = Job(
        borrower_id=user.id,
        source_type=SourceType.PASTE,
        prompt="…",
        status=JobStatus.QUEUED,
        created_at=datetime.now(UTC) - timedelta(minutes=minutes_ago),
    )
    session.add(job)
    await session.flush()
    return job


# --- 過期 -----------------------------------------------------------------


async def test_a_job_that_nobody_took_expires(session) -> None:
    user, _setting, _accounts = await _fixtures(session)
    job = await _queued(session, user, minutes_ago=30)

    assert await sweep_once(session) == 1
    assert job.status is JobStatus.EXPIRED
    # 有結束時間才算真的結案 —— 沒有的話畫面會顯示「已執行 30 分鐘」。
    assert job.finished_at is not None


async def test_a_job_still_within_the_window_is_left_alone(session) -> None:
    """15 分鐘的門檻是給代跑者開機的緩衝，不是急著結案。"""
    user, _setting, _accounts = await _fixtures(session)
    job = await _queued(session, user, minutes_ago=1)

    await sweep_once(session)
    assert job.status is JobStatus.QUEUED


async def test_claimed_jobs_are_not_swept(session) -> None:
    """已經被領走的有 worker 在照顧它 —— 卡住的判定在管理頁，不該被這裡搶著結案。"""
    user, _setting, (account,) = await _fixtures(session)
    job = await _queued(session, user, minutes_ago=30)
    job.status = JobStatus.CLAIMED
    job.account_id = account.id
    await session.flush()

    await sweep_once(session)
    assert job.status is JobStatus.CLAIMED


# --- 提交時的死路 ---------------------------------------------------------


async def _make_the_whole_site_dead(session) -> None:
    """把**開發資料庫裡既有的**設定與帳號也一起弄成「沒有人跑得動」。

    `_check_model()` 問的是全站，而 fixture 只控制得了自己建的那一份 ——
    不處理既有資料的話，這個測試會在「開發機上剛好有人在接單」時變紅，
    而那跟它要驗的規則無關。這已經發生過兩次，第一次只處理了帳號、
    漏了設定，所以這裡兩個都要。整個測試包在交易裡，結束就 rollback。
    """
    for setting in await session.scalars(select(LendingSetting)):
        setting.accepting = False
    for row in await session.scalars(select(LendingAccount)):
        row.needs_reauth = True
    await session.flush()


async def test_submitting_is_refused_when_every_account_is_dead(session) -> None:
    """所有 token 都失效時排隊只是把失敗延後 15 分鐘，
    而使用者會以為自己在等一個會來的人。"""
    _user, _setting, _accounts = await _fixtures(session)
    await _make_the_whole_site_dead(session)

    with pytest.raises(HTTPException) as exc:
        await _check_model("sonnet", None, uuid.uuid4(), session)
    assert exc.value.status_code == 400
    assert "授權" in exc.value.detail


async def test_submitting_is_allowed_when_someone_is_merely_offline(session) -> None:
    """代跑者只是暫停接單／關機的時候要放行 —— 他回來 job 就跑了。

    半夜送一個、早上有人開機才跑，是這個工具該支援的用法。
    """
    _user, setting, (account,) = await _fixtures(session)
    setting.accepting = False  # 暫停接單
    account.needs_reauth = False  # 但憑證還活著
    await session.flush()

    await _check_model("sonnet", None, uuid.uuid4(), session)  # 不拋就是過
    assert account.usable
