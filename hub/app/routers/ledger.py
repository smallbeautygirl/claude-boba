"""人情債帳本。SPEC.md §4.7、§4.8。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .. import notify
from ..auth import require_user
from ..db import get_session
from ..enums import DebtStatus, JobStatus
from ..models import Debt, Job, LendingSetting, User
from ..pricing import LABELS, MIN_DEBT_USD

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
        "small_change": await _small_change(user.id, session),
    }


async def _small_change(me: uuid.UUID, session: AsyncSession) -> list[dict]:
    """不到一杯的往來，按人加總。

    級距表最底下那格「< US$1 不用還」是刻意的（SPEC §4.7），所以這些 job 沒有債。
    但 2026-09-23 正式站第一天：同事借了六次、合計 US$0.62，債主的帳本寫著
    「乾乾淨淨，不欠任何人」—— 對的時候跟壞掉的時候長得一模一樣，債主以為
    記帳沒在動。這一段只是把「人情而已」讓兩邊都看得到，**不改規則**：
    這裡沒有按鈕、不會變成債，也不累計成一杯。

    只算成功、且不是自己跑自己的 job（跟掛債同一個入口的兩條規則）；
    金額門檻用 pricing.MIN_DEBT_USD，級距表改了這裡跟著動。
    """
    cost = Job.total_cost_usd
    stmt = (
        select(
            Job.borrower_id,
            LendingSetting.owner_user_id,
            func.count(Job.id),
            func.sum(cost),
            func.max(Job.finished_at),
        )
        .join(LendingSetting, LendingSetting.id == Job.lending_id)
        .where(
            Job.status.in_([st for st in JobStatus if st.creates_debt]),
            cost.is_not(None),
            cost < MIN_DEBT_USD,
            Job.borrower_id != LendingSetting.owner_user_id,
            or_(Job.borrower_id == me, LendingSetting.owner_user_id == me),
        )
        .group_by(Job.borrower_id, LendingSetting.owner_user_id)
    )
    rows = (await session.execute(stmt)).all()
    if not rows:
        return []
    others = {b if b != me else o for b, o, *_ in rows}
    names = dict(
        (
            await session.execute(
                select(User.id, User.display_name).where(User.id.in_(others))
            )
        ).all()
    )
    out = []
    for borrower_id, owner_id, n, total, last in rows:
        direction = "owe" if borrower_id == me else "owed"
        other = owner_id if direction == "owe" else borrower_id
        out.append(
            {
                "direction": direction,
                "counterpart": names.get(other, "？"),
                "jobs": int(n),
                "total_usd": str(total),
                "last_at": last.isoformat() if last else None,
            }
        )
    out.sort(key=lambda r: (r["direction"], -r["jobs"]))
    return out


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
    # 已經結清的再按一次就是同一件事，不是錯誤 —— 但**不再送一次通知**。
    # 兩個分頁、或 Teams 連結點兩下，都會走到這裡。
    if debt.status is DebtStatus.SETTLED:
        return {"status": str(debt.status)}
    debt.status = DebtStatus.SETTLED
    debt.settled_at = datetime.now(UTC)
    await session.commit()
    notify.debt_settled(debt.borrower, user, LABELS[debt.tier], debt.id)
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

    **戳是一則通知，不是一個狀態切換。** 再戳一次會再送一則 —— 那正是「催促」
    的意思，而且這個站台只有個位數使用者，防洗版不是現在該花的複雜度。
    """
    debt = await _get(debt_id, session)
    if debt.borrower_id != user.id:
        raise HTTPException(status_code=403, detail="只有欠債的人能戳")
    # 結清是**債主單方面宣告**的（SPEC §4.8）。讓借用者戳一筆已結清的債，
    # 等於讓他把別人宣告過的事情撤銷掉 —— 帳本上那筆會從「已結清」跳回
    # 債主的「別人欠你」，而債主不會知道。這條在 2026-09-23 實測到過。
    if debt.status is DebtStatus.SETTLED:
        raise HTTPException(status_code=409, detail="這筆已經結清了，不用再戳")
    debt.status = DebtStatus.NUDGED
    debt.nudged_at = datetime.now(UTC)
    await session.commit()
    notify.debt_nudged(debt.lender, user, LABELS[debt.tier], debt.id)
    return {"status": str(debt.status)}


async def _get(debt_id: uuid.UUID, session: AsyncSession) -> Debt:
    """借貸雙方一起載進來 —— 結清與戳都要通知對方，而 commit 之後
    再去 lazy load 一個關聯會是 async 下的 `MissingGreenlet`。"""
    debt = await session.get(
        Debt, debt_id, options=[selectinload(Debt.borrower), selectinload(Debt.lender)]
    )
    if debt is None:
        raise HTTPException(status_code=404, detail="debt not found")
    return debt
