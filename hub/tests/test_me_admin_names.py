"""`/me` 的 `admin_names`：隱私勾選點名用（web-spec〈隱私勾選〉，2026-09-23）。

釘兩件事：名字對得回 users 表的 display_name；還沒登入過的管理者不會消失
（退回 email 的 @ 前半）。第二條是揭露的底線 —— 少列一個人就是少揭露一個人。
"""

from __future__ import annotations

import uuid

import pytest
from app.config import settings
from app.models import User
from app.routers.auth import _admin_names
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


async def test_admin_names_resolve_to_display_names_and_never_drop_anyone(
    session, monkeypatch
) -> None:
    tag = uuid.uuid4().hex[:8]
    logged_in = User(
        observ_user_id=uuid.uuid4().int % 2_000_000_000,
        email=f"Admin.{tag}@example.com",  # 大小寫故意跟設定不同
        display_name="小明",
    )
    session.add(logged_in)
    await session.flush()
    monkeypatch.setattr(
        settings, "admin_emails", f"admin.{tag}@example.com, ghost.{tag}@example.com"
    )

    got = await _admin_names(session)
    assert got == sorted(["小明", f"ghost.{tag}"])


async def test_no_admins_means_an_empty_list(session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "admin_emails", "")
    assert await _admin_names(session) == []
