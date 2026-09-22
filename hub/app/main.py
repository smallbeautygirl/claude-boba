from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .db import engine
from .routers import admin, auth, commands, jobs, ledger, uploads, worker, workers
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _check_migrations()
    # 金鑰有問題就在啟動時爆，不要等到有代跑者要授權才爆 —— 那時候他已經
    # 走到一半，而錯誤會長得像「授權失敗」。
    check_configured()
    ensure_bucket()
    yield
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
app.include_router(ledger.router)
app.include_router(uploads.router)
app.include_router(worker.router)
app.include_router(workers.router)


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}
