"""出借設定的外網預設是開的（2026-09-23，紅線 3 改寫）。

釘住預設值本身，因為它在兩個地方各一份（ORM default 與 DB server_default），
其中一份漂掉的話，走 API 建的與走 SQL 建的會拿到不同的預設。
"""

from __future__ import annotations

import uuid

import pytest
from app.config import settings
from app.models import LendingSetting, User
from sqlalchemy import text
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


async def test_new_lending_settings_allow_network_by_default(session) -> None:
    user = User(
        observ_user_id=uuid.uuid4().int % 2_000_000_000,
        email=f"{uuid.uuid4()}@example.com",
        display_name="新代跑者",
    )
    session.add(user)
    await session.flush()

    via_orm = LendingSetting(owner_user_id=user.id, available_models=["sonnet"])
    session.add(via_orm)
    await session.flush()
    assert via_orm.allow_full_network is True

    # 直接走 SQL、不給那個欄位：拿的是 DB 的 server_default，兩份要一致。
    # owner_user_id 是 unique，所以再開一個人。
    other = User(
        observ_user_id=uuid.uuid4().int % 2_000_000_000,
        email=f"{uuid.uuid4()}@example.com",
        display_name="另一位",
    )
    session.add(other)
    await session.flush()
    raw_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO lending_settings"
            " (id, owner_user_id, available_models, job_budget_usd, accepting)"
            " VALUES (:id, :owner, '[\"sonnet\"]'::jsonb, 5, true)"
        ),
        {"id": raw_id, "owner": other.id},
    )
    got = await session.scalar(
        text("SELECT allow_full_network FROM lending_settings WHERE id = :id"),
        {"id": raw_id},
    )
    assert got is True
