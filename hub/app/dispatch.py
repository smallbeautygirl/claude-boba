"""自動派單挑誰。SPEC.md §4.5。

**這個模組刻意不碰資料庫。** 同一條規則有三個地方要用 —— 領單（routers/worker）、
提交前的檢查（routers/jobs）、提交頁的下拉（web/src/pages/Submit.tsx）——
三處各寫一次就會各自漂移，而「為什麼那個人沒出現在下拉裡」是最難查的那種 bug。
"""

from __future__ import annotations

import uuid
from typing import Protocol


class Lending(Protocol):
    """派單看得到的那一面。實體是 models.LendingSetting。"""

    owner_user_id: uuid.UUID

    def runnable_models(self) -> list[str]: ...


def auto_candidates(
    settings: list[Lending], *, model: str, borrower_id: uuid.UUID
) -> list[Lending]:
    """「自動」的候選人。

    兩條：跑得動這個 model，而且**不是委託者本人**。

    排除本人是 2026-09-23 加的。提交頁自己的文案是「找還有額度的**同事**幫你跑」，
    派給自己不是互助，而且會產生一筆 borrower == lender 的人情債
    （那筆債現在不會產生了，見 pricing.job_creates_debt）。

    **只排除在自動這條路上。**「指定自己」完全不受影響 —— 個人帳號爆了、
    公司帳號還有，那正是 ADR-0001 的場景。
    """
    return [
        s
        for s in settings
        if s.owner_user_id != borrower_id and model in s.runnable_models()
    ]


def only_me(settings: list[Lending], *, model: str, borrower_id: uuid.UUID) -> bool:
    """「站台上跑得動這個 model 的只有你自己」。

    這個狀態值得跟「一個人都沒有」分開講，因為下一步不一樣：這裡的下一步是
    「改選指定自己」，那裡的下一步是「等別人上線」。講錯會讓人等一個永遠不會
    來的人 —— routers/jobs 的註解記過一次那種事真的發生過。

    我在線上但我沒開這個 model 的時候回 False：那不是「只有你」，那是「沒有人」。
    講成「去指定自己」會把人導到一個一樣跑不動的選項。
    """
    if auto_candidates(settings, model=model, borrower_id=borrower_id):
        return False
    return any(
        s.owner_user_id == borrower_id and model in s.runnable_models()
        for s in settings
    )


def follow_up_target(
    *,
    parent_lending_owner: uuid.UUID | None,
    parent_lending_id: uuid.UUID | None,
    borrower_id: uuid.UUID,
) -> uuid.UUID | None:
    """接著問這一輪要不要指名代跑者。

    預設不指名（None = 自動）：transcript 在 MinIO 上，任何出借帳號都拿得到，
    指名會讓那個人剛好停接單時，使用者卡在一個他看不懂的等待上。

    **唯一的例外是「上一輪是你自己跑的」。** 自動自 2026-09-23 起不派給本人
    （auto_candidates），所以自己跑自己的鏈若掉回自動，站台上只有他自己時就是
    一條死路 —— 而且是安靜的那種：提交時不擋，排隊 15 分鐘後「目前沒人有空」
    （2026-09-23 正式站第一條續問鏈就這樣作廢）。他第一輪就是明確選了自己、
    不計債，這一輪沿用是他預期的事，不是替他做決定。
    """
    if parent_lending_owner is not None and parent_lending_owner == borrower_id:
        return parent_lending_id
    return None
