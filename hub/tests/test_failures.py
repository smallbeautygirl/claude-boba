"""失敗分類。

這是使用者在事情出錯時唯一看得到的東西。分類錯了，他要嘛以為工具壞了
（其實是自己可以修的），要嘛以為自己做錯了（其實是系統問題）。
"""

from __future__ import annotations

from app.enums import JobStatus
from app.failures import FailureKind, classify


def test_success_has_no_failure() -> None:
    assert classify(JobStatus.SUCCEEDED, None, None) is None


def test_running_job_has_no_failure_yet() -> None:
    assert classify(JobStatus.RUNNING, None, None) is None


def test_timeout_and_budget_are_fixable_and_say_so() -> None:
    for status in (JobStatus.TIMEOUT, JobStatus.OVER_BUDGET):
        f = classify(status, None, None)
        assert f is not None
        assert f.kind is FailureKind.FIXABLE
        assert f.hint and "不計債" in f.hint


def test_allowlist_block_is_recognised() -> None:
    """RD 最常撞的牆。只寫「執行失敗」的話，他們會以為工具壞了。"""
    f = classify(
        JobStatus.FAILED,
        "api_error",
        "npm error code E403\nnpm error 403 Forbidden - GET https://registry.npmjs.org/pptxgenjs",
    )
    assert f is not None
    assert f.blocked_by_network is True
    assert f.kind is FailureKind.FIXABLE
    assert f.hint and "開放網路" in f.hint


def test_a_bare_403_is_not_assumed_to_be_the_allowlist() -> None:
    """403 也可能是 Claude 打某個 API 被拒。要同時有套件安裝的跡象才算。"""
    f = classify(
        JobStatus.FAILED, "api_error", "The upstream API returned 403 Forbidden"
    )
    assert f is not None
    assert f.blocked_by_network is False


def test_system_problems_hide_the_detail() -> None:
    """worker 內部錯誤的 stderr 對使用者沒有意義，也幫不上忙。"""
    f = classify(
        JobStatus.FAILED, "no_result_event", "Traceback (most recent call last): ..."
    )
    assert f is not None
    assert f.kind is FailureKind.SYSTEM
    assert f.show_detail is False


def test_cancelled_hides_detail_so_the_note_can_speak() -> None:
    f = classify(JobStatus.CANCELLED, "cancelled_by_lender", None)
    assert f is not None and f.show_detail is False


def test_anything_else_is_shown_verbatim() -> None:
    """Claude 的訊息是它給使用者的，不是我們的 —— 原樣顯示比改寫有用。"""
    f = classify(JobStatus.FAILED, "refusal", "I can't help with that.")
    assert f is not None
    assert f.kind is FailureKind.CLAUDE
    assert f.show_detail is True


# --- 授權失效（2026-09-22 真的發生過一次） --------------------------------


def test_auth_failure_is_not_an_over_budget_job() -> None:
    """🚨 這是一個真的把人導去錯路的 bug，不是分類潔癖。

    實際發生的事：job 跑 2 秒、花 $0、死在 401，但畫面說「超過花費上限，
    已中止 —— 換成 Haiku，或把範圍縮小再送一次」。換成 Haiku 會用一模一樣的
    方式再死一次。

    成因有兩層，兩層都要擋：worker 把每個失敗都標成 over_budget（對整包
    result JSON 比對 "budget"，而 result 本來就有那個欄位），而 classify()
    無條件相信那個狀態。所以這裡故意餵**錯的狀態**進去 ——
    原因要贏過狀態。
    """
    f = classify(
        JobStatus.OVER_BUDGET,
        "auth_failed",
        "Failed to authenticate. API Error: 401 OAuth access token is invalid.",
    )
    assert f is not None
    assert "授權" in f.title
    assert f.hint and "Haiku" not in f.hint


def test_auth_failure_is_not_the_borrowers_problem() -> None:
    """委託者修不了它，所以不能給「再試一次」那種建議。"""
    f = classify(JobStatus.FAILED, "auth_failed", None)
    assert f is not None
    assert f.kind is FailureKind.SYSTEM
    assert f.hint and "不是你的問題" in f.hint


def test_auth_failure_recognised_from_the_message_alone() -> None:
    """舊 worker 送上來的 error_kind 是 "success"（subtype 在失敗時也可能是它）。

    所以只靠 error_kind 認不出來 —— 訊息本身也要看。
    """
    f = classify(
        JobStatus.OVER_BUDGET,
        "success",
        "Failed to authenticate. API Error: 401 OAuth access token is invalid.",
    )
    assert f is not None and f.kind is FailureKind.SYSTEM
