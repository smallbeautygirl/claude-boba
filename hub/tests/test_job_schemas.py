"""Job 的回應模型。

這組測試的存在有個具體理由：`JobDetail` 繼承 `JobSummary`，而列表與詳情是
**兩個不同的建構處**。往父類別加欄位時只更新其中一個，另一邊就會在執行時
噴 pydantic ValidationError —— 而且是 500，前端如果沒有 catch 就永遠停在
「載入中…」。實際發生過一次。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from app.enums import JobStatus, SourceType
from app.routers.jobs import _detail, _preview


def _fake_job(**overrides):
    base = {
        "id": uuid.uuid4(),
        "status": JobStatus.SUCCEEDED,
        "borrower": SimpleNamespace(display_name="kevin"),
        "borrower_id": uuid.uuid4(),
        "worker": None,
        "worker_id": None,
        "parent_job_id": None,
        "prompt": "幫我看一下這份需求\n第二行",
        "model": "sonnet",
        "source_type": SourceType.PASTE,
        "created_at": datetime.now(UTC),
        "finished_at": datetime.now(UTC),
        "total_cost_usd": Decimal("2.30"),
        "transcript_key": "jobs/x/transcript.jsonl",
        "result_text": "done",
        "error_kind": None,
        "error_detail": None,
        "stop_note": None,
        "lender_cli_version": "2.1.278",
        "borrower_cli_version": None,
    }
    return SimpleNamespace(**(base | overrides))


def test_detail_supplies_every_required_field() -> None:
    """_detail() 必須填滿 JobDetail 的所有必填欄位，包含繼承來的。"""
    detail = _detail(_fake_job())
    assert detail.borrower == "kevin"
    assert detail.preview  # 繼承自 JobSummary，曾經漏填
    assert detail.is_follow_up is False


def test_detail_marks_follow_ups() -> None:
    parent = uuid.uuid4()
    detail = _detail(_fake_job(parent_job_id=parent))
    assert detail.is_follow_up is True
    assert detail.parent_job_id == parent


def test_debt_label_only_on_success() -> None:
    assert _detail(_fake_job()).debt_label is not None
    assert _detail(_fake_job(status=JobStatus.FAILED)).debt_label is None
    assert _detail(_fake_job(total_cost_usd=None)).debt_label is None


def test_preview_takes_the_first_non_empty_line() -> None:
    """列表上的摘要。沒有它，一排 UUID 沒人認得出哪個是哪個。"""
    assert _preview("\n\n  第一行  \n第二行") == "第一行"
    assert _preview("") == ""
    long = "字" * 200
    assert _preview(long).endswith("…")
    assert len(_preview(long)) <= 91
