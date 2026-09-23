"""派單前確保手上那張 access token 還活著。SPEC.md §11 #13。

## 為什麼需要這一層

`setup_token` 那種一年期、拿了就用。**`oauth` 那種八小時就死** —— 而 job 可能
在授權之後很久才被派出去。少了這一層，代跑者會在授權滿八小時之後突然「不能跑」，
而錯誤長得像帳號出問題。

## 交出去的是 access token，不是換票的能力

refresh token **絕不離開 Hub**（security.md 紅線 2）。這個模組是唯一會碰到它的
地方之一，而它回傳的永遠只有 access token。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from . import oauth, secrets_box
from .enums import CredentialKind
from .models import LendingAccount

logger = logging.getLogger(__name__)

# 提前多久換。CLI 自己用五分鐘，我們用十五 —— job 最長跑十分鐘，而**判斷是在
# 派單那一刻做的**：卡在五分鐘的話，一個剛好還有六分鐘的 token 會在 job 跑到
# 一半時死掉，而那時沒有人能救它。
RENEW_MARGIN = timedelta(minutes=15)


def needs_renewal(account: LendingAccount, *, now: datetime | None = None) -> bool:
    """這張 access token 是不是該換了。

    `setup_token` 永遠回 False —— 它沒有 refresh token，也沒有到期時間可看。
    不知道到期時間的 `oauth` 帳號也回 False：那是資料有問題，而**在派單路徑上
    亂猜比放行更糟** —— 放行最多失敗一個 job，亂猜會把一個好帳號停掉。
    """
    if account.credential_kind is not CredentialKind.OAUTH:
        return False
    if account.access_expires_at is None or account.refresh_token_enc is None:
        return False
    return (now or datetime.now(UTC)) + RENEW_MARGIN >= account.access_expires_at


async def access_token(account: LendingAccount, session: AsyncSession) -> str | None:
    """拿這個帳號現在可用的 access token；需要就先換一張。

    換不到回 `None`，而且**該停的時候把帳號停掉** —— refresh token 死了只有
    代跑者重新授權救得回來，繼續派給他只是讓每個 job 都失敗一次。

    暫時打不到 Anthropic 則**不停帳號**：那是網路問題，下一輪就好了。
    把它當成憑證死掉，會因為一次逾時就叫人重跑授權、作廢他還能用的憑證。
    """
    if account.oauth_token_enc is None:
        return None
    if not needs_renewal(account):
        return secrets_box.open_(account.oauth_token_enc)

    try:
        tokens = await oauth.refresh(secrets_box.open_(account.refresh_token_enc))
    except oauth.ReauthorizeNeeded:
        logger.info("出借帳號需要重新授權（refresh token 失效）")
        account.needs_reauth = True
        await session.commit()
        return None
    except oauth.OAuthError as exc:
        # 不標 needs_reauth：連不到跟死掉是兩件事。手上那張可能還有幾分鐘，
        # 讓它去跑 —— 最壞的情況是這一個 job 失敗，而不是這個帳號被停掉。
        logger.info("refresh 失敗，沿用現有的 access token：%s", exc)
        return secrets_box.open_(account.oauth_token_enc)

    now = datetime.now(UTC)
    account.oauth_token_enc = secrets_box.seal(tokens.access_token)
    account.refresh_token_enc = secrets_box.seal(tokens.refresh_token)
    account.access_expires_at = now + timedelta(seconds=tokens.expires_in)
    if tokens.refresh_expires_in:
        account.refresh_expires_at = now + timedelta(seconds=tokens.refresh_expires_in)
    if tokens.scopes:
        account.scopes = list(tokens.scopes)
    account.needs_reauth = False
    await session.commit()
    return tokens.access_token
