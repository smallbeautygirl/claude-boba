"""認證依賴。身分由 Observ 提供，我們只做對應與快取。"""

from __future__ import annotations

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import observ
from .config import settings
from .db import get_session
from .models import User

_bearer = HTTPBearer(auto_error=False)


async def upsert_user(session: AsyncSession, who: observ.ObservUser) -> User:
    user = await session.scalar(select(User).where(User.observ_user_id == who.id))
    if user is None:
        user = User(
            observ_user_id=who.id,
            email=who.email,
            # Observ 只給 id 與 email，沒有顯示名稱。用 email 的本地部分當預設，
            # 因為帳本和排行榜上要顯示的是「人」，不是一串 email。
            display_name=who.email.split("@")[0],
        )
        session.add(user)
    else:
        user.email = who.email
    await session.commit()
    await session.refresh(user)
    return user


def is_admin(user: User) -> bool:
    """這個人是不是站台管理者。

    判定只看 `ADMIN_EMAILS`，比對前兩邊都小寫化 + trim（見 config）。
    沒設就沒有人是 admin。
    """
    return user.email.strip().lower() in settings.admin_email_set


async def require_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> User:
    if creds is None:
        raise HTTPException(status_code=401, detail="請先登入")
    try:
        who = await observ.whoami(creds.credentials)
    except observ.ObservError as exc:
        # 把上游狀態碼原樣透出，不要一律轉成 401 ——
        # 502（Observ 掛了）與 401（token 過期）對使用者是不同的事。
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return await upsert_user(session, who)
