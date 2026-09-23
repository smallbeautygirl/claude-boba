"""`_account_view()` 放進去的每一個 key，`AccountView` 都要有。

**為什麼值得一支專門的測試。** `/api/workers/settings` 有 `response_model`，
所以 pydantic 會把 model 上沒有的 key **靜靜地**丟掉 —— 不報錯、不警告。
2026-09-23 就這樣掉了一個欄位：`last_assigned_at` 加進了 `_account_view` 的
dict 卻沒加進 model，結果出借頁四張卡片全寫「還沒被派到過 job」，
包含那個跑過六個 job 的帳號。**畫面上說了一句假話，而後端與前端都沒有錯。**

這支測試守的是那個縫，不是那一個欄位。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from app.routers.workers import AccountView, _account_view


def _account() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="個人 Max",
        oauth_token_enc=b"x",
        needs_reauth=False,
        approver_note=None,
        credits_required_models=[],
        rate_limit_windows={"five_hour": {"utilization": 0.3}},
        quota_updated_at=datetime.now(UTC),
        last_assigned_at=datetime.now(UTC),
        # `_fresh_utilization()` 會呼叫它 —— 額度新不新鮮不是這組測試的題目，
        # 給一個值讓它走得過去就好。
        utilization=lambda window="five_hour": 0.3,
    )


def test_every_key_survives_the_response_model() -> None:
    produced = set(_account_view(_account()))
    declared = set(AccountView.model_fields)
    missing = produced - declared
    assert not missing, (
        f"這些 key 會被 pydantic 靜靜丟掉：{sorted(missing)}。"
        " 往 _account_view 加欄位時，AccountView 要一起加。"
    )


def test_last_assigned_at_actually_comes_through() -> None:
    """那個真的掉過的欄位，單獨再釘一次。"""
    a = _account()
    assert AccountView(**_account_view(a)).last_assigned_at == a.last_assigned_at


def test_never_assigned_is_none_not_missing() -> None:
    """None 是「還沒被派到過」，不是「不知道」—— 前端據此換文案。"""
    a = _account()
    a.last_assigned_at = None
    assert AccountView(**_account_view(a)).last_assigned_at is None


def test_the_token_still_never_leaves() -> None:
    """順手守住紅線 2：這張 view 不能有 token 的任何形式。"""
    produced = _account_view(_account())
    assert "oauth_token_enc" not in produced
    assert not any("token" in k and k != "has_token" for k in produced)
