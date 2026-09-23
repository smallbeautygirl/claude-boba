"""人情債的換算。SPEC.md §4.7。

級距刻意做得很粗。精確的數字會讓人開始計較，粗糙的級距才會讓人笑著說「好啦我請」。
最底下那條「< US$1 不用還」是整張表最重要的一列 —— 多數 job 會落在那一格，
而讓多數互動不欠債，真正欠債時才顯得慎重。

全站共用一張表，不給出租者自訂：一旦可改就會有人比價，人情變市集。
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from .enums import DebtTier, JobStatus

# (下限含, 級距)。由高到低比對。
_TIERS: list[tuple[Decimal, DebtTier]] = [
    (Decimal(20), DebtTier.FEAST),
    (Decimal(8), DebtTier.BENTO),
    (Decimal(3), DebtTier.COFFEE),
    (Decimal(1), DebtTier.DRINK),
    (Decimal(0), DebtTier.NONE),
]

LABELS: dict[DebtTier, str] = {
    DebtTier.NONE: "🤝 不用還，人情而已",
    DebtTier.DRINK: "🧋 一杯手搖",
    DebtTier.COFFEE: "☕️ 一杯咖啡 + 一份點心",
    DebtTier.BENTO: "🍱 一個便當",
    DebtTier.FEAST: "🍖 一頓好料，而且你要挑餐廳",
}


def job_creates_debt(
    status: JobStatus, *, borrower_id: uuid.UUID, lender_id: uuid.UUID
) -> bool:
    """這一趟要不要掛債。**掛債的判斷只有這一個入口。**

    跟 `JobStatus.creates_debt` 的差別：那個回答「這個**狀態**會不會掛債」，
    這個回答「這**一筆 job** 會不會掛債」—— 後者還要看是誰欠誰。

    兩條規則：

    1. 只有成功才掛（SPEC §5）。失敗、逾時、取消、超出預算一律不計。
    2. **自己跑自己不掛**（SPEC §4.5，2026-09-23）。「自動」派單排除本人，
       但「指定自己」留著 —— 個人帳號爆了、公司帳號還有，那正是 ADR-0001 的
       場景。那一趟燒的是自己的額度，掛一筆「你欠你自己一杯手搖」在帳本與
       排行榜上是純雜訊。

    第 2 條選擇**一開始就不記**，而不是記了再過濾：過濾一筆已經存在的債，
    得在帳本、排行榜、掛債通知三個地方各記得一次；不記只要記得一次。
    """
    if not status.creates_debt:
        return False
    return borrower_id != lender_id


# 會產生債務的最低金額。出租者的單次預算上限若低於這個數字，
# 他的 job 永遠不可能掛債 —— 能跑完的都在門檻以下。見 workers.report_config 的警告。
MIN_DEBT_USD: Decimal = min(
    floor for floor, tier in _TIERS if tier is not DebtTier.NONE
)


def tier_for(amount_usd: Decimal) -> DebtTier:
    for floor, tier in _TIERS:
        if amount_usd >= floor:
            return tier
    return DebtTier.NONE


def label_for(amount_usd: Decimal) -> str:
    return LABELS[tier_for(amount_usd)]
