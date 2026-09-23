"""代跑者管理自己的**出借設定**與**出借帳號**。

一位代跑者恰好一份出借設定（條件），底下可以掛多個出借帳號（SPEC §4.12）。
條件屬於人，帳號屬於帳號 —— 上限 US$5 講的是他對風險的態度，不是對某個帳號的態度。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .. import authorize, secrets_box
from ..auth import require_user
from ..db import get_session
from ..models import Job, LendingAccount, LendingSetting, User, WorkerHost
from ..schemas import DEFAULT_MODELS, SITE_MODELS

router = APIRouter(prefix="/api/workers", tags=["lending"])

# 主機超過這個時間沒回報就算離線。worker 的 long-poll 是 30 秒一輪，
# 給兩輪的寬容度，避免網路抖一下就顯示離線。
OFFLINE_AFTER_SECONDS = 75

# 額度資料超過這個時間就不當成「現在」。代跑者自己在別的地方也在燒同一個帳號，
# 站台不會知道 —— 這時掛一個理直氣壯的 34% 比不顯示更糟（SPEC §11 spike #10）。
QUOTA_FRESH_SECONDS = 1800


def _light(util: float | None) -> str:
    """額度紅綠燈。

    只給燈號，不給百分比（docs/web-spec.md §3）：精確數字會讓委託者盤算
    「他還有 66%，再送一個沒差」，把人情變成資源計算。模糊剛好 ——
    足夠傳達分寸，不足以精算。
    """
    if util is None:
        return "unknown"
    if util < 0.6:
        return "green"
    return "yellow" if util < 0.85 else "red"


def _pool_utilization(accounts: list[LendingAccount]) -> float | None:
    """一個人的池子現在有多滿 —— 取**最充裕**的那個帳號。

    不取平均：個人帳號 10%、公司帳號 90% 平均出來的「🟡 快滿了」，
    兩個帳號都不是那個狀態。那顆燈回答的是「他的池子接不接得動」，
    而池子的能力由最有餘裕的帳號決定（web-spec §3）。
    """
    vals = [
        u for a in accounts if a.usable and (u := _fresh_utilization(a)) is not None
    ]
    return min(vals) if vals else None


def _fresh_utilization(a: LendingAccount, window: str = "five_hour") -> float | None:
    """過期的數字不算數。回 None 讓呼叫端知道「不知道」，而不是「很空」。"""
    if a.quota_updated_at is None:
        return None
    age = (datetime.now(UTC) - a.quota_updated_at).total_seconds()
    return a.utilization(window) if age <= QUOTA_FRESH_SECONDS else None


async def host_online(session: AsyncSession) -> bool:
    """領單主機在不在。

    託管模型下「線上」不再是每位代跑者各自的狀態 —— job 全跑在同一台機器上，
    那台沒跑起來的話，沒有人的額度借得出去。
    """
    seen = await session.scalar(select(func.max(WorkerHost.last_seen_at)))
    if seen is None:
        return False
    return (datetime.now(UTC) - seen).total_seconds() <= OFFLINE_AFTER_SECONDS


class LendingPatch(BaseModel):
    """出借條件。全部選填 —— 前端只送改動的那幾個。"""

    budget_usd: Decimal | None = Field(default=None, gt=0, le=100)
    available_models: list[str] | None = None
    allow_full_network: bool | None = None
    accepting: bool | None = None


class AuthorizeCode(BaseModel):
    code: str = Field(min_length=1, max_length=512)
    # 「這是誰的額度／誰批准的」。公司帳號**必填**由前端把關與提示，
    # 後端不驗證內容 —— 站台沒有辦法知道一個帳號是不是公司的（SPEC §4.12）。
    approver_note: str | None = Field(default=None, max_length=200)
    # 指定要換掉哪個帳號的 token。None = 新增一個帳號。
    account_id: uuid.UUID | None = None


class AccountView(BaseModel):
    """一個出借帳號。**沒有 token 欄位，也不會有**（security.md 紅線 2）。"""

    id: uuid.UUID
    name: str
    has_token: bool
    needs_reauth: bool
    approver_note: str | None
    # 本人看得到精確百分比與重置時間；委託者只有紅綠燈（web-spec §3 / §8）。
    windows: dict
    quota_updated_at: datetime | None
    quota_fresh: bool


class LendingView(BaseModel):
    """出借設定。**沒有 token 欄位，也不會有** —— 只回「有沒有」
    （security.md 紅線 2）。

    這幾支給了 response model 而不是回裸 dict，是為了讓契約能從
    /openapi.json 逐欄位比對。沒有型別的話，對照的人只能去讀原始碼 ——
    而「讀原始碼確認欄位名」正是今天一再出錯的那種驗證方式。
    """

    has_token: bool
    budget_usd: str
    available_models: list[str]
    allow_full_network: bool
    accepting: bool
    online: bool
    accounts: list[AccountView]


class AuthorizeStart(BaseModel):
    authorize_url: str


async def _my_lending(user: User, session: AsyncSession) -> LendingSetting:
    """取得（必要時建立）這個人的出借設定。

    託管模型下他沒有機器，所以沒有「新增一台」這個動作 —— 一個人就是一份條件。
    第一次打開那一頁就該看到預設條件，不是一個空清單加一顆「新增」。
    """
    row = await session.scalar(
        select(LendingSetting)
        .where(LendingSetting.owner_user_id == user.id)
        .options(selectinload(LendingSetting.accounts))
    )
    if row is None:
        row = LendingSetting(
            owner_user_id=user.id,
            available_models=list(DEFAULT_MODELS),
            # 還沒授權之前先不要接單 —— 接了也跑不動，只會讓委託者等。
            accepting=False,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row, ["accounts"])
    return row


def _account_view(a: LendingAccount) -> dict:
    return {
        "id": a.id,
        "name": a.name,
        "has_token": a.oauth_token_enc is not None,
        "needs_reauth": a.needs_reauth,
        "approver_note": a.approver_note,
        "windows": a.rate_limit_windows or {},
        "quota_updated_at": a.quota_updated_at,
        "quota_fresh": _fresh_utilization(a) is not None,
    }


def _lending_view(s: LendingSetting, *, online: bool) -> dict:
    """出借設定。**絕不包含 token**，連遮罩後的值都不行 ——
    只回 has_token 布林（security.md 紅線 2）。"""
    accounts = list(s.accounts)
    return {
        "has_token": any(a.usable for a in accounts),
        "budget_usd": str(s.job_budget_usd),
        "available_models": s.available_models or [],
        "allow_full_network": s.allow_full_network,
        "accepting": s.accepting,
        "online": online,
        "accounts": [_account_view(a) for a in accounts],
    }


@router.get("/settings", response_model=LendingView)
async def my_lending(
    user: User = Depends(require_user), session: AsyncSession = Depends(get_session)
) -> dict:
    """我的出借設定（單一物件）。

    託管模型下他沒有機器，他有的是一組條件 —— 所以這裡不是清單。
    帳號是清單，掛在 accounts 底下。
    """
    row = await _my_lending(user, session)
    return _lending_view(row, online=await host_online(session))


@router.put("/settings", response_model=LendingView)
async def update_lending(
    body: LendingPatch,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """改出借條件。

    **這三個控制項是「把額度借出去」讓人敢做的原因。** 舊模型下它們在代跑者
    自己機器的 worker/.env；託管模型下他沒有機器，不搬進來就直接消失了。
    一個大 model 的 job 跑八分鐘就可能燒掉 US$25 —— 那是他的額度。
    """
    row = await _my_lending(user, session)
    if body.budget_usd is not None:
        row.job_budget_usd = body.budget_usd
    if body.available_models is not None:
        bad = [m for m in body.available_models if m not in SITE_MODELS]
        if bad:
            raise HTTPException(400, f"這個站台不跑 {'、'.join(bad)}")
        if not body.available_models:
            raise HTTPException(400, "至少要開放一個 model，不然沒有人派得動你")
        row.available_models = body.available_models
    if body.allow_full_network is not None:
        row.allow_full_network = body.allow_full_network
    if body.accepting is not None:
        if body.accepting and not any(a.usable for a in row.accounts):
            raise HTTPException(400, "還沒授權，接單了也跑不動")
        row.accepting = body.accepting
    await session.commit()
    return _lending_view(row, online=await host_online(session))


@router.post("/authorize", response_model=AuthorizeStart)
async def start_authorize(
    user: User = Depends(require_user), session: AsyncSession = Depends(get_session)
) -> dict:
    """開始授權，回授權網址。

    代跑者去那個網址授權，拿到一個**一次性授權碼**貼回來（見 /authorize/code）。
    走這條而不是請他自己貼 token：一年期 token 從頭到尾不經過人的手。
    """
    row = await _my_lending(user, session)
    try:
        url = await authorize.start(row.id)
    except authorize.AuthorizeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"authorize_url": url}


@router.post("/authorize/code", response_model=LendingView)
async def submit_authorize_code(
    body: AuthorizeCode,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """把授權碼送回去，換到 token 並加密存起來。

    **token 不會出現在回應裡**，只回 has_token（security.md 紅線 2）。

    帶 account_id 就是換掉那個帳號的 token（舊的站台不再用，但它在 Claude 那邊
    仍然有效到期滿 —— 這句話由前端在按鈕旁講清楚）；不帶就是新增一個帳號。
    """
    row = await _my_lending(user, session)
    try:
        token = await authorize.submit_code(row.id, body.code)
    except authorize.AuthorizeError as exc:
        raise HTTPException(400, str(exc)) from exc

    # 授權之前有沒有任何能跑的帳號。用來判斷「接單」是不是被系統關掉的 ——
    # 見下面那段。
    had_usable = any(a.usable for a in row.accounts)

    if body.account_id is not None:
        account = next((a for a in row.accounts if a.id == body.account_id), None)
        if account is None:
            raise HTTPException(404, "找不到這個出借帳號")
    else:
        account = LendingAccount(
            lending_id=row.id,
            name=f"帳號 {len(row.accounts) + 1}",
        )
        session.add(account)
        row.accounts.append(account)

    # 拿到就馬上加密，明文不要在任何地方多待一行。
    account.oauth_token_enc = secrets_box.seal(token)
    del token
    account.needs_reauth = False
    if body.approver_note is not None:
        account.approver_note = body.approver_note.strip() or None

    # 最後一個帳號失效時，系統會自動把接單關掉（routers/worker.py）。那不是他按的，
    # 所以他重新授權好之後也不該要他自己再去按一次開回來 —— 授權成功了卻還是
    # 不接單，而畫面上沒有任何東西解釋為什麼（2026-09-22 真的發生過）。
    #
    # **只在「原本一個能跑的帳號都沒有」時才打開。** 他自己按的「暫停接單」
    # 不能被這裡蓋掉 —— 那是他的決定，而多授權一個帳號不代表他想恢復接單。
    if not had_usable and account.usable:
        row.accepting = True

    await session.commit()
    await session.refresh(row, ["accounts"])
    return _lending_view(row, online=await host_online(session))


class AccountPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    approver_note: str | None = Field(default=None, max_length=200)


@router.put("/accounts/{account_id}", response_model=LendingView)
async def update_account(
    account_id: uuid.UUID,
    body: AccountPatch,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """改帳號的名字或批准者備註。條件不在這裡改 —— 那是整份出借設定的事。"""
    row = await _my_lending(user, session)
    account = next((a for a in row.accounts if a.id == account_id), None)
    if account is None:
        raise HTTPException(404, "找不到這個出借帳號")
    if body.name is not None:
        account.name = body.name
    if body.approver_note is not None:
        account.approver_note = body.approver_note.strip() or None
    await session.commit()
    return _lending_view(row, online=await host_online(session))


@router.delete("/accounts/{account_id}", response_model=LendingView)
async def remove_account(
    account_id: uuid.UUID,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """移除一個**從來沒跑過 job** 的出借帳號。

    判定不是「現在沒有進行中的 job」—— 那會讓一個跑過 50 個 job 的帳號只要閒著
    就能被刪掉，而那 50 筆 job 的「由誰代跑」是人情債的依據。

    跑過 job 的帳號改成撤掉 token：它不再接單，紀錄留著。
    """
    row = await _my_lending(user, session)
    account = next((a for a in row.accounts if a.id == account_id), None)
    if account is None:
        raise HTTPException(404, "找不到這個出借帳號")

    used = await session.scalar(
        select(func.count()).select_from(Job).where(Job.account_id == account_id)
    )
    if used:
        account.oauth_token_enc = None
        account.needs_reauth = False
        account.rate_limit_windows = {}
        account.quota_updated_at = None
    else:
        row.accounts.remove(account)
        await session.delete(account)

    if not any(a.usable for a in row.accounts):
        # 最後一個能跑的帳號沒了就別再接單，否則 job 會排隊等一個不會來的人。
        row.accepting = False
    await session.commit()
    await session.refresh(row, ["accounts"])
    return _lending_view(row, online=await host_online(session))


@router.get("/lenders")
async def list_lenders(
    user: User = Depends(require_user), session: AsyncSession = Depends(get_session)
) -> list[dict]:
    """提交頁的代跑者下拉用。

    **一列一個人，不是一個帳號。** 委託者看不到帳號的存在 —— 他挑的是人，
    Hub 自己決定用哪個帳號跑（ADR-0001）。
    """
    rows = list(
        await session.scalars(
            select(LendingSetting).options(
                selectinload(LendingSetting.accounts),
                selectinload(LendingSetting.owner),
            )
        )
    )
    online = await host_online(session)

    counts: dict[uuid.UUID, int] = {}
    mine = [s.id for s in rows if s.owner_user_id == user.id]
    if mine:
        # 只算自己的 job 數 —— 別人的 job 數不該出現在別人的畫面上，
        # 那是「誰幫誰跑過幾次」的資訊，不是紅綠燈那種粗略分寸。
        counted = await session.execute(
            select(Job.lending_id, func.count())
            .where(Job.lending_id.in_(mine))
            .group_by(Job.lending_id)
        )
        counts = {lid: n for lid, n in counted}

    out = []
    for s in rows:
        accounts = list(s.accounts)
        usable = [a for a in accounts if a.usable]
        data = {
            "id": str(s.id),
            "name": s.owner.display_name if s.owner else "?",
            "owner": s.owner.display_name if s.owner else "?",
            "online": online and s.accepting and bool(usable),
            "accepting": s.accepting,
            "allow_full_network": s.allow_full_network,
            "available_models": s.available_models or [],
            "claude_code_version": next(
                (a.claude_code_version for a in usable if a.claude_code_version), None
            ),
            "quota": _light(_pool_utilization(accounts)),
        }
        if s.owner_user_id == user.id:
            # 只有本人看得到自己的精確數字（web-spec §3 的「不顯示百分比」
            # 只約束委託者看別人）。
            data |= {
                "job_count": counts.get(s.id, 0),
                "job_budget_usd": str(s.job_budget_usd),
                "accounts": [_account_view(a) for a in accounts],
            }
        out.append(data)
    return out


@router.post("/accepting")
async def set_accepting(
    accepting: bool,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """暫停/恢復接單。代跑者可能只是要專心工作，不是拒絕誰（web-spec §8）。

    這是整份出借設定的開關，不是某個帳號的 —— 單一帳號的停擺是 token 失效，
    那個由系統自己標（needs_reauth），不是他按的。
    """
    row = await _my_lending(user, session)
    if accepting and not any(a.usable for a in row.accounts):
        raise HTTPException(400, "還沒授權，接單了也跑不動")
    row.accepting = accepting
    await session.commit()
    return {"accepting": row.accepting}
