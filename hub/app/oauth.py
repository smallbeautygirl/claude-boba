"""站台自己跑 Claude 的 OAuth 手動流程。SPEC.md §11 #13。

## 為什麼不讓 CLI 做

`claude setup-token` 是**專為非互動設計的子指令** —— 那就是 `app/authorize.py`
那條 pty 路線能成立的原因。`claude login` 不是：它啟動完整 TUI，然後在裡面說
`Not logged in · Run /login`（2026-09-23 實測，連拆三道關卡仍到不了授權網址）。
要走它就得在 TUI 裡打 `/login` 再走選單 —— 把授權流程綁在一個會改版的 UI 上，
而這個 repo 已經被 TUI 擷取咬過一次（§11 的截斷 token 事件，沉默了一整天）。

## 為什麼值得自己走一趟 OAuth

`setup-token` 的 token 只有 `user:inference`，問不出帳號身分（§11 #12 實測 403）。
自己跑就能要 `user:profile`，而且**交換授權碼的回應本身就帶 `account`** ——
身分不用再打一支端點，也就不受那支端點的 scope 限制。

## 常數全部來自 CLI binary

`strings claude.exe`（2.1.278）。它們是**觀察到的**，不是文件承諾的 ——
Anthropic 改了不會通知我們，所以測試把它們釘住。

## Cloudflare：User-Agent 不只要帶，還要帶**對的那一串**

`POST /v1/oauth/token` 前面有一層 Cloudflare 規則看 UA：

| UA | 結果 |
|---|---|
| 不帶 | `403 error code: 1010` |
| `claude-code/2.1.278`（我們自己編的，2026-09-23 早上到下午都在用） | **`429 rate_limit_error`，每一發都是，跟 IP 無關** |
| 瀏覽器的 UA | 同上 429 |
| `claude-cli/2.1.278 (external, cli)`（CLI 真正送的，binary 裡 `X0()` 組的） | 到得了後端 |
| `axios/1.7.9` | 到得了後端 |

分辨方法：被 Cloudflare 擋的回應**沒有 `request-id`、沒有 `cf-cache-status`**，
Anthropic 後端回的才有。429 的 body 長得跟 API 的限流一模一樣，光看 body 分不出。

**2026-09-23 整天的「限流」就是這一列。** 當時判斷成「綁出口 IP 的節流」，
還為此加了下面的熔斷、寫進 SPEC 與 production README —— 直到有人從家裡網路和
VPN 打同一個請求也拿到 429，才回頭去比 UA。教訓：**看 header 不要只看 status**，
而且 CLI 的常數要從 binary 逐字抄，不要憑印象寫一個像的。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import random
import secrets
import time
import urllib.parse as up
from dataclasses import dataclass, field

import httpx

CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
AUTHORIZE_URL = "https://claude.com/cai/oauth/authorize"
TOKEN_BASE = "https://platform.claude.com"
TOKEN_PATH = "/v1/oauth/token"
REVOKE_PATH = "/v1/oauth/token/revoke"
# 手動流程：授權碼顯示在畫面上讓人複製，不導回 localhost。
# Hub 跑在別台機器上，localhost 那條在這裡沒有意義。
MANUAL_REDIRECT_URL = "https://platform.claude.com/oauth/code/callback"
# CLI 自己在用的 UA，**逐字**抄自 binary（`claude-cli/${VERSION} (external, cli)`）。
# 帶什麼很重要：帶錯的那串會被 Cloudflare 一律回 429（見上面那段），跟 IP 無關。
USER_AGENT = "claude-cli/2.1.278 (external, cli)"

# `user:profile` 是走這條路的理由，不是附加品。其餘照 CLI 登入時要的那組 ——
# 少要一個，那個功能在 job 裡就會安靜地不能用。
SCOPES = (
    "user:inference",
    "user:profile",
    "user:sessions:claude_code",
    "user:mcp_servers",
    "user:plugins",
    "user:file_upload",
)

_TIMEOUT = 30.0

# 429 的重試。**這是站台該替使用者吃下來的錯**：他手上拿著一串會過期的一次性碼，
# 而「稍後再試」對他來說是去重走一次授權 —— 那正是這個流程最貴的一步。
#
# 交換授權碼時退避得短（人在畫面前等），refresh 時可以長一點（背景在跑）。
# 加抖動是因為多位代跑者的 token 會在相近的時間到期，一起醒來只會把限流撞得更死。
_RETRY_BACKOFF = (1.5, 4.0)

# 連續被 429 之後，**整個站台停手一段時間**。
#
# 這段是 2026-09-23 在誤判「限流綁這台機器的 IP」時加的（真正的原因是 UA，
# 見模組 docstring）。留著，理由變了：真的限流若有一天出現，Hub 替每位代跑者
# 每 8 小時 refresh 一次，多位到期時間相近的帳號一起醒來重試，只會把洞挖得更深。
# 冷卻期間直接拒絕，連打都不打。
_COOLDOWN_AFTER = 2
_COOLDOWN_SECONDS = 15 * 60
_throttled_until = 0.0
_consecutive_429 = 0


def _cooldown_left() -> int:
    return max(0, int(_throttled_until - time.monotonic()))


def _note_429() -> None:
    global _consecutive_429, _throttled_until
    _consecutive_429 += 1
    if _consecutive_429 >= _COOLDOWN_AFTER:
        _throttled_until = time.monotonic() + _COOLDOWN_SECONDS


def _note_ok() -> None:
    global _consecutive_429, _throttled_until
    _consecutive_429 = 0
    _throttled_until = 0.0


def _reset_for_tests() -> None:
    _note_ok()


class OAuthError(RuntimeError):
    """授權流程失敗。訊息是給人看的，會直接顯示在出借頁上。"""


class ReauthorizeNeeded(OAuthError):
    """refresh token 死了 —— 只有代跑者重新授權才救得回來。

    跟「暫時打不到」分開是刻意的：混在一起會把人叫去重跑一次其實不必要的授權，
    而那會**作廢他現在還能用的憑證**。
    """


@dataclass(frozen=True)
class Start:
    """一次授權的開頭。`verifier` 與 `state` 要留到交換那一步。"""

    url: str
    verifier: str = field(repr=False)
    state: str


@dataclass(frozen=True)
class Tokens:
    """交換或 refresh 的成果。**`repr` 不含任何 token**（security.md 紅線 2）。"""

    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_in: int
    refresh_expires_in: int | None
    scopes: tuple[str, ...]
    # 交換時一定有；refresh 時**不一定**（CLI 的程式對它也是可有可無）。
    account_uuid: str | None = None
    email: str | None = None


def begin() -> Start:
    """產生授權網址與 PKCE 的 verifier。

    每次都是新的 verifier 與 state —— 兩位代跑者可能同時在授權，
    共用的話其中一個一定會失敗。
    """
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    state = secrets.token_urlsafe(24)
    query = up.urlencode(
        {
            "code": "true",
            "client_id": CLIENT_ID,
            "response_type": "code",
            "redirect_uri": MANUAL_REDIRECT_URL,
            "scope": " ".join(SCOPES),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
    )
    return Start(url=f"{AUTHORIZE_URL}?{query}", verifier=verifier, state=state)


async def exchange(
    start: Start,
    code: str,
    *,
    client: httpx.AsyncClient | None = None,
    retries: int | None = None,
) -> Tokens:
    """把授權碼換成 token。

    貼回來的碼常常是 `<code>#<state>` —— 手動流程的頁面就是那樣顯示的，
    而站台的文案本來就叫他「整串複製，包含 # 後面那一段」。這裡自己拆開，
    不要求使用者動手。
    """
    raw, _, tail = code.strip().partition("#")
    payload = {
        "grant_type": "authorization_code",
        "code": raw,
        "redirect_uri": MANUAL_REDIRECT_URL,
        "client_id": CLIENT_ID,
        "code_verifier": start.verifier,
        "state": tail or start.state,
    }
    data = await _post(
        payload,
        client,
        on_invalid_grant="這串授權碼不被接受",
        **({} if retries is None else {"retries": retries}),
    )
    return _tokens(data, fallback_refresh=None)


async def refresh(
    refresh_token: str,
    *,
    client: httpx.AsyncClient | None = None,
    retries: int | None = None,
) -> Tokens:
    """用 refresh token 換一張新的 access token。

    實測是滾動式的（會換一張新的 refresh token，而且效期往後推，§11 #13），
    但**沒回傳就沿用舊的** —— 假設它一定會換，而某次沒換的話我們會把
    refresh token 存成空的，那個帳號就再也 refresh 不了，且要八小時後才發現。
    """
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": CLIENT_ID,
        "scope": " ".join(SCOPES),
    }
    data = await _post(
        payload,
        client,
        reauthorize_on_invalid_grant=True,
        **({} if retries is None else {"retries": retries}),
    )
    return _tokens(data, fallback_refresh=refresh_token)


async def revoke(
    refresh_token: str, *, client: httpx.AsyncClient | None = None
) -> bool:
    """撤銷一組 refresh token。回「有沒有成功」，不拋。

    **這是義務不是選項**（security.md 紅線 2）。在這之前，按「不再出借這個帳號」
    只是刪掉我們手上那一份 —— 那組憑證在 Anthropic 那邊照樣有效到期滿。
    代跑者以為自己收回了額度，其實沒有。

    失敗不擋流程：他要的是「別再用我的額度」，而站台這邊停止使用是立刻生效的。
    撤不掉時該讓他知道，但不該因此不讓他停借。
    """
    owned = client is None
    c = client or httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        resp = await c.post(
            TOKEN_BASE + REVOKE_PATH,
            content=json.dumps(
                {
                    "token": refresh_token,
                    "token_type_hint": "refresh_token",
                    "client_id": CLIENT_ID,
                }
            ),
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        )
        return resp.status_code < 400
    except httpx.HTTPError:
        return False
    finally:
        if owned:
            await c.aclose()


async def _post(
    payload: dict,
    client: httpx.AsyncClient | None,
    *,
    on_invalid_grant: str = "",
    reauthorize_on_invalid_grant: bool = False,
    retries: int = len(_RETRY_BACKOFF),
) -> dict:
    if (left := _cooldown_left()) > 0:
        raise OAuthError(
            f"Anthropic 對這個流程限流了，站台先停手 {left // 60 + 1} 分鐘再試 —— "
            "現在一直按只會讓它更久。那串授權碼還沒有被用掉"
        )

    owned = client is None
    c = client or httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        for attempt in range(retries + 1):
            try:
                resp = await c.post(
                    TOKEN_BASE + TOKEN_PATH,
                    content=json.dumps(payload),
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": USER_AGENT,
                    },
                )
            except httpx.HTTPError as exc:
                # **不是 ReauthorizeNeeded。** 連不上跟憑證死掉是兩件事，而把
                # 前者講成後者，會叫人去重跑一次授權並作廢他還能用的憑證。
                raise OAuthError(
                    f"連不到 Anthropic（{type(exc).__name__}），等一下再試"
                ) from exc
            # **只重試 429。** 其他錯誤重試沒有意義，只會讓人多等好幾秒
            # 才看到一個本來就確定的失敗。
            if resp.status_code != 429 or attempt == retries:
                break
            await asyncio.sleep(_RETRY_BACKOFF[attempt] * (1 + random.random() * 0.4))
    finally:
        if owned:
            await c.aclose()

    if resp.status_code == 429:
        _note_429()
    else:
        _note_ok()

    if resp.status_code == 200:
        try:
            return resp.json()
        except ValueError as exc:
            raise OAuthError("Anthropic 回了 200，但內容不是 JSON") from exc

    if resp.status_code == 429:
        # 重試過了還是 429。**要講「那串碼還能用」** —— 429 是在伺服器處理之前
        # 就被擋下，授權碼沒有被消耗掉。不講的話他會去重走一次授權，
        # 而那是這整個流程最貴的一步（還會作廢他手上那組）。
        left = _cooldown_left()
        raise OAuthError(
            "Anthropic 對這個流程限流了。"
            + (
                f"站台先停手 {left // 60 + 1} 分鐘 —— 現在一直按只會讓它更久。"
                if left
                else "等一兩分鐘再按一次「完成授權」就好。"
            )
            + "那串授權碼還沒有被用掉，不用重拿"
        )
    if resp.status_code == 403 and "1010" in resp.text:
        # 這條要跟授權失敗分開講，否則使用者會一直重貼一個其實沒問題的碼。
        raise OAuthError("被 Anthropic 的前端防護擋下（不是授權碼的問題），稍後再試")

    if _is_invalid_grant(resp):
        if reauthorize_on_invalid_grant:
            raise ReauthorizeNeeded("這個帳號的授權已經失效，要重新授權一次")
        raise OAuthError(f"{on_invalid_grant or '授權失敗'} —— 請重新走一次授權")
    raise OAuthError(f"Anthropic 回了 HTTP {resp.status_code}")


def _is_invalid_grant(resp: httpx.Response) -> bool:
    try:
        err = resp.json().get("error")
    except ValueError:
        return False
    if isinstance(err, dict):
        err = err.get("type")
    return err in ("invalid_grant", "invalid_request")


def _tokens(data: dict, *, fallback_refresh: str | None) -> Tokens:
    access = data.get("access_token")
    if not isinstance(access, str) or not access:
        raise OAuthError("Anthropic 的回應裡沒有 access token")
    account = data.get("account") if isinstance(data.get("account"), dict) else {}
    return Tokens(
        access_token=access,
        refresh_token=data.get("refresh_token") or fallback_refresh or "",
        expires_in=int(data.get("expires_in") or 0),
        refresh_expires_in=(
            int(data["refresh_token_expires_in"])
            if isinstance(data.get("refresh_token_expires_in"), int)
            else None
        ),
        scopes=tuple((data.get("scope") or "").split()),
        account_uuid=account.get("uuid"),
        # 交換的回應用 `email_address`（CLI 的 `tokenAccount` 就是讀這個名字），
        # 而 /api/oauth/profile 用 `email`。兩邊都收。
        email=account.get("email_address") or account.get("email"),
    )
