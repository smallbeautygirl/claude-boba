"""登入與身分。Hub 不儲存密碼，只把登入請求轉給 Observ 換一次 token。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .. import notify, observ
from ..auth import is_admin, require_user
from ..config import settings
from ..db import get_session
from ..models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=200)


@router.post("/login")
async def login(body: LoginBody) -> dict:
    try:
        token = await observ.login(body.email, body.password)
    except observ.ObservError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {"token": token}


@router.get("/me")
async def me(user: User = Depends(require_user)) -> dict:
    admin = is_admin(user)
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "has_teams_webhook": bool(user.teams_webhook_url),
        # 沒設個人 webhook 的人到底收不收得到通知，取決於這個 env 有沒有設。
        # 前端要能講實話，就得知道這件事。
        "channel_notifications": bool(settings.teams_channel_webhook),
        "channel_name": settings.teams_channel_name,
        "is_admin": admin,
        # MinIO console 的網址。**只回給管理者** —— 那個 console 沒有「只看自己」
        # 的權限，登進去看得到所有人的對話與檔案。靠前端不渲染是不夠的：
        # 沒渲染不等於沒送出去，打開開發者工具就看得到。
        "s3_console_url": settings.s3_console_url if admin else "",
    }


class WebhookBody(BaseModel):
    # 空字串代表關掉通知。
    url: str = Field(default="", max_length=1024)


@router.put("/me/teams-webhook")
async def set_teams_webhook(
    body: WebhookBody,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """存下這個人的 Teams webhook，並立刻送一則測試訊息。

    立刻測試是刻意的：貼錯網址的人不會發現，只會以為「這工具不會通知」。
    """
    url = body.url.strip()
    user.teams_webhook_url = url or None
    await session.commit()
    if url:
        notify.test_message(url)
    return {"has_teams_webhook": bool(url)}
