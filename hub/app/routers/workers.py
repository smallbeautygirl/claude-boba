"""出租者管理自己的 worker。"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_user
from ..db import get_session
from ..models import User, Worker

router = APIRouter(prefix="/api/workers", tags=["workers"])

# 超過這個時間沒回報就算離線。worker 的 long-poll 是 30 秒一輪，
# 給兩輪的寬容度，避免網路抖一下就顯示離線。
OFFLINE_AFTER_SECONDS = 75


class WorkerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


def _light(util: float | None) -> str:
    """額度紅綠燈。

    只給燈號，不給百分比（docs/web-spec.md §3）：精確數字會讓借用者盤算
    「他還有 66%，再送一個沒差」，把人情變成資源計算。模糊剛好 ——
    足夠傳達分寸，不足以精算。
    """
    if util is None:
        return "unknown"
    if util < 0.6:
        return "green"
    return "yellow" if util < 0.85 else "red"


def _view(w: Worker, *, owner: bool) -> dict:
    stale = (
        w.last_seen_at is None
        or (datetime.now(UTC) - w.last_seen_at).total_seconds() > OFFLINE_AFTER_SECONDS
    )
    data = {
        "id": str(w.id),
        "name": w.name,
        "owner": w.owner.display_name if w.owner else "?",
        "online": not stale and w.accepting,
        "accepting": w.accepting,
        "allow_full_network": w.allow_full_network,
        "available_models": w.available_models or [],
        "claude_code_version": w.claude_code_version,
        "quota": _light(w.utilization_five_hour),
    }
    if owner:
        # 只有出租者本人看得到自己的精確數字。
        data |= {
            "utilization_five_hour": w.utilization_five_hour,
            "utilization_seven_day": w.utilization_seven_day,
            "job_budget_usd": str(w.job_budget_usd),
            "max_concurrency": w.max_concurrency,
        }
    return data


@router.get("")
async def list_workers(
    user: User = Depends(require_user), session: AsyncSession = Depends(get_session)
) -> list[dict]:
    """提交頁的出租者下拉用。所有人都看得到，但只看得到紅綠燈。"""
    rows = await session.scalars(select(Worker).order_by(Worker.name))
    return [_view(w, owner=w.owner_user_id == user.id) for w in rows]


@router.post("", status_code=201)
async def create_worker(
    body: WorkerCreate,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """產生一個 worker token，貼進 worker 的 .env。

    token 只在這裡回傳一次。worker 因此完全不需要出租者的 Observ 帳密 ——
    把公司密碼寫進 .env 是不必要的風險。
    """
    worker = Worker(
        owner_user_id=user.id,
        name=body.name,
        token=secrets.token_urlsafe(32),
        available_models=["sonnet", "haiku"],
    )
    session.add(worker)
    await session.commit()
    await session.refresh(worker)
    return {"id": str(worker.id), "name": worker.name, "token": worker.token}


@router.post("/{worker_id}/accepting")
async def set_accepting(
    worker_id: uuid.UUID,
    accepting: bool,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """暫停/恢復接單。出租者可能只是要專心工作，不是拒絕誰（web-spec §8）。"""
    worker = await _own(worker_id, user, session)
    worker.accepting = accepting
    await session.commit()
    return {"accepting": worker.accepting}


async def _own(worker_id: uuid.UUID, user: User, session: AsyncSession) -> Worker:
    worker = await session.get(Worker, worker_id)
    if worker is None:
        raise HTTPException(status_code=404, detail="worker not found")
    if worker.owner_user_id != user.id:
        raise HTTPException(status_code=403, detail="這不是你的 worker")
    return worker
