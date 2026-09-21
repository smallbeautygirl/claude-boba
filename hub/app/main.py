from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import Base, engine
from .routers import jobs, worker


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Phase 1 用 create_all。一旦有真實資料就要換成 Alembic —— 見 README。
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(title="claude-boba hub", lifespan=lifespan)

# Phase 1：前端跑在 vite dev server，與 Hub 不同 port。
# 上線前要收斂成明確的來源（.claude/rules/security.md：CORS 不用 ["*"]）。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(jobs.router)
app.include_router(worker.router)


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}
