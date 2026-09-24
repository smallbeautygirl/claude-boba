"""帳本的「不到一杯的」：小額往來按人加總，讓債主看得到誰來借過。

2026-09-23 正式站第一天：同事借了六次、合計 US$0.62，每筆都在「< US$1 不用還」
那格，帳本寫「乾乾淨淨，不欠任何人」—— 對的時候跟壞掉的時候長得一模一樣。

釘住的是：**規則沒變**（沒有債被建出來），只是多一段脈絡；而且它跟掛債走同一組
篩選 —— 只算成功的、不算自己跑自己的、門檻用 pricing.MIN_DEBT_USD。
"""

from __future__ import annotations

import asyncio
import random
import uuid
from decimal import Decimal

import pytest
from app.auth import require_user
from app.db import engine
from app.enums import JobStatus, SourceType
from app.main import app
from app.models import Job, LendingSetting, User
from app.pricing import MIN_DEBT_USD
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


def _job(
    borrower: User, lending: LendingSetting, cost: str, status=JobStatus.SUCCEEDED
) -> Job:
    return Job(
        borrower_id=borrower.id,
        source_type=SourceType.PASTE,
        prompt="…",
        status=status,
        lending_id=lending.id,
        total_cost_usd=Decimal(cost),
    )


@pytest.fixture
def people():
    tag = uuid.uuid4().hex[:8]
    ids: dict = {}

    async def build(s):
        borrower, lender = _person(tag, "借用者"), _person(tag, "債主")
        s.add_all([borrower, lender])
        await s.flush()
        lending = LendingSetting(owner_user_id=lender.id, available_models=["sonnet"])
        s.add(lending)
        await s.flush()
        jobs = [
            _job(borrower, lending, "0.03"),
            _job(borrower, lending, "0.20"),
            _job(borrower, lending, "0.39"),
            # 這三筆**不該**被算進去：失敗的、自己跑自己的、以及已經夠掛債的。
            _job(borrower, lending, "0.50", status=JobStatus.FAILED),
            _job(lender, lending, "0.10"),
            _job(borrower, lending, str(MIN_DEBT_USD)),
        ]
        s.add_all(jobs)
        await s.commit()
        ids.update(
            borrower=borrower,
            lender=lender,
            lending=lending.id,
            jobs=[j.id for j in jobs],
        )

    _run(build)
    yield ids["borrower"], ids["lender"]

    async def drop(s):
        for jid in ids["jobs"]:
            row = await s.get(Job, jid)
            if row is not None:
                await s.delete(row)
        for model, key in (
            (LendingSetting, ids["lending"]),
            (User, ids["borrower"].id),
            (User, ids["lender"].id),
        ):
            row = await s.get(model, key)
            if row is not None:
                await s.delete(row)
        await s.commit()

    _run(drop)


def test_lender_sees_who_borrowed_below_a_drink(people) -> None:
    borrower, lender = people
    body = _client(lender).get("/api/ledger").json()
    assert body["owed_to_me"] == []  # 規則沒變：一筆債都沒有
    rows = [
        r for r in body["small_change"] if r["counterpart"] == borrower.display_name
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row["direction"] == "owed"
    assert row["jobs"] == 3
    assert Decimal(row["total_usd"]) == Decimal("0.62")


def test_borrower_sees_the_same_rows_from_the_other_side(people) -> None:
    borrower, lender = people
    body = _client(borrower).get("/api/ledger").json()
    rows = [r for r in body["small_change"] if r["counterpart"] == lender.display_name]
    assert len(rows) == 1 and rows[0]["direction"] == "owe" and rows[0]["jobs"] == 3


def test_ledger_carries_the_tier_table(people) -> None:
    """級距表跟著帳本回來，來源是 pricing._TIERS，前端不另抄。"""
    _borrower, lender = people
    tiers = _client(lender).get("/api/ledger").json()["tiers"]
    floors = [Decimal(t["floor_usd"]) for t in tiers]
    assert floors == sorted(floors, reverse=True), "由高到低"
    assert floors[-1] == 0 and tiers[-1]["tier"] == "none"
    assert Decimal(tiers[-2]["floor_usd"]) == MIN_DEBT_USD
    assert all(t["label"] for t in tiers)
