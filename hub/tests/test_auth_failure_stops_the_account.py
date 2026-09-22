"""授權失效的帳號要自己停下來（2026-09-22 真的發生過一次）。

**失效是沉默的。** 代跑者的 token 死掉之後，站台仍然認為那個帳號可用、仍然把
job 派給它，而每一個都會用一模一樣的方式失敗 —— 畫面上它還是綠的，所以沒有人
會發現，直到有人抱怨「怎麼都跑不起來」。

這裡跑真的資料庫，因為要驗的正是「有沒有寫進去」。整個測試包在一個交易裡、
結束就 rollback，不留任何資料。連不到資料庫就跳過。
"""

from __future__ import annotations

import uuid

import pytest
from app.config import settings
from app.models import (
    Job,
    JobStatus,
    LendingAccount,
    LendingSetting,
    SourceType,
    User,
)
from app.routers.worker import _absorb_auth_failure
from app.schemas import JobResult
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


async def _result(kind: str) -> JobResult:
    return JobResult(status=JobStatus.FAILED, error_kind=kind)


async def test_auth_failure_stops_that_account(session) -> None:
    user, setting, (account,) = await _fixtures(session)
    job = _job(user, account)
    session.add(job)
    await session.flush()

    await _absorb_auth_failure(job, await _result("auth_failed"), session)

    assert account.needs_reauth is True
    # 沒有能跑的帳號了還掛著「接單中」，job 會排隊等一個不會來的人。
    assert setting.accepting is False


async def test_other_accounts_keep_accepting(session) -> None:
    """只停**那一個**帳號 —— 這正是多帳號存在的理由（SPEC §4.12）。"""
    user, setting, (first, second) = await _fixtures(session, accounts=2)
    job = _job(user, first)
    session.add(job)
    await session.flush()

    await _absorb_auth_failure(job, await _result("auth_failed"), session)

    assert first.needs_reauth is True
    assert second.needs_reauth is False
    assert setting.accepting is True


async def test_other_failures_do_not_touch_the_account(session) -> None:
    """跑不出東西跟授權失效是兩件事。把帳號停掉的門檻要高。"""
    user, setting, (account,) = await _fixtures(session)
    job = _job(user, account)
    session.add(job)
    await session.flush()

    await _absorb_auth_failure(job, await _result("api_error"), session)

    assert account.needs_reauth is False
    assert setting.accepting is True
