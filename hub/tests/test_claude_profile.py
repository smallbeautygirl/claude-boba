"""用代跑 token 反查 Claude 帳號的身分。

**端點與回應形狀是從 CLI binary 裡挖出來的**，不是猜的也不是二手文件
（`strings claude.exe`，2026-09-23）：

    async function Mxe(e){ let n = `${BASE_API_URL}/api/oauth/profile`;
      await lt.get(n, {headers:{Authorization:`Bearer ${e}`, ...}}) }

    account: { uuid, email }.passthrough()
    organization: { uuid }.passthrough()

**scope 過不過得了，在寫這組測試的當下還是未知。** 同一份 binary 裡寫著
「env-var and setup-token sessions default to user:inference only」，而我們的
token 正是 setup-token 產的。所以這個模組的每一條失敗路徑都跟成功路徑一樣重要：
拿不到身分是**預期中的一種結果**，不是例外。
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from app.claude_profile import PROFILE_PATH, Profile, fetch_profile

OK = {
    "account": {
        "uuid": "11111111-2222-3333-4444-555555555555",
        "email": "someone@example.com",
        "display_name": "Someone",
    },
    "organization": {
        "uuid": "99999999-0000-0000-0000-000000000000",
        "organization_type": "claude_max",
    },
}


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def call(token: str, handler):
    """這個 repo 沒有 pytest-asyncio，慣例是直接 asyncio.run（見
    test_model_allowlist.py）。為了一個模組多一個測試相依不划算。"""
    return asyncio.run(fetch_profile(token, client=_client(handler)))


def test_reads_the_identity() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == PROFILE_PATH
        assert request.headers["authorization"] == "Bearer sk-ant-oat01-xxx"
        return httpx.Response(200, json=OK)

    got = call("sk-ant-oat01-xxx", handler)
    assert got == Profile(
        account_uuid="11111111-2222-3333-4444-555555555555",
        email="someone@example.com",
        plan="max",
    )


def test_scope_refused_is_a_result_not_a_crash() -> None:
    """403 = setup-token 的 token 只有 user:inference。

    **這是預期中的一種結果。** 授權流程不能因此失敗 —— 他手上那組 token 是好的，
    只是我們問不出它是誰。
    """

    async def handler(_r):
        return httpx.Response(403, json={"error": {"message": "insufficient scope"}})

    assert call("sk-ant-oat01-xxx", handler) is None


@pytest.mark.parametrize("status", [401, 404, 429, 500])
def test_every_other_status_is_also_just_none(status: int) -> None:
    async def handler(_r):
        return httpx.Response(status, json={})

    assert call("t", handler) is None


def test_network_failure_is_none() -> None:
    async def handler(_r):
        raise httpx.ConnectError("boom")

    assert call("t", handler) is None


def test_a_200_with_the_wrong_shape_is_none() -> None:
    """形狀不對就當沒拿到。

    半個身分比沒有身分糟：uuid 是拿來當唯一鍵的，缺了它就不能去重，
    而一個只有 email 沒有 uuid 的紀錄看起來像「已經認出來了」。
    """

    async def handler(_r):
        return httpx.Response(200, json={"account": {"email": "x@y.z"}})

    assert call("t", handler) is None


def test_unknown_plan_is_none_but_identity_still_counts() -> None:
    """方案只是附註，認不出來不該連身分一起丟掉。"""

    async def handler(_r):
        return httpx.Response(
            200,
            json={
                "account": {"uuid": "u", "email": "e@x"},
                "organization": {"organization_type": "claude_something_new"},
            },
        )

    got = call("t", handler)
    assert got is not None and got.plan is None and got.email == "e@x"


def test_the_token_never_appears_in_the_repr() -> None:
    """紅線 2：這個模組拿著明文 token，它不能從任何地方漏出去。"""

    async def handler(_r):
        return httpx.Response(200, json=OK)

    got = call("sk-ant-oat01-SECRET", handler)
    assert "SECRET" not in repr(got)
