from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .db import Base, engine
from .routers import auth, commands, jobs, ledger, worker, workers
from .storage import ensure_bucket


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Phase 1 用 create_all。一旦有真實資料就要換成 Alembic —— 見 README。
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
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

app.include_router(auth.router)
app.include_router(commands.router)
app.include_router(jobs.router)
app.include_router(ledger.router)
app.include_router(worker.router)
app.include_router(workers.router)


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}
