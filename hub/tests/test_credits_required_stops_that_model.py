"""帳號被回「要買 usage credits」之後，就不該再接那個 model 的 job。

**站台問不出來，只能跑失敗一次才知道**（2026-09-23 spike）：`GET /v1/models`、
`count_tokens`、`POST /v1/messages` 對有 Fable 與沒有 Fable 的帳號回的東西逐字
相同，都是 429 `rate_limit_error`。而有 Fable 的帳號在額度滿的時候也回 429 ——
額度滿正是這個站台存在的理由，所以 429 永遠不能讀成「沒有權限」。
唯一講得出差別的是 CLI 的 `api_error_code: credits_required`。

所以這裡是那個資訊唯一的入口。少了它，同一個帳號會用一模一樣的方式失敗到
天荒地老 —— 跟 2026-09-22 那次授權失效的沉默失敗同一種病，只是換一個原因。

跟 `test_auth_failure_stops_the_account.py` 一樣跑真的資料庫，整個測試包在一個
交易裡、結束就 rollback。連不到資料庫就跳過。
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
from app.routers.worker import _absorb_credits_required, _pick_account
from app.schemas import JobResult
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
        available_models=["sonnet", "haiku", "fable"],
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


def _job(borrower: User, account: LendingAccount, model: str) -> Job:
    return Job(
        borrower_id=borrower.id,
        source_type=SourceType.PASTE,
        prompt="…",
        model=model,
        status=JobStatus.FAILED,
        lending_id=account.lending_id,
        account_id=account.id,
    )


def _result() -> JobResult:
    return JobResult(status=JobStatus.FAILED, error_kind="credits_required")


async def test_the_model_is_marked_on_that_account(session) -> None:
    user, _setting, (account,) = await _fixtures(session)
    job = _job(user, account, "fable")
    session.add(job)
    await session.flush()

    await _absorb_credits_required(job, _result(), session)

    assert account.credits_required_models == ["fable"]


async def test_only_that_model_is_blocked(session) -> None:
    """credits 是 per-model 的。擋掉 Fable 不該動到 Sonnet 與 Haiku ——
    那會把一個「買個 credits 就好」的狀況，變成整個帳號停擺。"""
    user, setting, (account,) = await _fixtures(session)
    job = _job(user, account, "fable")
    session.add(job)
    await session.flush()

    await _absorb_credits_required(job, _result(), session)

    assert setting.runnable_models() == ["sonnet", "haiku"]
    # 接單中不動 —— 他仍然接得了其他 model 的 job（授權失效那條才會停他）。
    assert setting.accepting is True
    assert account.needs_reauth is False


async def test_only_that_account_is_blocked(session) -> None:
    """另一個帳號可能有 credits。多帳號存在的理由就是這個（SPEC §4.12）。"""
    user, setting, (first, second) = await _fixtures(session, accounts=2)
    job = _job(user, first, "fable")
    session.add(job)
    await session.flush()

    await _absorb_credits_required(job, _result(), session)

    assert first.credits_required_models == ["fable"]
    assert second.credits_required_models == []
    # 還有一個跑得動，所以他的 Fable 仍然派得出去。
    assert "fable" in setting.runnable_models()


async def test_dispatch_skips_the_marked_account(session) -> None:
    """標記了卻照樣派給它，等於每個 Fable job 都再失敗一次。"""
    user, setting, (first, second) = await _fixtures(session, accounts=2)
    job = _job(user, first, "fable")
    session.add(job)
    await session.flush()
    await _absorb_credits_required(job, _result(), session)

    picked = await _pick_account(setting, session, "fable")
    assert picked is not None and picked.id == second.id
    # 同一個帳號跑 Sonnet 完全沒問題。
    assert await _pick_account(setting, session, "sonnet") is not None


async def test_marking_is_idempotent(session) -> None:
    """同一個 model 失敗兩次不該變成兩筆 —— 畫面上會印出「fable、fable」。"""
    user, _setting, (account,) = await _fixtures(session)
    job = _job(user, account, "fable")
    session.add(job)
    await session.flush()

    await _absorb_credits_required(job, _result(), session)
    await _absorb_credits_required(job, _result(), session)

    assert account.credits_required_models == ["fable"]


async def test_other_failures_do_not_mark_anything(session) -> None:
    """只有 credits_required 這個碼算數。用訊息字串比對的話，一個剛好提到
    「credits」的 Claude 回覆就會誤傷 —— over_budget 就是這樣誤判過的。"""
    user, _setting, (account,) = await _fixtures(session)
    job = _job(user, account, "fable")
    session.add(job)
    await session.flush()

    await _absorb_credits_required(
        job, JobResult(status=JobStatus.FAILED, error_kind="auth_failed"), session
    )

    assert account.credits_required_models == []
