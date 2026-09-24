"""排行榜。SPEC §4.9、web-spec §7。

**純聚合數字，不洩漏內容**：只有名字與數量，沒有 prompt、沒有「誰跟誰借了什麼」
的逐筆清單（那是動態牆，SPEC §4.3 的隱私線）。

三張榜：

- **欠債王**：未結清筆數最多的人，同時顯示累計金額。用筆數不用金額排 —— 跑了一次
  大 job、當天就請客的人不該高居榜首；榜首該是欠了五杯三個禮拜的那位（web-spec §7）。
  時間範圍是全部，不按月重置：這個系統的核心資產就是債會一直記著。
- **金主榜**：幫別人跑成功最多 job 的人，同時顯示借出的額度合計。用 job 數不用債：
  多數 job 不到一杯（SPEC §4.7），只算債的話，一個幫全公司做了三十份簡報的人會
  一筆都沒有 —— 2026-09-24 正式站就是這樣（六個 job、零筆債）。
- **本月最大宗**：這個月單筆花最多的一個 job。只給金額、級距與兩邊的名字。

`users` 是全站人數，給空狀態用：「只有你一個人」跟「有六個人但還沒有人跨人借過」
是兩件事，下一步不一樣（2026-09-24 回報：明明有兩個人在用，頁面卻說只有一個）。
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import pricing
from ..auth import require_user
from ..db import get_session
from ..enums import DebtStatus, JobStatus
from ..models import Debt, Job, LendingSetting, User
from ..pricing import LABELS, tier_for

router = APIRouter(prefix="/api/leaderboard", tags=["leaderboard"])

_TOP = 10


@router.get("")
async def leaderboard(
    _user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return {
        "users": await session.scalar(select(func.count(User.id))) or 0,
        "debtors": await _debtors(session),
        "lenders": await _lenders(session),
        "biggest_this_month": await _biggest_this_month(session),
        # 級距表跟著回：榜上寫「欠著 2 筆」時，旁邊就要看得到一筆是怎麼算出來的
        # （2026-09-24 回報）。同一份 pricing._TIERS，前端不另抄。
        "tiers": pricing.table(),
    }


async def _debtors(session: AsyncSession) -> list[dict]:
    rows = (
        await session.execute(
            select(
                User.display_name,
                func.count(Debt.id),
                func.sum(Debt.amount_usd),
                func.min(Debt.created_at),
            )
            .join(User, User.id == Debt.borrower_id)
            .where(Debt.status != DebtStatus.SETTLED)
            .group_by(User.id, User.display_name)
            .order_by(func.count(Debt.id).desc(), func.sum(Debt.amount_usd).desc())
            .limit(_TOP)
        )
    ).all()
    now = datetime.now(UTC)
    return [
        {
            "name": name,
            "open_debts": int(n),
            "total_usd": str(total),
            # 最老那筆欠了幾天 —— 「欠了五杯三個禮拜」的「三個禮拜」。
            "oldest_days": (now - oldest).days if oldest else 0,
        }
        for name, n, total, oldest in rows
    ]


async def _lenders(session: AsyncSession) -> list[dict]:
    rows = (
        await session.execute(
            select(
                User.display_name,
                func.count(Job.id),
                func.coalesce(func.sum(Job.total_cost_usd), 0),
            )
            .join(LendingSetting, LendingSetting.id == Job.lending_id)
            .join(User, User.id == LendingSetting.owner_user_id)
            .where(
                Job.status.in_([st for st in JobStatus if st.creates_debt]),
                Job.borrower_id != LendingSetting.owner_user_id,
            )
            .group_by(User.id, User.display_name)
            .order_by(
                func.count(Job.id).desc(),
                func.coalesce(func.sum(Job.total_cost_usd), 0).desc(),
            )
            .limit(_TOP)
        )
    ).all()
    return [
        {"name": name, "jobs": int(n), "total_usd": str(total)}
        for name, n, total in rows
    ]


async def _biggest_this_month(session: AsyncSession) -> dict | None:
    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    borrower = User.__table__.alias("borrower")
    owner = User.__table__.alias("owner")
    row = (
        await session.execute(
            select(
                Job.total_cost_usd,
                Job.finished_at,
                borrower.c.display_name,
                owner.c.display_name,
            )
            .join(LendingSetting, LendingSetting.id == Job.lending_id)
            .join(borrower, borrower.c.id == Job.borrower_id)
            .join(owner, owner.c.id == LendingSetting.owner_user_id)
            .where(
                Job.status.in_([st for st in JobStatus if st.creates_debt]),
                Job.borrower_id != LendingSetting.owner_user_id,
                Job.total_cost_usd.is_not(None),
                Job.finished_at >= month_start,
            )
            .order_by(Job.total_cost_usd.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    cost, finished, b_name, o_name = row
    return {
        # 不給 job id 連結：這一頁誰都看得到，job 頁只有當事人能開。
        "amount_usd": str(cost),
        "label": LABELS[tier_for(cost)],
        "borrower": b_name,
        "lender": o_name,
        "finished_at": finished.isoformat() if finished else None,
    }
