"""移除出借帳號的守門：**跑過 job 的帳號不會消失，只會被撤掉 token**。

這條不是防呆，是人情債的完整性。判定若寫成「現在沒有進行中的 job」，一個跑過
50 個 job 的帳號只要閒著就能被刪掉，那 50 筆的「由 X 代跑」會斷掉 —— 而那正是
債務的依據（SPEC §4.6）。

2026-09-22（SPEC §4.12）：舊版對跑過 job 的 worker 直接回 409，並在註解裡承認
「跑過 job 的機器目前沒有辦法從清單移除，這是已知的洞」。多帳號之後那個洞會被
踩到 —— 換公司帳號、token 被收回都需要一個「我不借這個帳號了」的動作。
所以改成撤 token：帳號留著（紀錄不斷），但它不再接單。

這裡跑真的資料庫，但整個測試包在一個交易裡、結束就 rollback，不留任何資料。
連不到資料庫就跳過 —— 其餘測試都不需要資料庫，不要因為這一個檔讓整套跑不動。
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
from app.routers.workers import remove_account
from fastapi import HTTPException
from sqlalchemy import func, select
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


async def test_never_used_account_is_deleted(session) -> None:
    user, _setting, (account,) = await _fixtures(session)
    await remove_account(account.id, user=user, session=session)
    assert await session.get(LendingAccount, account.id) is None


async def test_account_that_ran_jobs_keeps_its_record(session) -> None:
    """跑過 job 的帳號留著，但 token 撤掉 —— 那 3 筆 job 的「由誰代跑」不能斷。"""
    user, _setting, (account,) = await _fixtures(session)
    for _ in range(3):
        session.add(_job(user, account))
    await session.flush()

    await remove_account(account.id, user=user, session=session)

    kept = await session.get(LendingAccount, account.id)
    assert kept is not None
    assert kept.oauth_token_enc is None
    assert not kept.usable
    # job 仍然指得到它 —— 這才是不刪的理由。
    ran = await session.scalar(
        select(func.count()).select_from(Job).where(Job.account_id == account.id)
    )
    assert ran == 3


async def test_removing_the_last_usable_account_stops_accepting(session) -> None:
    """沒有帳號還掛著「接單中」，job 會排隊等一個不會來的人。"""
    user, setting, (account,) = await _fixtures(session)
    await remove_account(account.id, user=user, session=session)
    await session.refresh(setting)
    assert setting.accepting is False


async def test_other_accounts_keep_running(session) -> None:
    """移除一個帳號不影響另一個 —— 這正是多帳號存在的理由（SPEC §4.12）。"""
    user, setting, (first, second) = await _fixtures(session, accounts=2)
    await remove_account(first.id, user=user, session=session)
    await session.refresh(setting)
    assert setting.accepting is True
    assert (await session.get(LendingAccount, second.id)).usable


async def test_someone_elses_account_is_not_removable(session) -> None:
    _owner, _setting, (account,) = await _fixtures(session)
    other = User(
        observ_user_id=uuid.uuid4().int % 2_000_000_000,
        email=f"{uuid.uuid4()}@example.com",
        display_name="別人",
    )
    session.add(other)
    await session.flush()

    with pytest.raises(HTTPException) as exc:
        await remove_account(account.id, user=other, session=session)
    # 別人的帳號在他自己的出借設定裡根本不存在 —— 404 而不是 403，
    # 因為「這個 id 是誰的」本身就不該回答。
    assert exc.value.status_code == 404
    assert await session.get(LendingAccount, account.id) is not None
