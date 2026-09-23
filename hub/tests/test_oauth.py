"""站台自己跑 Claude 的 OAuth 手動流程。SPEC §11 #13。

**為什麼不讓 CLI 做**：`claude setup-token` 是專為非互動設計的子指令，而
`claude login` 不是 —— 它啟動完整 TUI，要在裡面打 `/login` 再走選單。
驅動那個等於把授權流程綁在一個會改版的 UI 上，而這個 repo 已經被 TUI 擷取
咬過一次（§11 的截斷 token 事件，沉默了一整天）。

所需的常數全部來自 CLI binary（`strings claude.exe`），測試把它們釘住 ——
它們變了要有人知道，而不是在某次授權時才發現。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import urllib.parse as up

import httpx
import pytest
from app import oauth


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ── 授權網址 ──────────────────────────────────────────────────────


def test_authorize_url_carries_everything_the_exchange_will_need() -> None:
    start = oauth.begin()
    q = up.parse_qs(up.urlparse(start.url).query)
    assert q["client_id"] == [oauth.CLIENT_ID]
    assert q["response_type"] == ["code"]
    assert q["code_challenge_method"] == ["S256"]
    # 手動流程：授權碼會顯示在畫面上讓人複製，而不是導回 localhost ——
    # Hub 跑在別台機器上，localhost 那條在這裡沒有意義。
    assert q["redirect_uri"] == [oauth.MANUAL_REDIRECT_URL]
    assert q["code"] == ["true"]


def test_the_challenge_really_is_the_verifier_hashed() -> None:
    """S256：challenge = base64url(sha256(verifier))，去掉補位的 `=`。

    自己算錯的話，伺服器會在**交換**那一步才拒絕 —— 那時使用者已經去瀏覽器
    繞了一圈回來，而錯誤看起來像「授權碼無效」。
    """
    start = oauth.begin()
    q = up.parse_qs(up.urlparse(start.url).query)
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(start.verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    assert q["code_challenge"] == [expected]
    assert "=" not in q["code_challenge"][0]


def test_each_start_is_independent() -> None:
    """兩位代跑者可能同時在授權。共用 verifier 或 state 會讓其中一個失敗。"""
    a, b = oauth.begin(), oauth.begin()
    assert a.verifier != b.verifier and a.state != b.state


def test_scopes_include_the_one_that_was_missing() -> None:
    """`user:profile` 是走這條路的**理由** —— setup-token 沒有它，
    所以問不出帳號身分（§11 #12）。少了它整件事就白做了。"""
    q = up.parse_qs(up.urlparse(oauth.begin().url).query)
    assert "user:profile" in q["scope"][0].split()
    assert "user:inference" in q["scope"][0].split()


# ── 交換授權碼 ────────────────────────────────────────────────────

TOKENS = {
    "access_token": "sk-ant-oat01-" + "a" * 80,
    "refresh_token": "sk-ant-ort01-" + "b" * 80,
    "expires_in": 28800,
    "refresh_token_expires_in": 2592000,
    "scope": "user:inference user:profile",
    "account": {"uuid": "acc-uuid", "email_address": "someone@example.com"},
    "organization": {"uuid": "org-uuid"},
}


def test_exchange_returns_tokens_and_the_identity() -> None:
    """**交換的回應本身就帶身分** —— 這是走這條路而不是驅動 TUI 的額外好處：
    不用再打一支 `/api/oauth/profile`，也就不受它的 scope 限制。"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == oauth.TOKEN_PATH
        # 不帶 User-Agent 會被 Cloudflare 擋成 403 error code: 1010 ——
        # 那不是 OAuth 的拒絕，而且錯誤訊息完全不會提到 UA（2026-09-23 實測）。
        assert request.headers["user-agent"]
        body = json.loads(request.content)
        assert body["grant_type"] == "authorization_code"
        assert body["code_verifier"] and body["client_id"] == oauth.CLIENT_ID
        return httpx.Response(200, json=TOKENS)

    start = oauth.begin()
    got = asyncio.run(oauth.exchange(start, "the-code", client=_client(handler)))
    assert got.access_token == TOKENS["access_token"]
    assert got.refresh_token == TOKENS["refresh_token"]
    assert got.email == "someone@example.com"
    assert got.account_uuid == "acc-uuid"
    assert "user:profile" in got.scopes


def test_the_code_may_arrive_with_the_state_fragment_attached() -> None:
    """手動流程貼回來的是 `<code>#<state>`，使用者會整串複製 ——
    §11 的既有文案就是叫他「整串複製，包含 # 後面那一段」。"""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=TOKENS)

    start = oauth.begin()
    asyncio.run(
        oauth.exchange(start, f"the-code#{start.state}", client=_client(handler))
    )
    assert seen["code"] == "the-code"
    assert seen["state"] == start.state


def test_a_rejected_code_says_so_in_words() -> None:
    def handler(_r):
        return httpx.Response(400, json={"error": "invalid_grant"})

    with pytest.raises(oauth.OAuthError) as e:
        asyncio.run(oauth.exchange(oauth.begin(), "bad", client=_client(handler)))
    assert "授權碼" in str(e.value)


def test_cloudflare_is_not_reported_as_an_auth_failure() -> None:
    """403 + `error code: 1010` 是 Cloudflare 擋掉非瀏覽器請求，**不是授權失敗**。

    把它講成「授權碼無效」會讓人一直重貼一個其實沒問題的碼。
    """

    def handler(_r):
        return httpx.Response(403, text="error code: 1010")

    with pytest.raises(oauth.OAuthError) as e:
        asyncio.run(oauth.exchange(oauth.begin(), "c", client=_client(handler)))
    msg = str(e.value)
    # 斷言的是**意思**不是字面：訊息裡出現「授權碼」是可以的（「不是授權碼的
    # 問題」正是要講的話），不能出現的是「再走一次授權」那種指示 ——
    # 重走一次會作廢他手上那組還沒用掉的碼，而問題根本不在那裡。
    assert "重新走一次" not in msg and "不被接受" not in msg
    assert "稍後" in msg


def test_rate_limited_says_wait() -> None:
    def handler(_r):
        return httpx.Response(429, json={"error": {"type": "rate_limit_error"}})

    with pytest.raises(oauth.OAuthError) as e:
        asyncio.run(oauth.exchange(oauth.begin(), "c", client=_client(handler)))
    assert "稍後" in str(e.value)


# ── refresh ──────────────────────────────────────────────────────


def test_refresh_keeps_the_old_refresh_token_when_none_comes_back() -> None:
    """實測是滾動式的，但**不能假設**：沒回傳就沿用舊的，這也是 CLI 的行為。

    假設它一定會換，而某次沒換的話，我們會把 refresh token 存成 None ——
    那個帳號就再也 refresh 不了，而且要到八小時後才會被發現。
    """

    def handler(_r):
        return httpx.Response(
            200, json={k: v for k, v in TOKENS.items() if k != "refresh_token"}
        )

    got = asyncio.run(oauth.refresh("old-refresh", client=_client(handler)))
    assert got.refresh_token == "old-refresh"


def test_refresh_takes_the_new_refresh_token_when_it_rolls() -> None:
    def handler(_r):
        return httpx.Response(200, json=TOKENS)

    got = asyncio.run(oauth.refresh("old-refresh", client=_client(handler)))
    assert got.refresh_token == TOKENS["refresh_token"]


def test_a_dead_refresh_token_is_distinguishable() -> None:
    """refresh token 死掉要跟「暫時打不到」分開 —— 前者要代跑者重新授權，
    後者等一下就好。混在一起會把人叫去做一件不需要做的事。"""

    def handler(_r):
        return httpx.Response(400, json={"error": "invalid_grant"})

    with pytest.raises(oauth.ReauthorizeNeeded):
        asyncio.run(oauth.refresh("dead", client=_client(handler)))


def test_a_network_blip_is_not_reauthorize() -> None:
    def handler(_r):
        raise httpx.ConnectError("boom")

    with pytest.raises(oauth.OAuthError) as e:
        asyncio.run(oauth.refresh("fine", client=_client(handler)))
    assert not isinstance(e.value, oauth.ReauthorizeNeeded)
