"""登入與身分。Hub 不儲存密碼，只把登入請求轉給 Observ 換一次 token。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import notify, observ
from ..auth import is_admin, require_user
from ..config import settings
from ..db import get_session
from ..models import Job, LendingAccount, LendingSetting, User

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
async def me(
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
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
        # 下面兩個是**畫面條件**，不是權限：介紹頁與「換你了」要據此決定顯示什麼
        # （web-spec §13）。放在 me 而不是讓前端各自去撈，是因為它們問的是
        # 「這個人現在處於什麼狀態」—— `has_teams_webhook` 已經是同一類的先例。
        # 提交頁若為了決定一段文案要不要出現而去抓整份 job 清單，代價會落在
        # 「我額度爆了，很急」的那一頁上。
        "has_run_a_job": await _has_run_a_job(session, user),
        "is_lender": await _is_lender(session, user),
        # MinIO console 的網址。**只回給管理者** —— 那個 console 沒有「只看自己」
        # 的權限，登進去看得到所有人的對話與檔案。靠前端不渲染是不夠的：
        # 沒渲染不等於沒送出去，打開開發者工具就看得到。
        "s3_console_url": settings.s3_console_url if admin else "",
    }


async def _has_run_a_job(session: AsyncSession, user: User) -> bool:
    """他以委託者的身分丟過 job 沒有。

    **任何狀態都算**，包含失敗與取消的 —— 這個旗標要回答的是「他看過這東西怎麼跑」，
    而失敗的那一趟他一樣看過。
    """
    hit = await session.scalar(
        select(Job.id).where(Job.borrower_id == user.id).limit(1)
    )
    return hit is not None


async def _is_lender(session: AsyncSession, user: User) -> bool:
    """他是不是代跑者。

    判準是**至少有一個出借帳號**，不是有沒有出借設定：設定只是一組條件，
    沒有帳號的條件沒有借出任何東西（CONTEXT.md「是不是代跑者」）。
    """
    hit = await session.scalar(
        select(LendingAccount.id)
        .join(LendingSetting, LendingAccount.lending_id == LendingSetting.id)
        .where(LendingSetting.owner_user_id == user.id)
        .limit(1)
    )
    return hit is not None


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
