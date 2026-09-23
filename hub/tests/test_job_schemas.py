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
        "lending": None,
        "lending_id": None,
        # 用了哪個出借帳號。只有代跑者本人拿得到值（ADR-0001）。
        "account": None,
        "parent_job_id": None,
        "prompt": "幫我看一下這份需求\n第二行",
        "model": "sonnet",
        "source_type": SourceType.PASTE,
        "created_at": datetime.now(UTC),
        "started_at": datetime.now(UTC),
        "finished_at": datetime.now(UTC),
        "total_cost_usd": Decimal("2.30"),
        "transcript_key": "jobs/x/transcript.jsonl",
        "result_text": "done",
        "error_kind": None,
        "error_detail": None,
        "stop_note": None,
        "lender_cli_version": "2.1.278",
        "borrower_cli_version": None,
        "attachment_keys": [],
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


def _claimed_by(owner_id: uuid.UUID) -> SimpleNamespace:
    """被某個人接走的 job 才可能掛債。

    預設的替身是 `lending=None`，那在現實中不會跟 SUCCEEDED 同時出現 ——
    跑成功的 job 一定被誰接過。掛債的測試要用真的組合，否則 2026-09-23 加的
    「自己跑自己不掛債」在這一層完全測不到。
    """
    return SimpleNamespace(
        owner_user_id=owner_id, owner=SimpleNamespace(display_name="vivian")
    )


def test_debt_label_only_on_success() -> None:
    other = _claimed_by(uuid.uuid4())
    assert _detail(_fake_job(lending=other)).debt_label is not None
    assert _detail(_fake_job(lending=other, status=JobStatus.FAILED)).debt_label is None
    assert _detail(_fake_job(lending=other, total_cost_usd=None)).debt_label is None


def test_running_your_own_job_shows_no_debt_label() -> None:
    """自己跑自己不掛債（SPEC §4.5），畫面上也不該出現級距。

    畫面與帳本必須講同一個故事 —— 兩邊各判斷一次就會有一邊先漂掉，
    所以 `_detail` 跟掛債走的是同一個入口（pricing.job_creates_debt）。
    """
    me = uuid.uuid4()
    job = _fake_job(borrower_id=me, lending=_claimed_by(me))
    detail = _detail(job)
    assert detail.debt_label is None
    assert detail.self_run is True


def test_someone_elses_job_is_not_a_self_run() -> None:
    job = _fake_job(borrower_id=uuid.uuid4(), lending=_claimed_by(uuid.uuid4()))
    assert _detail(job).self_run is False


def test_preview_takes_the_first_non_empty_line() -> None:
    """列表上的摘要。沒有它，一排 UUID 沒人認得出哪個是哪個。"""
    assert _preview("\n\n  第一行  \n第二行") == "第一行"
    assert _preview("") == ""
    long = "字" * 200
    assert _preview(long).endswith("…")
    assert len(_preview(long)) <= 91


def test_only_the_running_lender_can_stop() -> None:
    """授權規則：只有**正在跑這個 job 的出租者**能中止它。

    借用者不行（取消是另一件事，還沒做），不相干的人更不行。
    這條寫錯的後果是別人能停你的 job。
    """
    from app.routers.jobs import _can_stop

    lender = SimpleNamespace(id=uuid.uuid4())
    borrower = SimpleNamespace(id=uuid.uuid4())
    stranger = SimpleNamespace(id=uuid.uuid4())
    lending = SimpleNamespace(owner_user_id=lender.id)

    running = _fake_job(
        status=JobStatus.RUNNING, lending=lending, borrower_id=borrower.id
    )
    assert _can_stop(running, lender) is True
    assert _can_stop(running, borrower) is False
    assert _can_stop(running, stranger) is False


def test_cannot_stop_a_job_that_already_finished() -> None:
    from app.routers.jobs import _can_stop

    lender = SimpleNamespace(id=uuid.uuid4())
    lending = SimpleNamespace(owner_user_id=lender.id)
    for status in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED):
        assert _can_stop(_fake_job(status=status, lending=lending), lender) is False


def test_can_stop_while_still_claimed() -> None:
    """claimed 也算 —— 已經派出去但還沒開始跑，一樣該能喊停。"""
    from app.routers.jobs import _can_stop

    lender = SimpleNamespace(id=uuid.uuid4())
    lending = SimpleNamespace(owner_user_id=lender.id)
    assert (
        _can_stop(_fake_job(status=JobStatus.CLAIMED, lending=lending), lender) is True
    )
