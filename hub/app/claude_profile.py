"""用代跑 token 反查那個 Claude 帳號是誰。

**端點與回應形狀是從 CLI binary 裡挖出來的**（`strings claude.exe`，2026-09-23）——
不是猜的，也不是二手文件。Claude Code 自己就在打這一支：

    async function Mxe(e){ let n = `${BASE_API_URL}/api/oauth/profile`;
      await lt.get(n, {headers:{Authorization:`Bearer ${e}`, ...}}) }

它自己的回應驗證器長這樣（`.passthrough()`，所以還有別的欄位）：

    account: { uuid, email }
    organization: { uuid }

## 為什麼站台需要這個

代跑者可以出借多個帳號（ADR-0001），而在這之前**站台從頭到尾沒看過帳號的身分** ——
名字是他自己打的一串字，同一個 Claude 帳號授權兩次會變成兩個看起來不相干的帳號。
`account.uuid` 是拿來去重的鍵（email 會改，uuid 不會）；email 是拿給他看的。

## 拿不到是預期中的結果，不是例外

同一份 binary 裡寫著 `env-var and setup-token sessions default to user:inference
only`，而我們的 token 正是 `claude setup-token` 產的。所以 `/api/oauth/profile`
很可能回 403。**那不能讓授權失敗** —— 他手上那組 token 是好的，只是我們問不出
它是誰。每一條失敗路徑都回 `None`，呼叫端當作「這個帳號沒有身分」處理。

## 紅線 2

這個模組會拿到明文 token。它**只出現在 Authorization header 裡** ——
不進 log、不進例外訊息、不進回傳值。`Profile` 上沒有任何欄位放得下它。
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from .config import settings

BASE_URL = "https://api.anthropic.com"
PROFILE_PATH = "/api/oauth/profile"
_TIMEOUT = 10.0

# CLI 自己的對照表（binary 裡的 `Iye`）。認不出來的方案回 None ——
# 方案只是附註，不該因為 Anthropic 多開一個等級就讓整個身分作廢。
_PLANS: dict[str, str] = {
    "claude_max": "max",
    "claude_pro": "pro",
    "claude_enterprise": "enterprise",
    "claude_team": "team",
}


@dataclass(frozen=True)
class Lookup:
    """一次反查的結果。**拿不到的時候，為什麼拿不到才是重點。**

    原本只回 `Profile | None`，理由寫在 log 裡 —— 而這個專案**從來沒有設定過
    logging**，所以那行 `logger.info` 一次都沒有被輸出過。一個「有記錄但沒人
    看得到」的理由等於沒有理由，而使用者看到的是一句「Anthropic 不給」，
    分不出那是 scope 不夠、逾時、還是對方掛了。

    `reason` 是給人看的短句，不是錯誤碼 —— 它會直接出現在出借頁上。
    """

    profile: Profile | None
    reason: str | None = None


@dataclass(frozen=True)
class Profile:
    """一個 Claude 帳號的身分。**不含 token，也不會有。**"""

    account_uuid: str
    email: str
    # 認不出來就是 None。它只是拿來顯示的。
    plan: str | None


async def fetch_profile(
    token: str, *, client: httpx.AsyncClient | None = None
) -> Lookup:
    """問 Anthropic 這組 token 屬於誰。

    **永遠回 `Lookup`，不拋例外。** 拿不到是預期中的一種結果，而且那時
    `reason` 一定有值 —— 呼叫端要能把它顯示出來。

    `client` 只給測試注入 `MockTransport` 用 —— 正式路徑自己開一個。
    """
    owned = client is None
    c = client or httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        resp = await c.get(
            BASE_URL + PROFILE_PATH,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Cache-Control": "no-cache",
            },
        )
    except httpx.HTTPError as exc:
        # 只講類別，不講訊息 —— httpx 的例外訊息會帶 URL，而我們不冒那個險。
        return Lookup(None, f"連不到 Anthropic（{type(exc).__name__}）")
    finally:
        if owned:
            await c.aclose()

    if resp.status_code != 200:
        # **不要把回應內容放進 reason** —— 那是帳號資料，而 reason 會顯示在畫面上。
        # 只有狀態碼，加上一句人話。403 幾乎一定是 scope：`claude setup-token`
        # 產的 token 只有 `user:inference`（CLI binary 自己這樣寫）。
        hint = {
            401: "這組 token 不被接受",
            403: "這組 token 的授權範圍不含帳號資訊",
            429: "被限流了，等一下再試",
        }.get(resp.status_code, "Anthropic 回了非 200")
        return Lookup(None, f"{hint}（HTTP {resp.status_code}）")

    return _parse(resp)


def _parse(resp: httpx.Response) -> Lookup:
    _BAD_SHAPE = "Anthropic 回了 200，但內容不是預期的格式"
    try:
        data = resp.json()
    except ValueError:
        return Lookup(None, "Anthropic 回了 200，但內容不是 JSON")
    if not isinstance(data, dict):
        return Lookup(None, _BAD_SHAPE)

    account = data.get("account")
    if not isinstance(account, dict):
        return Lookup(None, _BAD_SHAPE)
    uuid_, email = account.get("uuid"), account.get("email")
    # **兩個都要有。** 半個身分比沒有身分糟：uuid 是去重的鍵，缺了它就去不了重，
    # 而一筆只有 email 的紀錄看起來像「已經認出來了」。
    if (
        not isinstance(uuid_, str)
        or not isinstance(email, str)
        or not uuid_
        or not email
    ):
        return Lookup(None, _BAD_SHAPE)

    org = data.get("organization")
    org_type = org.get("organization_type") if isinstance(org, dict) else None
    return Lookup(
        Profile(
            account_uuid=uuid_,
            email=email,
            plan=_PLANS.get(org_type) if isinstance(org_type, str) else None,
        )
    )


def enabled() -> bool:
    """關得掉。反查會對外送出一個請求，而那不是這個站台的核心功能 ——
    Anthropic 那邊改了什麼的時候，要能一行關掉而不是等著改程式。"""
    return settings.claude_profile_lookup
