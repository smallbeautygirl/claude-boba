"""金額與級距。

這是 repo 的第一個測試，從這裡開始不是巧合：算錯錢是這個專案裡最不能
無聲發生的事 —— 帳單是要給人看的，數字錯一次，整個工具的可信度就沒了。
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from app.enums import DebtTier, JobStatus
from app.pricing import LABELS, MIN_DEBT_USD, job_creates_debt, tier_for


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        ("0", DebtTier.NONE),
        ("0.9999", DebtTier.NONE),
        ("1", DebtTier.DRINK),  # 邊界含下限
        ("2.99", DebtTier.DRINK),
        ("3", DebtTier.COFFEE),
        ("7.99", DebtTier.COFFEE),
        ("8", DebtTier.BENTO),
        ("19.99", DebtTier.BENTO),
        ("20", DebtTier.FEAST),
        ("1000", DebtTier.FEAST),
    ],
)
def test_tier_boundaries(amount: str, expected: DebtTier) -> None:
    assert tier_for(Decimal(amount)) is expected


def test_every_tier_has_a_label() -> None:
    """少一個 label 會讓帳本在那個級距整頁炸掉，而那正是金額最大的時候。"""
    for tier in DebtTier:
        assert tier in LABELS


def test_min_debt_matches_the_table() -> None:
    """MIN_DEBT_USD 是從級距表導出的，不是另外寫死的常數。

    兩處各寫一個數字，改了其中一個就會靜靜地不一致。
    """
    assert MIN_DEBT_USD == Decimal(1)
    assert tier_for(MIN_DEBT_USD) is not DebtTier.NONE
    assert tier_for(MIN_DEBT_USD - Decimal("0.01")) is DebtTier.NONE


def test_only_success_creates_debt() -> None:
    """SPEC §5：失敗、逾時、取消、超出預算一律不計債。

    這條規則的價值在於簡單。任何人想「修正」成按比例計費之前，先看 SPEC §5。
    """
    assert JobStatus.SUCCEEDED.creates_debt
    for status in JobStatus:
        if status is not JobStatus.SUCCEEDED:
            assert not status.creates_debt, status


def test_running_your_own_job_creates_no_debt() -> None:
    """自己跑自己不掛債（SPEC §4.5）。

    「自動」派單排除本人，但**指定自己**是留著的 —— 個人帳號爆了、公司帳號還有，
    那正是 ADR-0001 的場景。那一趟燒的是自己的額度，掛一筆「你欠你自己一杯手搖」
    在帳本與排行榜上是純雜訊。

    **一開始就不記，不是記了再過濾。** 過濾一筆已經存在的債，得在帳本、排行榜、
    掛債通知三個地方各記得一次；不記只要記得一次。
    """
    me, other = uuid.uuid4(), uuid.uuid4()
    assert job_creates_debt(JobStatus.SUCCEEDED, borrower_id=me, lender_id=other)
    assert not job_creates_debt(JobStatus.SUCCEEDED, borrower_id=me, lender_id=me)


def test_self_run_still_respects_the_status_rule() -> None:
    """兩條規則是 AND，不是其中一條說了算。"""
    me, other = uuid.uuid4(), uuid.uuid4()
    for status in JobStatus:
        if status is JobStatus.SUCCEEDED:
            continue
        assert not job_creates_debt(status, borrower_id=me, lender_id=other), status
        assert not job_creates_debt(status, borrower_id=me, lender_id=me), status
