"""`/me` 上的兩個畫面旗標：`has_run_a_job` 與 `is_lender`（web-spec §13）。

它們**不是權限**，是「這個人現在處於什麼狀態」—— 提交頁那段第一次來的介紹、
以及「換你了」那塊招募，都靠它們決定要不要出現。

釘住的是兩條容易被「順手改進」掉的定義：

1. **`is_lender` 的判準是有沒有至少一個出借帳號，不是有沒有出借設定。**
   設定只是一組條件（上限、model、接不接單）—— 一個建了設定卻還沒交任何 token
   的人，一杯都還沒借出去。把判準改成「有沒有設定」，那個人就會被當成代跑者，
   然後再也看不到邀請他真的交出帳號的那塊。

2. **`has_run_a_job` 任何狀態都算，包含失敗與取消的。** 它要回答的是「他看過這
   東西怎麼跑」，而失敗的那一趟他一樣看過。改成只算成功的，第一個 job 就失敗的
   人會一直被當成新人。
"""

from __future__ import annotations

import asyncio
import random
import uuid

import pytest
from app.auth import require_user
from app.db import engine
from app.enums import JobStatus, SourceType
from app.main import app
from app.models import Job, LendingAccount, LendingSetting, User
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings  # isort: skip


def _run(coro):
    """在自己的事件迴圈與連線池上做資料準備（test_ledger.py 同一條理由）。"""

    async def go():
        eng = create_async_engine(settings.database_url)
        try:
            async with async_sessionmaker(eng, expire_on_commit=False)() as s:
                return await coro(s)
        finally:
            await eng.dispose()

    return asyncio.run(go())


def _client(user: User) -> TestClient:
    engine.sync_engine.pool.dispose()
    app.dependency_overrides[require_user] = lambda: user
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_pool():
    engine.sync_engine.pool.dispose()
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def newcomer():
    """一個什麼都還沒做的人。測完刪掉 —— 假人留在開發資料庫裡會跑進派單下拉。"""
    tag = uuid.uuid4().hex[:8]

    async def build(s):
        user = User(
            observ_user_id=random.randint(10**8, 10**9),
            email=f"newcomer-{tag}@example.com",
            display_name=f"新人{tag}",
        )
        s.add(user)
        await s.commit()
        return user

    user = _run(build)
    yield user

    async def drop(s):
        # 由內而外刪：帳號 → 設定 → job → 人。外鍵擋得住順序錯誤，
        # 但錯誤訊息會指向外鍵，不會指向「這個 fixture 的刪除順序反了」。
        lending_ids = (
            await s.execute(
                select(LendingSetting.id).where(LendingSetting.owner_user_id == user.id)
            )
        ).scalars()
        await s.execute(
            delete(LendingAccount).where(
                LendingAccount.lending_id.in_(list(lending_ids))
            )
        )
        await s.execute(
            delete(LendingSetting).where(LendingSetting.owner_user_id == user.id)
        )
        await s.execute(delete(Job).where(Job.borrower_id == user.id))
        await s.execute(delete(User).where(User.id == user.id))
        await s.commit()

    _run(drop)


def test_newcomer_is_neither(newcomer) -> None:
    body = _client(newcomer).get("/api/auth/me").json()
    assert body["has_run_a_job"] is False
    assert body["is_lender"] is False


def test_a_failed_job_still_counts(newcomer) -> None:
    """失敗的那一趟他一樣看過整個流程 —— 不該再被當成沒用過的人。"""

    async def build(s):
        s.add(
            Job(
                borrower_id=newcomer.id,
                source_type=SourceType.PASTE,
                prompt="跑個東西",
                status=JobStatus.FAILED,
            )
        )
        await s.commit()

    _run(build)
    assert _client(newcomer).get("/api/auth/me").json()["has_run_a_job"] is True


def test_lending_settings_alone_do_not_make_a_lender(newcomer) -> None:
    """建了條件但一個帳號都沒交 —— 他還沒借出任何東西，邀請要繼續出現。"""

    async def build(s):
        s.add(LendingSetting(owner_user_id=newcomer.id, available_models=["sonnet"]))
        await s.commit()

    _run(build)
    assert _client(newcomer).get("/api/auth/me").json()["is_lender"] is False


def test_one_account_makes_a_lender(newcomer) -> None:
    async def build(s):
        lending = LendingSetting(owner_user_id=newcomer.id, available_models=["sonnet"])
        s.add(lending)
        await s.flush()
        s.add(LendingAccount(lending_id=lending.id, name="個人帳號"))
        await s.commit()

    _run(build)
    assert _client(newcomer).get("/api/auth/me").json()["is_lender"] is True
