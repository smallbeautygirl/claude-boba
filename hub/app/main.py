from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import authorize, expiry, orphans
from .config import settings
from .db import engine
from .routers import (
    admin,
    auth,
    commands,
    jobs,
    leaderboard,
    ledger,
    uploads,
    wishes,
    worker,
    workers,
)
from .secrets_box import check_configured
from .storage import ensure_bucket


async def _check_migrations() -> None:
    """DB 落後就拒絕啟動，並講清楚要跑什麼。

    刻意不自動套用 migration：自動 upgrade 會在多個 instance 同時啟動時互相
    競爭，而且會讓一個有問題的 migration 悄悄上線。失敗要響亮，不要猜。
    """
    script = ScriptDirectory(str(Path(__file__).resolve().parent.parent / "alembic"))
    head = script.get_current_head()

    async with engine.connect() as conn:
        current = await conn.run_sync(
            lambda sync_conn: MigrationContext.configure(
                sync_conn
            ).get_current_revision()
        )

    if current == head:
        return
    raise RuntimeError(
        f"資料庫 schema 版本是 {current or '（未初始化）'}，程式需要 {head}。\n"
        f"請先在 hub/ 執行：  .venv/bin/alembic upgrade head"
    )


def _check_observ() -> None:
    """認證來源沒設就拒絕啟動。

    跟金鑰同一條理由（secrets_box）：**失敗要響亮**。少了它，Hub 會正常起來、
    登入頁會正常顯示，然後每一次登入都失敗 —— 而錯誤會長得像「帳號密碼錯了」，
    那是最花時間的一種假象。
    """
    missing = [
        name
        for name, value in (
            ("OBSERV_BASE_URL", settings.observ_base_url),
            ("OBSERV_SERVICE_ID", settings.observ_service_id),
        )
        if not value.strip()
    ]
    if missing:
        raise RuntimeError(
            f"{'、'.join(missing)} 沒有設定，Hub 不會有任何人登得進來。\n"
            f"請在 hub/.env 填上認證來源（見 .env.example）。"
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _check_migrations()
    _check_observ()
    # 金鑰有問題就在啟動時爆，不要等到有代跑者要授權才爆 —— 那時候他已經
    # 走到一半，而錯誤會長得像「授權失敗」。
    check_configured()
    ensure_bucket()

    # 授權 session 的清潔工。沒有它，開了授權頁又關掉的人會留下一個
    # 永不結束的 `claude setup-token` 子行程。
    #
    # 這個 create_task 是它唯一的啟動點 —— 之前沒有人呼叫，那條防禦在正式
    # 環境一次都沒跑過，而八個生命週期測試全綠，因為它們呼叫的是同步的
    # `sweep()`，不是這個迴圈。**測試呼叫得到內層函式，不代表外層有人啟動它。**
    sweeper = asyncio.create_task(authorize.sweeper(), name="authorize-sweeper")

    # 派不出去的 job 的清潔工。同樣地，**這行 create_task 是它唯一的啟動點** ——
    # `expired` 這個狀態、它的文案與 job_queue_expiry_seconds 在這之前就都存在了，
    # 只是沒有任何程式會去設它，所以 job 會永遠停在「排隊中」（見 app/expiry.py）。
    expirer = asyncio.create_task(expiry.sweeper(), name="job-expiry-sweeper")

    # worker 死掉之後其 job 的清潔工。同一句話第三次：**這行是它唯一的啟動點。**
    # 2026-09-23 一個 job 因 worker 崩潰而「執行中」半小時，才發現 hub 對已派出的
    # job 完全沒有自己的判斷（見 app/orphans.py）。
    orphaner = asyncio.create_task(orphans.sweeper(), name="job-orphan-sweeper")
    try:
        yield
    finally:
        for task in (sweeper, expirer, orphaner):
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await engine.dispose()


app = FastAPI(title="claude-boba hub", lifespan=lifespan)

# 前端跑在 vite dev server，與 Hub 不同 port，所以需要 CORS。
# 來源從設定讀（見 config.py），不寫死也不用 ["*"]。
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(admin.router)
app.include_router(auth.router)
app.include_router(commands.router)
app.include_router(jobs.router)
app.include_router(leaderboard.router)
app.include_router(ledger.router)
app.include_router(uploads.router)
app.include_router(wishes.router)
app.include_router(worker.router)
app.include_router(workers.router)


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}
