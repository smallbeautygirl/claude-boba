"""借用者面向的 API。身分由 Observ 提供（SPEC.md §4.10）。"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .. import events, notify, observ, secrets_box, storage
from ..auth import require_user
from ..config import settings
from ..db import get_session
from ..dispatch import auto_candidates, follow_up_target, only_me
from ..enums import JobStatus, SourceType
from ..failures import classify
from ..models import Artifact, Job, JobEvent, LendingAccount, LendingSetting, User
from ..pricing import job_creates_debt, label_for
from ..schemas import (
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENTS_TOTAL_BYTES,
    MAX_JOB_INPUT_BYTES,
    MAX_TRANSCRIPT_BYTES,
    SITE_MODELS,
    TRANSCRIPT_SNIFF_BYTES,
    TRANSCRIPT_SNIFF_LINES,
    FollowUp,
    JobAttachment,
    JobCreate,
    JobDetail,
    JobSummary,
    StopJob,
    needs_observ_token,
)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

# SSE 心跳。沒有它，中介的 proxy 會把閒置連線切掉，而 job 可能十分鐘沒有事件。
_HEARTBEAT_SECONDS = 15.0

_LOAD = (
    selectinload(Job.borrower),
    selectinload(Job.lending).selectinload(LendingSetting.owner),
    # 代跑者自己要看得到這一趟燒的是他哪一個帳號（web-spec §8）。
    # 預先載入是必要的：async session 裡 lazy load 會觸發同步 IO 並炸掉。
    selectinload(Job.account),
)


_PREVIEW_CHARS = 90


def _preview(prompt: str) -> str:
    """列表用的摘要。取第一行，太長就截斷。

    貼上整段對話的 job，第一行通常就是最能認出它的那一句。
    """
    first = next((ln.strip() for ln in prompt.splitlines() if ln.strip()), "")
    return first[:_PREVIEW_CHARS] + ("…" if len(first) > _PREVIEW_CHARS else "")


def _can_stop(job: Job, user: User) -> bool:
    return (
        job.status in (JobStatus.CLAIMED, JobStatus.RUNNING)
        and job.lending is not None
        and job.lending.owner_user_id == user.id
    )


def _detail(job: Job, user: User | None = None) -> JobDetail:
    lender_id = job.lending.owner_user_id if job.lending else None
    # 自己跑自己不掛債（SPEC §4.5）。掛債的判斷只有 pricing.job_creates_debt
    # 一個入口 —— 這裡跟著它走，否則畫面會講一個跟帳本不一樣的故事。
    self_run = lender_id is not None and lender_id == job.borrower_id
    debt = None
    if (
        lender_id is not None
        and job_creates_debt(
            job.status, borrower_id=job.borrower_id, lender_id=lender_id
        )
        and job.total_cost_usd is not None
    ):
        debt = label_for(job.total_cost_usd)
    return JobDetail(
        id=job.id,
        status=job.status,
        borrower=job.borrower.display_name,
        preview=_preview(job.prompt),
        is_follow_up=job.parent_job_id is not None,
        lender=job.lending.owner.display_name if job.lending else None,
        parent_job_id=job.parent_job_id,
        # 只有成功且留下 transcript 的 job 能被接續。
        can_follow_up=job.status.creates_debt and job.transcript_key is not None,
        model=job.model,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        total_cost_usd=job.total_cost_usd,
        prompt=job.prompt,
        source_type=job.source_type,
        lending_id=job.lending_id,
        self_run=self_run,
        # **只有代跑者本人看得到用了哪個帳號**（ADR-0001：帳號對委託者不可見）。
        # 自己跑自己時兩個身分是同一個人，那當然看得到。
        account_name=(
            job.account.name
            if job.account is not None and user is not None and lender_id == user.id
            else None
        ),
        result_text=job.result_text,
        error_kind=job.error_kind,
        error_detail=job.error_detail,
        stop_note=job.stop_note,
        lender_cli_version=job.lender_cli_version,
        borrower_cli_version=job.borrower_cli_version,
        debt_label=debt,
        failure=f.as_dict()
        if (
            f := classify(
                job.status,
                job.error_kind,
                job.error_detail,
                lender=job.lending.owner.display_name if job.lending else None,
                model=job.model,
            )
        )
        else None,
        can_stop=bool(user and _can_stop(job, user)),
        # 下載連結只發給看得到這一頁的人 —— _get_job 已經擋過（委託者本人與代跑者），
        # 跟「產出的檔案」同一條規則。
        attachments=[
            JobAttachment(
                name=storage.attachment_name(key),
                download_url=storage.presign_get(
                    key, filename=storage.attachment_name(key)
                ),
            )
            for key in (job.attachment_keys or [])
        ],
    )


async def _get_job(job_id: uuid.UUID, session: AsyncSession, user: User) -> Job:
    job = await session.scalar(select(Job).options(*_LOAD).where(Job.id == job_id))
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    # 只有借用者本人與執行該 job 的出租者能讀內容（.claude/rules/security.md）。
    lender_id = job.lending.owner_user_id if job.lending else None
    if user.id not in (job.borrower_id, lender_id):
        raise HTTPException(status_code=403, detail="這不是你的 job")
    return job


def _check_transcript(key: str, user: User) -> None:
    """驗上傳的 session 檔：是你的、存在、不過大、看起來像 JSONL。

    **第一項是存取控制，不是整理。** key 由客戶端指定，不檢查 prefix 的話
    任何人都能把它指到 `jobs/<別人的 job>/transcript.jsonl` —— worker 會把
    那份 transcript resume 出來，等於讀到別人的對話（security.md：只有借用者
    本人與執行該 job 的出租者能讀該 job 的內容）。
    """
    if not storage.owns_upload(key, user.id):
        # 不分「不是你的」與「不存在」—— 分開講等於給人探測別人 job id 的工具。
        raise HTTPException(404, "找不到這個上傳的檔案，請重新上傳")

    size = storage.stat(key)
    if size is None:
        raise HTTPException(404, "找不到這個上傳的檔案，請重新上傳")
    if size == 0:
        raise HTTPException(400, "這個檔案是空的")
    if size > MAX_TRANSCRIPT_BYTES:
        mb = MAX_TRANSCRIPT_BYTES // (1024 * 1024)
        raise HTTPException(413, f"session 檔超過 {mb} MB，無法續跑")

    head = storage.read_head(key, min(size, TRANSCRIPT_SNIFF_BYTES))
    lines = head.split(b"\n")
    # 最後一行可能被 Range 切斷，不驗它。整份只有一行時就驗那一行。
    candidates = [ln for ln in lines[:-1] if ln.strip()] or [lines[0]]
    for line in candidates[:TRANSCRIPT_SNIFF_LINES]:
        try:
            json.loads(line)
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(
                400,
                "這不像 Claude Code 的 session 檔。"
                "它應該在 ~/.claude/projects/<專案>/ 底下，每行是一個 JSON。",
            ) from exc


def _check_attachments(keys: list[str], user: User, transcript_bytes: int) -> None:
    """驗上傳的附件：是你的、存在、不過大。

    **第一項是存取控制。** `attachment_keys` 由客戶端指定，不檢查 prefix 的話
    任何人都能把 key 指到 `jobs/<別人的 job>/output/…`，讓 worker 把別人的產出
    放進自己的工作目錄讀走。與 transcript 同一條規則
    （`.claude/rules/security.md`）。
    """
    if len(set(keys)) != len(keys):
        raise HTTPException(400, "同一個檔案重複帶了兩次")

    total = transcript_bytes
    for key in keys:
        if not storage.owns_attachment(key, user.id):
            # 「不是你的」與「不存在」回同一個 404 —— 分開講等於給人探測
            # 別人 job id 的工具。
            raise HTTPException(404, "找不到這個上傳的檔案，請重新上傳")
        size = storage.stat(key)
        if size is None:
            raise HTTPException(404, "找不到這個上傳的檔案，請重新上傳")
        if size == 0:
            raise HTTPException(400, f"{storage.attachment_name(key)} 是空的")
        if size > MAX_ATTACHMENT_BYTES:
            mb = MAX_ATTACHMENT_BYTES // (1024 * 1024)
            raise HTTPException(413, f"{storage.attachment_name(key)} 超過 {mb} MB")
        total += size

    attachments_total = total - transcript_bytes
    if attachments_total > MAX_ATTACHMENTS_TOTAL_BYTES:
        mb = MAX_ATTACHMENTS_TOTAL_BYTES // (1024 * 1024)
        raise HTTPException(413, f"附件合計超過 {mb} MB")
    if total > MAX_JOB_INPUT_BYTES:
        mb = MAX_JOB_INPUT_BYTES // (1024 * 1024)
        raise HTTPException(413, f"這個 job 的輸入合計超過 {mb} MB")


async def _anyone_can_run(session: AsyncSession) -> bool:
    """站台上還有沒有**任何**跑得動的出借帳號。

    問的不是「現在有沒有人接單」（那是暫時的，值得排隊），是「有沒有一組還活著
    的憑證」。全部失效的時候，排隊等的是一個不會來的人。

    `needs_reauth` 由 job 回報認證失敗時自動設起來（routers/worker.py）。
    """
    return bool(
        await session.scalar(
            select(LendingAccount.id)
            .where(
                LendingAccount.oauth_token_enc.is_not(None),
                LendingAccount.needs_reauth.is_(False),
            )
            .limit(1)
        )
    )


async def _check_model(
    model: str,
    requested_lending_id: uuid.UUID | None,
    borrower_id: uuid.UUID,
    session: AsyncSession,
) -> None:
    """model 必須在站台白名單，而且真的有人跑得動。

    **這是唯一擋得住的位置。** spike #8 實測 `--settings availableModels` 不擋
    model（新舊兩條憑證路徑都一樣，CLI 只拿它擋 fast mode），而 worker 是把
    `--model` 直接帶給 CLI 的。前端的下拉只是方便，直接打 API 就繞過去了。

    不擋的後果不是「跑錯 model」，是**委託者可以讓代跑者欠十倍的錢** ——
    Fable 的 output 單價是 Haiku 的 10 倍，而債務照實際花費算。

    2026-09-23 起 Fable 在站台白名單上，但沒有人預設開放它（schemas.DEFAULT_MODELS）。
    所以對 Fable 來說，第二段那條「至少一位開了」才是真正在擋的那條 ——
    沒有人勾 Fable 的站台，Fable 的 job 在這裡就會被退回。

    ⚠️ 2026-09-22：這裡從「**每一位**可接單的代跑者都要跑得動」放寬成「**至少
    一位**跑得動」。舊的那條是因為當時 Hub 挑不了人（誰先 poll 誰拿到），只能
    事先保證每個人都行。現在派單在 Hub，跑不動這個 model 的人根本不會被挑中
    （SPEC §4.12），那條限制已經沒有存在的理由 —— 它只會讓一個人關掉 Sonnet
    就擋住全站的 Sonnet job。

    ⚠️ 2026-09-23：**「自動」不再把委託者本人算進池子**（SPEC §4.5）。
    所以多了一個以前不存在的狀態 ——「站台上跑得動這個 model 的只有你自己」。
    它要跟「一個人都沒有」分開講，因為下一步不一樣：前者是改選指定自己，
    後者是等別人上線。規則本身在 app/dispatch.py，三個地方共用同一份。
    """
    if model not in SITE_MODELS:
        raise HTTPException(
            400, f"這個站台只跑 {'、'.join(SITE_MODELS)}，不支援 {model}"
        )

    # selectinload 是必要的：runnable_models() 會碰 accounts，在 async session 裡
    # 沒有預先載入就會炸（lazy load 觸發同步 IO）。
    stmt = (
        select(LendingSetting)
        .where(LendingSetting.accepting.is_(True))
        .options(selectinload(LendingSetting.accounts))
    )
    if requested_lending_id is not None:
        stmt = stmt.where(LendingSetting.id == requested_lending_id)
    pool = list(await session.scalars(stmt))

    # 一個都沒有就不在這裡擋 —— job 會排隊等人上線，那是正常狀態，
    # 不是使用者挑錯 model。派不出去的處理在別處（sweep_expired_jobs）。
    #
    # ⚠️ 但「暫時沒人在線」與「站台上一個可用帳號都沒有」是兩件事，只有前者
    # 值得排隊。後者是死路：所有 token 都失效的時候排隊只是把失敗延後十五分鐘，
    # 而使用者會以為自己在等一個會來的人（2026-09-22 真的發生過）。
    if not pool:
        if not await _anyone_can_run(session):
            raise HTTPException(
                400,
                "現在站台上沒有任何可用的額度 —— 代跑者的授權失效了，"
                "等他重新授權好再送。這不是你的問題，送出去也只會排隊到過期。",
            )
        return

    # 指定那條不排除任何人，包含指定自己（ADR-0001 的場景）。
    if requested_lending_id is not None:
        # 用 runnable_models 而不是 available_models：勾著 Fable 但每個帳號都被
        # 回過「要買 usage credits」的人，派給他只會再失敗一次。「他願意」和
        # 「他能」是兩件事，對外一律用交集（models.LendingSetting.runnable_models）。
        if not any(model in s.runnable_models() for s in pool):
            raise HTTPException(400, f"你指定的那位代跑者沒有開放 {model}")
        return

    if auto_candidates(pool, model=model, borrower_id=borrower_id):
        return

    # 「只有你自己」是 2026-09-23 之後才存在的狀態。不單獨講的話，使用者會看到
    # 「線上沒有人開放 sonnet」，然後看著下拉裡那個開著 sonnet 的自己發呆。
    if only_me(pool, model=model, borrower_id=borrower_id):
        raise HTTPException(
            400,
            f"站台上現在跑得動 {model} 的只有你自己。「自動」不會派給你本人 —— "
            "要跑的話在代跑者那一欄改選你自己，那一趟不計債。",
        )

    raise HTTPException(400, f"現在線上沒有人開放 {model}，請換一個 model 或稍後再送")


async def _observ_token_for(prompt: str, token: str | None, user: User) -> bytes | None:
    """「查 Observ 事件」的 job 才收 Observ token；收了就要驗（ADR-0002 修訂，2026-09-24）。

    三道檢查，都在**花掉代跑者額度之前**（SPEC §4.13 的原則）：
    1. prompt 不是那個指令 → 不收。瀏覽器不該送，送了也不存 —— 一張 72 小時的
       Observ 身分沒有理由躺在一個用不到它的 job 上。
    2. token 必須是**本人**的。不驗這條的話，任何登入者都能把別人的 token 塞進來，
       讓 job 用別人的身分查事件。
    3. 剩餘效期要夠 job 排隊＋跑完（settings.observ_token_min_remaining_seconds）。
       送出時還活著、跑到一半 401，是花了額度才失敗的那種，要在這裡擋。
    回傳加密後的 bytes；直到派單前都只以這個形狀存在。
    """
    if not needs_observ_token(prompt):
        return None
    if not token:
        raise HTTPException(
            400,
            "「查 Observ 事件」需要你的 Observ 登入。這一頁沒有帶上它 —— "
            "請重新登入 boba 後再送一次。",
        )
    try:
        who = await observ.whoami(token)
    except observ.ObservError as exc:
        if exc.status_code == 401:
            raise HTTPException(
                400, "你的 Observ 登入已經過期，請重新登入 boba 後再送一次。"
            ) from exc
        raise HTTPException(exc.status_code, exc.detail) from exc
    if who.id != user.observ_user_id:
        raise HTTPException(403, "這張 Observ 登入不是你的。")
    expires_at = observ.token_expires_at(token)
    if expires_at is not None:
        remaining = (expires_at - datetime.now(UTC)).total_seconds()
        if remaining < settings.observ_token_min_remaining_seconds:
            raise HTTPException(
                400,
                "你的 Observ 登入快過期了（剩不到一小時），job 可能跑到一半就失效。"
                "請重新登入 boba 後再送一次。",
            )
    return secrets_box.seal(token)


async def _check_network(
    requested_lending_id: uuid.UUID | None,
    borrower_id: uuid.UUID,
    session: AsyncSession,
) -> None:
    """帶 Observ token 的 job 要連 Observ 與 middleware，只能派給開外網的代跑者
    （SPEC §4.13「選了指令就收窄派單範圍」的第一個實例，2026-09-24）。

    在這裡擋而不是排隊：排到一位白名單代跑者手上，容器連不到、花了額度才失敗，
    而錯誤會長得像「指令壞了」。
    """
    stmt = select(LendingSetting).where(
        LendingSetting.accepting.is_(True),
        LendingSetting.allow_full_network.is_(True),
    )
    if requested_lending_id is not None:
        stmt = stmt.where(LendingSetting.id == requested_lending_id)
    open_ones = list(await session.scalars(stmt))
    if requested_lending_id is not None:
        if not open_ones:
            raise HTTPException(
                400,
                "你指定的代跑者關掉了外網，「查 Observ 事件」在他那裡連不到 Observ。"
                "請改選「自動」或另一位開放外網的代跑者。",
            )
        return
    if not any(s.owner_user_id != borrower_id for s in open_ones):
        raise HTTPException(
            400,
            "目前線上沒有開放外網的代跑者，「查 Observ 事件」需要連到 Observ。"
            "請稍後再送，或在代跑者那一欄指定自己（如果你自己有開）。",
        )


@router.post("", response_model=JobDetail, status_code=201)
async def create_job(
    body: JobCreate,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> JobDetail:
    transcript_bytes = 0
    await _check_model(body.model, body.requested_lending_id, user.id, session)
    if body.transcript_key:
        _check_transcript(body.transcript_key, user)
        transcript_bytes = storage.stat(body.transcript_key) or 0
    if body.attachment_keys:
        _check_attachments(body.attachment_keys, user, transcript_bytes)
    observ_token_enc = await _observ_token_for(body.prompt, body.observ_token, user)
    if observ_token_enc is not None:
        await _check_network(body.requested_lending_id, user.id, session)

    job = Job(
        borrower_id=user.id,
        prompt=body.prompt,
        model=body.model,
        # 帶了 session 檔就是真的續跑，不是把對話當文字重貼一次。
        source_type=SourceType.TRANSCRIPT if body.transcript_key else body.source_type,
        transcript_key=body.transcript_key,
        attachment_keys=list(body.attachment_keys),
        requested_lending_id=body.requested_lending_id,
        borrower_cli_version=body.borrower_cli_version,
        observ_token_enc=observ_token_enc,
        status=JobStatus.QUEUED,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    job.borrower = user
    events.notify_new_job()
    return _detail(job)


@router.get("", response_model=list[JobSummary])
async def list_jobs(
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=50, le=200),
) -> list[JobSummary]:
    """只列自己送出的 job。"""
    rows = await session.scalars(
        select(Job)
        .options(selectinload(Job.borrower))
        .where(Job.borrower_id == user.id)
        .order_by(Job.created_at.desc())
        .limit(limit)
    )
    return [
        JobSummary(
            id=j.id,
            status=j.status,
            borrower=j.borrower.display_name,
            preview=_preview(j.prompt),
            is_follow_up=j.parent_job_id is not None,
            model=j.model,
            created_at=j.created_at,
            started_at=j.started_at,
            finished_at=j.finished_at,
            total_cost_usd=j.total_cost_usd,
        )
        for j in rows
    ]


@router.get("/{job_id}", response_model=JobDetail)
async def get_job(
    job_id: uuid.UUID,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> JobDetail:
    job = await _get_job(job_id, session, user)
    return _detail(job, user)


@router.post("/{job_id}/follow-up", response_model=JobDetail, status_code=201)
async def follow_up(
    job_id: uuid.UUID,
    body: FollowUp,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> JobDetail:
    """接著問。

    新開一個 job，但帶著上一個 job 的 transcript。worker 會用
    `claude --resume <transcript>` 接上去 —— 這是真的續跑，不是把對話重貼一次
    （跨機器 resume 已於 SPEC.md §11 spike #2 驗證可行）。
    """
    parent = await _get_job(job_id, session, user)
    if parent.transcript_key is None:
        raise HTTPException(
            status_code=409,
            detail="這個 job 沒有留下可接續的紀錄（可能是失敗或中止了）。",
        )
    if parent.borrower_id != user.id:
        raise HTTPException(status_code=403, detail="只有原本的提問者能接著問")

    # 第三個參數不能傳 0。100 MB 是「這個 job 的輸入合計」，而接著問的
    # transcript 雖然不是使用者這次上傳的，worker 一樣要把它連同附件整包下載。
    # 傳 0 的話一條長的續問鏈每輪都能再塞 50 MB，而 transcript 本身還在長。
    if body.attachment_keys:
        _check_attachments(
            body.attachment_keys, user, storage.stat(parent.transcript_key) or 0
        )

    # 續問預設不指名代跑者，**除了上一輪是自己跑的**（規則與理由在
    # dispatch.follow_up_target）。然後跟第一次提交走同一道死路檢查 ——
    # 續問以前沒走這條，所以死路是排隊 15 分鐘後才以「沒人有空」的樣子出現。
    requested = follow_up_target(
        parent_lending_owner=parent.lending.owner_user_id if parent.lending else None,
        parent_lending_id=parent.lending_id,
        borrower_id=user.id,
    )
    await _check_model(parent.model, requested, user.id, session)
    # 接著問「查 Observ 事件」：看的是**這一輪**的 prompt 開頭。上一輪的 token 派單時
    # 就清掉了，所以瀏覽器要再送一張當下的；沒有就走一般續問，腳本會說缺環境變數。
    observ_token_enc = await _observ_token_for(body.prompt, body.observ_token, user)
    if observ_token_enc is not None:
        await _check_network(requested, user.id, session)

    job = Job(
        borrower_id=user.id,
        prompt=body.prompt,
        model=parent.model,
        source_type=parent.source_type,
        parent_job_id=parent.id,
        transcript_key=parent.transcript_key,
        attachment_keys=body.attachment_keys,
        requested_lending_id=requested,
        observ_token_enc=observ_token_enc,
        status=JobStatus.QUEUED,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    job.borrower = user
    events.notify_new_job()
    return _detail(job)


@router.post("/{job_id}/stop", response_model=JobDetail)
async def stop_job(
    job_id: uuid.UUID,
    body: StopJob,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> JobDetail:
    """出租者中止正在跑的 job。

    只有**跑這個 job 的出租者**能停。

    `note` 是這個功能真正的重點（docs/web-spec.md §8）。比較一下：

        ❌ 你的 job 已被出租者終止。
        ✅ Vivian 終止了你的 job：「這個看起來會跑很久，我等下要開會，晚點再幫你跑」

    沒有那句話，停止會被讀成拒絕。

    Hub 這裡只改狀態；真正殺掉容器的是 worker —— 它在下一次回報時會從回應裡
    收到 cancel（SPEC.md §9 的控制通道）。狀態要立刻改，不能等 worker 確認，
    否則按了之後畫面沒反應。
    """
    job = await _get_job(job_id, session, user)
    if not _can_stop(job, user):
        raise HTTPException(
            status_code=409,
            detail="只有正在跑這個 job 的出租者能中止它，而且它必須還在執行中。",
        )

    job.status = JobStatus.CANCELLED
    job.observ_token_enc = None
    job.stop_note = (body.note or "").strip() or None
    job.error_kind = "cancelled_by_lender"
    job.finished_at = datetime.now(UTC)
    await session.commit()

    notify.job_stopped(job.borrower, user.display_name, job.id, job.stop_note)
    events.publish(
        job.id, {"seq": -1, "payload": {"type": "stream_end", "status": "cancelled"}}
    )
    return _detail(job, user)


@router.get("/{job_id}/artifacts")
async def artifacts(
    job_id: uuid.UUID,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """產出檔案清單，附短效期下載連結。

    預簽 URL 本身就是憑證，所以每次請求現開，不存下來也不寫進通知
    （.claude/rules/security.md）。
    """
    await _get_job(job_id, session, user)
    rows = await session.scalars(
        select(Artifact).where(Artifact.job_id == job_id).order_by(Artifact.name)
    )
    return [
        {
            "name": a.name,
            "size_bytes": a.size_bytes,
            "download_url": storage.presign_get(a.key),
        }
        for a in rows
    ]


def _transcript_key_for_download(job: Job, user: User) -> str:
    """把這個 job 的 transcript 帶回自己機器 —— 回傳它在 MinIO 上的 key。

    **存取控制比 `_get_job` 更緊，這是刻意的。** `_get_job` 讓借用者本人與執行該
    job 的出租者都讀得到內容；下載是把整份對話搬出這個站台，而 SPEC.md §8 的
    30 天 lifecycle 是隱私承諾的一部分。搬得走的只有那份對話的主人。
    （對他本身不構成新的外洩：他本來就看得到這份對話。）

    開放條件與 `can_follow_up` 一致 —— 兩顆按鈕在畫面上並排，條件不一致的話
    其中一顆會是死的。
    """
    if user.id != job.borrower_id:
        raise HTTPException(403, "這不是你的對話")
    if not job.status.creates_debt or job.transcript_key is None:
        raise HTTPException(404, "這個 job 沒有留下可以續跑的對話")
    return job.transcript_key


@router.get("/{job_id}/transcript")
async def transcript(
    job_id: uuid.UUID,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """短效期下載連結，給「帶回自己的機器續跑」用。

    跟產出檔案同一條路：每次請求現開，不存下來也不寫進通知 ——
    預簽 URL 本身就是憑證（.claude/rules/security.md）。
    """
    job = await _get_job(job_id, session, user)
    key = _transcript_key_for_download(job, user)
    # 檔名要是 session id，Claude Code 才認得出來（放進 ~/.claude/projects/
    # 之後 `--resume` 是用檔名當 session id 找的）。
    #
    # 這個名字必須由**預簽 URL 自己**帶（Content-Disposition）—— 前端的
    # `<a download>` 在跨 origin 時會被瀏覽器忽略，檔案會落地成
    # `transcript.jsonl`，而那個名字 resume 不到。
    filename = f"{job.id}.jsonl"
    return {
        "filename": filename,
        "size_bytes": storage.stat(key),
        "download_url": storage.presign_get(key, filename=filename),
    }


@router.get("/{job_id}/events")
async def get_events(
    job_id: uuid.UUID,
    from_seq: int = Query(default=0, ge=0),
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Poll fallback。SSE 斷線時前端改打這裡，資料與串流完全相同。"""
    await _get_job(job_id, session, user)
    rows = await session.scalars(
        select(JobEvent)
        .where(JobEvent.job_id == job_id, JobEvent.seq >= from_seq)
        .order_by(JobEvent.seq)
    )
    return [{"seq": e.seq, "payload": e.payload} for e in rows]


@router.get("/{job_id}/stream")
async def stream(
    job_id: uuid.UUID,
    from_seq: int = Query(default=0, ge=0),
    token: str = Query(default=""),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """SSE。先重播已存事件，再接上即時流。

    token 走 query string 而不是 Authorization header，因為瀏覽器的 `EventSource`
    無法設定自訂 header。這是 SSE 的已知限制，不是偷懶 —— 代價是 token 會出現在
    伺服器的 access log，所以 Hub 不記錄 query string（見 main.py）。
    """
    user = await _user_from_query_token(token, session)
    job = await _get_job(job_id, session, user)
    replay = list(
        await session.scalars(
            select(JobEvent)
            .where(JobEvent.job_id == job_id, JobEvent.seq >= from_seq)
            .order_by(JobEvent.seq)
        )
    )
    already_done = job.status.is_terminal
    backlog = [{"seq": e.seq, "payload": e.payload} for e in replay]
    # 下面的 generate() 不碰資料庫，但 Depends(get_session) 的 session 會活到
    # 回應結束 —— 也就是使用者關掉 job 頁為止。那段時間它是「idle in transaction」，
    # 握著 jobs / job_events 的 AccessShareLock：2026-09-23 一支 ALTER TABLE 因此
    # 等了 13 分鐘，而排在它後面的每一個查詢都跟著卡住，hub 整個凍結。
    # 所以資料讀完就把交易結掉，串流只靠 events 的記憶體佇列。
    await session.commit()
    await session.close()

    async def generate() -> AsyncIterator[str]:
        # 先訂閱再送重播，否則兩者之間到達的事件會遺失。
        queue = events.subscribe(job_id)
        try:
            for item in backlog:
                yield _sse(item)
            if already_done:
                yield _sse({"seq": -1, "payload": {"type": "stream_end"}})
                return
            while True:
                try:
                    item = await asyncio.wait_for(
                        queue.get(), timeout=_HEARTBEAT_SECONDS
                    )
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield _sse(item)
                if item.get("payload", {}).get("type") == "stream_end":
                    return
        finally:
            events.unsubscribe(job_id, queue)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _user_from_query_token(token: str, session: AsyncSession) -> User:
    from .. import observ
    from ..auth import upsert_user

    if not token:
        raise HTTPException(status_code=401, detail="請先登入")
    try:
        who = await observ.whoami(token)
    except observ.ObservError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return await upsert_user(session, who)


def _sse(item: dict) -> str:
    return f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
