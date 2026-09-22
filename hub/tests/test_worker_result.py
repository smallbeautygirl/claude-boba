"""worker 把 CLI 的 result 事件對應成 job 狀態。

🚨 **不要對整包 result 做字串比對。** 原本 over_budget 的判斷是
`"budget" in json.dumps(result)`，而 result 裡本來就有一個 `budget` 欄位 ——
於是**每一個失敗的 job 都被標成「超出預算」**。它從來沒有正確過，
只是在 2026-09-22 之前沒有東西失敗過。
"""

from __future__ import annotations

import sys
from pathlib import Path

# worker/ 不是一個套件，也沒有自己的 pytest 設定 —— 這個檔放在 hub/tests
# 是為了讓它真的會被跑到。測的是 worker 的純函式，不碰網路也不碰 docker。
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "worker"))

from worker import _result_payload


def _call(result: dict) -> dict:
    return _result_payload(result, returncode=1, cancelled=False, stderr_tail=[])


# 真實的 result 事件長這樣：usage 裡有 budget，subtype 在失敗時仍是 "success"。
_AUTH_RESULT = {
    "is_error": True,
    "subtype": "success",
    "terminal_reason": "api_error",
    "result": "Failed to authenticate. API Error: 401 OAuth access token is invalid.",
    "total_cost_usd": 0,
    "usage": {"budget": {"remaining": 5}},
}


def test_auth_failure_is_not_over_budget() -> None:
    out = _call(_AUTH_RESULT)
    assert out["status"] == "failed"
    assert out["error_kind"] == "auth_failed"


def test_the_budget_field_alone_does_not_mean_over_budget() -> None:
    """這一條就是那個 bug 本身：只要 result 裡有 budget 欄位就被判定超支。"""
    out = _call(
        {
            "is_error": True,
            "subtype": "error_during_execution",
            "result": "something else went wrong",
            "usage": {"budget": {"remaining": 5}},
        }
    )
    assert out["status"] == "failed"


def test_a_real_over_budget_still_reads_as_over_budget() -> None:
    out = _call(
        {
            "is_error": True,
            "terminal_reason": "max_budget_exceeded",
            "result": "Exceeded max budget",
        }
    )
    assert out["status"] == "over_budget"


def test_error_kind_is_never_the_word_success() -> None:
    """subtype 在失敗時也可能是 "success"，拿它當 error_kind 會印在畫面上。"""
    out = _call({"is_error": True, "subtype": "success", "result": "boom"})
    assert out["error_kind"] != "success"
