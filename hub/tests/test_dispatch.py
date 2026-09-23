"""自動派單挑誰。

這一組的重點只有一條：**「自動」不派給你自己**（SPEC §4.5，2026-09-23）。
提交頁自己的文案是「找還有額度的**同事**幫你跑」，派給自己不是互助。

但「指定自己」是留著的 —— 個人帳號爆了、公司帳號還有，那正是 ADR-0001 的場景。
所以排除只發生在自動這條路上，指定那條完全不受影響。
"""

from __future__ import annotations

import uuid

from app.dispatch import auto_candidates, only_me


class _Setting:
    """LendingSetting 的替身。只實作派單看得到的那一面。"""

    def __init__(self, owner_user_id: uuid.UUID, runnable: list[str]) -> None:
        self.owner_user_id = owner_user_id
        self.id = uuid.uuid4()
        self._runnable = runnable

    def runnable_models(self) -> list[str]:
        return self._runnable


ME = uuid.uuid4()
OTHER = uuid.uuid4()


def test_auto_skips_the_borrower() -> None:
    mine, theirs = _Setting(ME, ["sonnet"]), _Setting(OTHER, ["sonnet"])
    picked = auto_candidates([mine, theirs], model="sonnet", borrower_id=ME)
    assert picked == [theirs]


def test_auto_still_filters_by_model() -> None:
    """排除自己是**加**一條，不是取代原本那條。"""
    haiku_only = _Setting(OTHER, ["haiku"])
    assert auto_candidates([haiku_only], model="sonnet", borrower_id=ME) == []


def test_auto_can_end_up_with_nobody() -> None:
    """站台上只有你自己的時候，自動就是派不出去 —— 這是正常狀態，不是錯誤。"""
    mine = _Setting(ME, ["sonnet"])
    assert auto_candidates([mine], model="sonnet", borrower_id=ME) == []


def test_only_me_is_the_state_worth_a_different_message() -> None:
    """「站台上只有你」與「站台上沒有人」要分開講。

    前者的下一步是「選指定自己」，後者的下一步是「等別人上線」。
    講錯的代價是讓人等一個永遠不會來的人 —— 2026-09-22 真的發生過一次。
    """
    mine, theirs = _Setting(ME, ["sonnet"]), _Setting(OTHER, ["sonnet"])
    assert only_me([mine], model="sonnet", borrower_id=ME)
    assert not only_me([mine, theirs], model="sonnet", borrower_id=ME)
    assert not only_me([], model="sonnet", borrower_id=ME)


def test_only_me_is_false_when_i_cannot_run_it_either() -> None:
    """我在線上但我沒開這個 model —— 那不是「只有你」，那是「沒有人」。

    講成「只有你，去指定自己」會把人導到一個一樣跑不動的選項。
    """
    mine = _Setting(ME, ["haiku"])
    assert not only_me([mine], model="sonnet", borrower_id=ME)
