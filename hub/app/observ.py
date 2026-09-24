"""Observ（VisionAI）身分驗證。

沿用 lighthouse-saas-api 既有的做法，但複製過來而不依賴那個 monorepo
（SPEC.md §4.10、§4.11）。

⚠️ **兩個 service id header 的名稱不同，而且送錯只會回 401** —— 跟 token 錯誤
長得一模一樣，分不出來。所以名稱一律用下面的常數，不要在呼叫處寫字面值。
"""

from __future__ import annotations

import base64
import binascii
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from .config import settings

# /auth/users/me 只讀這一個。用 X-Service-Id 會 401。
HEADER_SERVICE_ID_AUTH = "x-request-service-id"
# apiserver/* 的資料端點讀這一個。目前沒用到，列出來是為了避免有人誤用上面那個。
HEADER_SERVICE_ID_OBSERV = "X-Service-Id"

# token → 身分 的快取。沒有它，每次 poll 與 SSE 重連都會打一次 Observ。
_TOKEN_TTL_SECONDS = 60.0


@dataclass(frozen=True)
class ObservUser:
    id: int
    email: str


_cache: dict[str, tuple[float, ObservUser]] = {}


class ObservError(Exception):
    """Observ 回了非 200。帶著原始狀態碼，讓呼叫端決定怎麼呈現。"""

    def __init__(self, status_code: int, detail: object) -> None:
        super().__init__(f"observ {status_code}")
        self.status_code = status_code
        self.detail = detail


async def login(email: str, password: str) -> str:
    """換取 Observ 的 access token。Hub 不儲存密碼，只轉交一次。"""
    async with httpx.AsyncClient(timeout=settings.observ_timeout) as client:
        resp = await client.post(
            f"{settings.observ_base_url}/apiserver/users/token",
            json={"email": email, "password": password},
        )
    if resp.status_code != 200:
        raise ObservError(resp.status_code, _detail(resp))
    data = resp.json()
    token = data.get("access") or data.get("access_token") or data.get("token")
    if not token:
        raise ObservError(502, "Observ 回應中沒有 access token")
    return str(token)


async def whoami(token: str) -> ObservUser:
    """問 Observ 這個 token 是誰。結果快取 60 秒。"""
    now = time.monotonic()
    if hit := _cache.get(token):
        expires_at, user = hit
        if expires_at > now:
            return user

    async with httpx.AsyncClient(timeout=settings.observ_timeout) as client:
        resp = await client.get(
            f"{settings.observ_base_url}/auth/users/me",
            headers={
                "Authorization": f"Bearer {token}",
                HEADER_SERVICE_ID_AUTH: settings.observ_service_id,
            },
        )
    if resp.status_code != 200:
        raise ObservError(resp.status_code, _detail(resp))

    body = resp.json()
    try:
        user = ObservUser(id=int(body["id"]), email=str(body["email"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ObservError(502, "Observ 回應缺少 id 或 email") from exc

    _cache[token] = (now + _TOKEN_TTL_SECONDS, user)
    return user


def token_expires_at(token: str) -> datetime | None:
    """讀 JWT 的 exp。**不驗簽章** —— 身分由 whoami 向 Observ 驗，這裡只回答「還剩多久」。

    「查 Observ 事件」把委託者登入的 token 派給 job 用（ADR-0002 的 2026-09-24 修訂）。
    job 排隊最多 15 分鐘、跑最多 10 分鐘；送出時剩不到一小時的 token 會讓 job 跑到
    一半 401，那是花了代跑者額度才失敗的那種，要在收單時擋。
    不是 JWT、或沒有 exp → None，呼叫端當「不知道」處理（不擋）。
    """
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        exp = claims.get("exp")
    except (ValueError, TypeError, binascii.Error):
        return None
    if not isinstance(exp, int | float):
        return None
    return datetime.fromtimestamp(exp, tz=UTC)


def _detail(resp: httpx.Response) -> object:
    try:
        return resp.json()
    except ValueError:
        return resp.text[:500]
