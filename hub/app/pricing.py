"""人情債的換算。SPEC.md §4.7。

級距刻意做得很粗。精確的數字會讓人開始計較，粗糙的級距才會讓人笑著說「好啦我請」。
最底下那條「< US$1 不用還」是整張表最重要的一列 —— 多數 job 會落在那一格，
而讓多數互動不欠債，真正欠債時才顯得慎重。

全站共用一張表，不給出租者自訂：一旦可改就會有人比價，人情變市集。
"""

from __future__ import annotations

from decimal import Decimal

from .enums import DebtTier

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


def tier_for(amount_usd: Decimal) -> DebtTier:
    for floor, tier in _TIERS:
        if amount_usd >= floor:
            return tier
    return DebtTier.NONE


def label_for(amount_usd: Decimal) -> str:
    return LABELS[tier_for(amount_usd)]
