"""人情債帳本。SPEC.md §4.7、§4.8。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..auth import require_user
from ..db import get_session
from ..enums import DebtStatus
from ..models import Debt, User
from ..pricing import LABELS

router = APIRouter(prefix="/api/ledger", tags=["ledger"])


def _view(d: Debt, *, me: uuid.UUID) -> dict:
    days = (datetime.now(UTC) - d.created_at).days
    return {
        "id": str(d.id),
        "job_id": str(d.job_id),
        "direction": "owe" if d.borrower_id == me else "owed",
        "counterpart": (d.lender if d.borrower_id == me else d.borrower).display_name,
        "amount_usd": str(d.amount_usd),
        "tier": str(d.tier),
        "label": LABELS[d.tier],
        "status": str(d.status),
        # 不設到期日，但顯示欠了幾天 —— 比自動勾銷更有社交壓力，也更好笑。
        "days": days,
        "created_at": d.created_at.isoformat(),
    }


@router.get("")
async def ledger(
    user: User = Depends(require_user), session: AsyncSession = Depends(get_session)
) -> dict:
    rows = await session.scalars(
        select(Debt)
        .options(selectinload(Debt.borrower), selectinload(Debt.lender))
        .where(or_(Debt.borrower_id == user.id, Debt.lender_id == user.id))
        .order_by(Debt.created_at.desc())
    )
    items = [_view(d, me=user.id) for d in rows]
    return {
        "i_owe": [
            i for i in items if i["direction"] == "owe" and i["status"] != "settled"
        ],
        "owed_to_me": [
            i for i in items if i["direction"] == "owed" and i["status"] != "settled"
        ],
        "settled": [i for i in items if i["status"] == "settled"],
    }


@router.post("/{debt_id}/settle")
async def settle(
    debt_id: uuid.UUID,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """結清。**只有債主能按**（SPEC.md §4.8）。

    現實中請客是出租者被請，他最清楚有沒有發生。讓借用者自己宣告結清，
    會變成尷尬的自我認證。
    """
    debt = await _get(debt_id, session)
    if debt.lender_id != user.id:
        raise HTTPException(status_code=403, detail="只有債主能結清這筆")
    debt.status = DebtStatus.SETTLED
    debt.settled_at = datetime.now(UTC)
    await session.commit()
    return {"status": str(debt.status)}


@router.post("/{debt_id}/nudge")
async def nudge(
    debt_id: uuid.UUID,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """「我請過了，戳他確認」。

    債主通常懶得按結清，所以把催促的責任放在欠債的人身上 —— 這也比較符合
    人情實際運作的方式。
    """
    debt = await _get(debt_id, session)
    if debt.borrower_id != user.id:
        raise HTTPException(status_code=403, detail="只有欠債的人能戳")
    debt.status = DebtStatus.NUDGED
    debt.nudged_at = datetime.now(UTC)
    await session.commit()
    return {"status": str(debt.status)}


async def _get(debt_id: uuid.UUID, session: AsyncSession) -> Debt:
    debt = await session.get(Debt, debt_id)
    if debt is None:
        raise HTTPException(status_code=404, detail="debt not found")
    return debt
