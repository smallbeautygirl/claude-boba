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

import logging
from dataclasses import dataclass

import httpx

from .config import settings

logger = logging.getLogger(__name__)

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
class Profile:
    """一個 Claude 帳號的身分。**不含 token，也不會有。**"""

    account_uuid: str
    email: str
    # 認不出來就是 None。它只是拿來顯示的。
    plan: str | None


async def fetch_profile(
    token: str, *, client: httpx.AsyncClient | None = None
) -> Profile | None:
    """問 Anthropic 這組 token 屬於誰。拿不到一律回 `None`。

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
        # 只記類別，不記訊息 —— httpx 的例外訊息會帶 URL，而我們不冒那個險。
        logger.info("查不到 Claude 帳號身分：%s", type(exc).__name__)
        return None
    finally:
        if owned:
            await c.aclose()

    if resp.status_code != 200:
        # 403 最可能是 scope 不夠（setup-token 只有 user:inference）。
        # 這一行是之後要拿來查「為什麼帳號卡上沒有 email」的唯一線索，
        # 所以狀態碼要留著 —— 但**不要記回應內容**，那是別人的帳號資料。
        logger.info("查不到 Claude 帳號身分：HTTP %s", resp.status_code)
        return None

    return _parse(resp)


def _parse(resp: httpx.Response) -> Profile | None:
    try:
        data = resp.json()
    except ValueError:
        logger.info("查不到 Claude 帳號身分：回應不是 JSON")
        return None
    if not isinstance(data, dict):
        return None

    account = data.get("account")
    if not isinstance(account, dict):
        return None
    uuid_, email = account.get("uuid"), account.get("email")
    # **兩個都要有。** 半個身分比沒有身分糟：uuid 是去重的鍵，缺了它就去不了重，
    # 而一筆只有 email 的紀錄看起來像「已經認出來了」。
    if (
        not isinstance(uuid_, str)
        or not isinstance(email, str)
        or not uuid_
        or not email
    ):
        logger.info("查不到 Claude 帳號身分：回應少了 account.uuid 或 email")
        return None

    org = data.get("organization")
    org_type = org.get("organization_type") if isinstance(org, dict) else None
    return Profile(
        account_uuid=uuid_,
        email=email,
        plan=_PLANS.get(org_type) if isinstance(org_type, str) else None,
    )


def enabled() -> bool:
    """關得掉。反查會對外送出一個請求，而那不是這個站台的核心功能 ——
    Anthropic 那邊改了什麼的時候，要能一行關掉而不是等著改程式。"""
    return settings.claude_profile_lookup
