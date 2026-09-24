"""排行榜：三張榜 + 空狀態用的人數。web-spec §7、SPEC §4.9。

釘住的是：

1. **欠債王用未結清筆數排，不用金額**（web-spec §7）。
2. **金主榜算的是幫別人跑成功的 job 數**，不是債 —— 多數 job 不到一杯，只算債的話
   幫全公司做簡報的人會一筆都沒有（2026-09-24 正式站：六個 job、零筆債）。
   自己跑自己的不算。
3. `users` 是全站人數：空狀態要分得出「只有你一個人」跟「有人但還沒跨人借過」。
4. 回應裡沒有任何 job 內容（prompt、result），也沒有 job id。

跑真的資料庫，TestClient 模式同 test_ledger.py。
"""

from __future__ import annotations

import asyncio
import random
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.auth import require_user
from app.db import engine
from app.enums import DebtStatus, DebtTier, JobStatus, SourceType
from app.main import app
from app.models import Debt, Job, LendingSetting, User
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings  # isort: skip


def _run(coro):
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


def _person(tag: str, role: str) -> User:
    return User(
        observ_user_id=random.randrange(1_000_000_000, 2_000_000_000),
        email=f"{role}-{tag}@example.com",
        display_name=f"{role}{tag}",
    )


@pytest.fixture
def world():
    """借用者在債主那裡跑了三個成功的 job（0.3、0.4、4.0），其中 4.0 那筆掛了
    一筆未結清的債；債主自己也跑了自己一次（不算）。"""
    tag = uuid.uuid4().hex[:8]
    ids: dict = {}

    async def build(s):
        borrower, lender = _person(tag, "借用者"), _person(tag, "債主")
        s.add_all([borrower, lender])
        await s.flush()
        lending = LendingSetting(owner_user_id=lender.id, available_models=["sonnet"])
        s.add(lending)
        await s.flush()
        now = datetime.now(UTC)

        def job(who: User, cost: str) -> Job:
            return Job(
                borrower_id=who.id,
                source_type=SourceType.PASTE,
                prompt="秘密內容不該出現在排行榜",
                status=JobStatus.SUCCEEDED,
                lending_id=lending.id,
                total_cost_usd=Decimal(cost),
                finished_at=now,
            )

        jobs = [
            job(borrower, "0.3"),
            job(borrower, "0.4"),
            job(borrower, "4.0"),
            job(lender, "9.0"),
        ]
        s.add_all(jobs)
        await s.flush()
        debt = Debt(
            job_id=jobs[2].id,
            borrower_id=borrower.id,
            lender_id=lender.id,
            amount_usd=Decimal("4.0"),
            tier=DebtTier.COFFEE,
            status=DebtStatus.OPEN,
            created_at=now - timedelta(days=21),
        )
        s.add(debt)
        await s.commit()
        ids.update(
            borrower=borrower,
            lender=lender,
            lending=lending.id,
            jobs=[j.id for j in jobs],
            debt=debt.id,
        )

    _run(build)
    yield ids["borrower"], ids["lender"]

    async def drop(s):
        for model, key in (
            [(Debt, ids["debt"])]
            + [(Job, j) for j in ids["jobs"]]
            + [
                (LendingSetting, ids["lending"]),
                (User, ids["borrower"].id),
                (User, ids["lender"].id),
            ]
        ):
            row = await s.get(model, key)
            if row is not None:
                await s.delete(row)
        await s.commit()

    _run(drop)


def test_boards_count_the_right_things(world) -> None:
    borrower, lender = world
    body = _client(borrower).get("/api/leaderboard").json()

    debtor = next(r for r in body["debtors"] if r["name"] == borrower.display_name)
    assert debtor["open_debts"] == 1
    assert Decimal(debtor["total_usd"]) == Decimal("4.0")
    assert debtor["oldest_days"] == 21

    lender_row = next(r for r in body["lenders"] if r["name"] == lender.display_name)
    assert lender_row["jobs"] == 3, "三個成功的跨人 job；自己跑自己那筆不算"
    assert Decimal(lender_row["total_usd"]) == Decimal("4.7")

    assert body["users"] >= 2

    big = body["biggest_this_month"]
    assert big is not None and Decimal(big["amount_usd"]) >= Decimal("4.0")
    assert big["label"]


def test_nothing_from_a_job_leaks(world) -> None:
    borrower, _ = world
    text = _client(borrower).get("/api/leaderboard").text
    assert "秘密內容" not in text
    assert "job_id" not in text
