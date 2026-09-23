from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings


def make_engine(url: str):
    """所有 engine 都從這裡出，測試也是 —— engine 的設定只有一份可以錯。

    `hide_parameters=True` 是 security.md 紅線 1：SQLAlchemy 預設把綁定參數塞進
    例外訊息，而 INSERT jobs 的參數裡就有 prompt。未處理的例外會被 uvicorn 整段
    印進 log —— 2026-09-23 正式站的 hub.log 就這樣躺過一位使用者的 prompt。
    代價是 debug 時例外裡只剩 SQL 沒有值；要值就去看資料庫，不要關這個。
    """
    return create_async_engine(url, pool_pre_ping=True, hide_parameters=True)


engine = make_engine(settings.database_url)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
