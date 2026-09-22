"""出租者管理自己的 worker。"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_user
from ..db import get_session
from ..models import Job, User, Worker

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


def _view(w: Worker, *, owner: bool, jobs: int = 0) -> dict:
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
            # 跑過幾個 job。前端用它決定刪除按鈕能不能按，**而且要說得出原因** ——
            # 只把按鈕灰掉的話，使用者不知道自己少做了什麼才能刪。
            "job_count": jobs,
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
    rows = list(await session.scalars(select(Worker).order_by(Worker.name)))
    # 只算自己的 worker 的 job 數 —— 別人的 job 數不該出現在別人的畫面上，
    # 那是「誰幫誰跑過幾次」的資訊，不是紅綠燈那種粗略分寸。
    mine = [w.id for w in rows if w.owner_user_id == user.id]
    counts: dict[uuid.UUID, int] = {}
    if mine:
        counted = await session.execute(
            select(Job.worker_id, func.count())
            .where(Job.worker_id.in_(mine))
            .group_by(Job.worker_id)
        )
        counts = {wid: n for wid, n in counted}
    return [
        _view(w, owner=w.owner_user_id == user.id, jobs=counts.get(w.id, 0))
        for w in rows
    ]


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


@router.delete("/{worker_id}", status_code=204)
async def delete_worker(
    worker_id: uuid.UUID,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """移除一台**從來沒跑過 job** 的 worker。

    判定不是「現在沒有進行中的 job」—— 那會讓一台跑過 50 個 job 的機器只要
    閒著就能被刪掉，那 50 筆 job 的「由 X 代跑」會斷掉，而那是人情債的依據。

    跑過 job 的機器目前沒有辦法從清單移除。這是已知的洞，先不補 —— 還沒有人
    真的退役過機器，替還沒發生的情境設計正是 web-spec §10 一直在避免的事。
    """
    # 鎖住這一列再數：不鎖的話，「數完、還沒刪」之間剛好被派一個 job 就出事。
    # 派單那邊（routers/worker.py）設 Job.worker_id 時，Postgres 會對這一列拿
    # FOR KEY SHARE，跟這裡的 FOR UPDATE 互斥，所以兩邊只能有一個先過。
    worker = await _own(worker_id, user, session, lock=True)
    used = await session.scalar(
        select(func.count())
        .select_from(Job)
        .where(or_(Job.worker_id == worker_id, Job.requested_worker_id == worker_id))
    )
    if used:
        ran = await session.scalar(
            select(func.count()).select_from(Job).where(Job.worker_id == worker_id)
        )
        raise HTTPException(
            status_code=409,
            detail=(
                f"這台跑過 {ran} 個 job，刪掉會讓那些紀錄失去出租者"
                if ran
                # requested_worker_id 也要擋：有人指名這台、job 還在排隊時，
                # 刪掉會讓那筆 job 指向一個不存在的 worker。
                else "有 job 指名這台在排隊，等它跑完或過期再刪"
            ),
        )
    await session.delete(worker)
    try:
        await session.commit()
    except IntegrityError:
        # 資料庫是最後一道防線：真的在這一瞬間被派了單，外鍵會擋下來。
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="剛剛有 job 派給這台，請重新整理再試"
        ) from None


async def _own(
    worker_id: uuid.UUID, user: User, session: AsyncSession, *, lock: bool = False
) -> Worker:
    worker = await session.get(Worker, worker_id, with_for_update=lock)
    if worker is None:
        raise HTTPException(status_code=404, detail="worker not found")
    if worker.owner_user_id != user.id:
        raise HTTPException(status_code=403, detail="這不是你的 worker")
    return worker
