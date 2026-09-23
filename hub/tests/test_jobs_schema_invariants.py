"""jobs 表的兩條 schema 不變量。都是 2026-09-23 正式站第一筆指定代跑者的 job 換來的。

**測的是真的資料庫**（conftest 只允許 `boba`）：這兩件事只在 Postgres 的 schema
與 driver 的錯誤訊息裡看得到，mock 不出來。
"""

from __future__ import annotations

import asyncio
import uuid

from app.config import settings
from app.db import make_engine
from app.enums import JobStatus, SourceType
from app.models import Job
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker


def test_requested_lending_id_has_exactly_one_fk_and_it_points_at_lending_settings():
    """拆 `workers` 表的 migration（a1b2c3d4e5f6）把 `requested_worker_id` 改名成
    `requested_lending_id`，再加了一條指 `lending_settings` 的 FK —— 但**舊的那條
    沒拆**，它跟著被改名的表指到 `lending_accounts`。同一個欄位掛兩條 FK 指兩張表，
    值不可能同時滿足，於是指定代跑者的 job 一律 500。

    dev 從沒踩到：migration 把舊 worker 的 id 同時複製成兩張表的 id，而 dev 的 job
    一筆都沒指定過代跑者。正式站是乾淨的庫，第一筆就炸。
    """

    async def run():
        # 每則測試自己開 engine：`asyncio.run` 每次是新的事件迴圈，共用模組層的
        # 連線池會拿到掛在上一個迴圈的連線（asyncpg 直接 RuntimeError）。
        engine = make_engine(settings.database_url)
        async with engine.connect() as conn:
            rows = await conn.execute(
                text(
                    """
                    select confrelid::regclass::text
                    from pg_constraint c
                    join pg_attribute a
                      on a.attrelid = c.conrelid and a.attnum = any (c.conkey)
                    where c.conrelid = 'jobs'::regclass
                      and c.contype = 'f'
                      and a.attname = 'requested_lending_id'
                    """
                )
            )
            found = [r[0] for r in rows]
        await engine.dispose()
        return found

    targets = asyncio.run(run())
    assert targets == ["lending_settings"], targets


def test_integrity_errors_do_not_carry_the_prompt():
    """security.md 紅線 1：job 內容絕不進 log。

    SQLAlchemy 預設把**綁定參數**塞進例外訊息（`[parameters: (...)]`），而 INSERT
    jobs 的參數裡就有 prompt。uvicorn 把未處理的例外整段印進 log —— 2026-09-23
    正式站的 hub.log 裡就躺著一位使用者的 prompt。engine 要開 `hide_parameters`。
    """
    marker = f"紅線一探針-{uuid.uuid4().hex}"

    async def run() -> str:
        engine = make_engine(settings.database_url)
        async with async_sessionmaker(engine)() as s:
            s.add(
                Job(
                    id=uuid.uuid4(),
                    status=JobStatus.QUEUED,
                    borrower_id=uuid.uuid4(),  # 不存在的 user → FK 一定炸
                    source_type=SourceType.PASTE,
                    prompt=marker,
                    model="sonnet",
                    attachment_keys=[],
                )
            )
            try:
                await s.flush()
            except IntegrityError as exc:
                await s.rollback()
                await engine.dispose()
                return str(exc)
            await s.rollback()
            await engine.dispose()
            raise AssertionError("預期 FK 違反，卻插進去了")

    message = asyncio.run(run())
    assert "violates foreign key" in message
    assert marker not in message, "例外訊息帶著 prompt，這會被 uvicorn 整段印進 log"
