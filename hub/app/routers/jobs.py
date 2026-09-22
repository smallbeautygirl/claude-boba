"""借用者面向的 API。身分由 Observ 提供（SPEC.md §4.10）。"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .. import events, notify, storage
from ..auth import require_user
from ..db import get_session
from ..enums import JobStatus, SourceType
from ..failures import classify
from ..models import Artifact, Job, JobEvent, User, Worker
from ..pricing import label_for
from ..schemas import (
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENTS_TOTAL_BYTES,
    MAX_JOB_INPUT_BYTES,
    MAX_TRANSCRIPT_BYTES,
    SITE_MODELS,
    TRANSCRIPT_SNIFF_BYTES,
    TRANSCRIPT_SNIFF_LINES,
    FollowUp,
    JobCreate,
    JobDetail,
    JobSummary,
    StopJob,
)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

# SSE 心跳。沒有它，中介的 proxy 會把閒置連線切掉，而 job 可能十分鐘沒有事件。
_HEARTBEAT_SECONDS = 15.0

_LOAD = (
    selectinload(Job.borrower),
    selectinload(Job.worker).selectinload(Worker.owner),
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
        and job.worker is not None
        and job.worker.owner_user_id == user.id
    )


def _detail(job: Job, user: User | None = None) -> JobDetail:
    debt = None
    if job.status.creates_debt and job.total_cost_usd is not None:
        debt = label_for(job.total_cost_usd)
    return JobDetail(
        id=job.id,
        status=job.status,
        borrower=job.borrower.display_name,
        preview=_preview(job.prompt),
        is_follow_up=job.parent_job_id is not None,
        lender=job.worker.owner.display_name if job.worker else None,
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
        worker_id=job.worker_id,
        result_text=job.result_text,
        error_kind=job.error_kind,
        error_detail=job.error_detail,
        stop_note=job.stop_note,
        lender_cli_version=job.lender_cli_version,
        borrower_cli_version=job.borrower_cli_version,
        debt_label=debt,
        failure=f.as_dict()
        if (f := classify(job.status, job.error_kind, job.error_detail))
        else None,
        can_stop=bool(user and _can_stop(job, user)),
    )


async def _get_job(job_id: uuid.UUID, session: AsyncSession, user: User) -> Job:
    job = await session.scalar(select(Job).options(*_LOAD).where(Job.id == job_id))
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    # 只有借用者本人與執行該 job 的出租者能讀內容（.claude/rules/security.md）。
    lender_id = job.worker.owner_user_id if job.worker else None
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


# worker 超過這個秒數沒回報就當它離線（與 routers/workers.py 同一個值）。
_OFFLINE_AFTER_SECONDS = 75


async def _check_model(
    model: str, requested_worker_id: uuid.UUID | None, session: AsyncSession
) -> None:
    """model 必須在站台白名單，而且真的有人跑得動。

    **這是唯一擋得住的位置。** spike #8 實測 `--settings availableModels` 不擋
    model（新舊兩條憑證路徑都一樣，CLI 只拿它擋 fast mode），而 worker 是把
    `--model` 直接帶給 CLI 的。前端的下拉只是方便，直接打 API 就繞過去了。

    不擋的後果不是「跑錯 model」，是**借用者可以讓出租者欠十倍的錢** ——
    Opus 的 output 單價是 Haiku 的 10 倍，而債務照實際花費算。
    """
    if model not in SITE_MODELS:
        raise HTTPException(
            400, f"這個站台只跑 {' 或 '.join(SITE_MODELS)}，不支援 {model}"
        )

    fresh = datetime.now(UTC) - timedelta(seconds=_OFFLINE_AFTER_SECONDS)
    stmt = select(Worker).where(
        Worker.accepting.is_(True), Worker.last_seen_at >= fresh
    )
    if requested_worker_id is not None:
        stmt = stmt.where(Worker.id == requested_worker_id)
    pool = list(await session.scalars(stmt))

    # 一台都沒有就不在這裡擋 —— job 會排隊等人上線，那是正常狀態，
    # 不是使用者挑錯 model。派不出去的處理在別處（expired）。
    if not pool:
        return

    if requested_worker_id is not None:
        if model not in (pool[0].available_models or []):
            raise HTTPException(400, f"你指定的那台出租者沒有開放 {model}")
        return

    # 自動派單：派單不按 model 過濾（poll 只看 requested_worker_id），所以
    # 只要有任何一台可接單的機器跑不動它，這個 job 就可能派給那台然後失敗。
    # 取交集而不是聯集 —— 與前端的 offeredModels() 同一條規則。
    cannot = [w for w in pool if model not in (w.available_models or [])]
    if cannot:
        raise HTTPException(
            400,
            f"現在有出租者沒開放 {model}，而自動派單可能派給他們。"
            f"請改挑一個有開放的出租者，或換 model。",
        )


@router.post("", response_model=JobDetail, status_code=201)
async def create_job(
    body: JobCreate,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> JobDetail:
    transcript_bytes = 0
    await _check_model(body.model, body.requested_worker_id, session)
    if body.transcript_key:
        _check_transcript(body.transcript_key, user)
        transcript_bytes = storage.stat(body.transcript_key) or 0
    if body.attachment_keys:
        _check_attachments(body.attachment_keys, user, transcript_bytes)

    job = Job(
        borrower_id=user.id,
        prompt=body.prompt,
        model=body.model,
        # 帶了 session 檔就是真的續跑，不是把對話當文字重貼一次。
        source_type=SourceType.TRANSCRIPT if body.transcript_key else body.source_type,
        transcript_key=body.transcript_key,
        attachment_keys=list(body.attachment_keys),
        requested_worker_id=body.requested_worker_id,
        borrower_cli_version=body.borrower_cli_version,
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

    job = Job(
        borrower_id=user.id,
        prompt=body.prompt,
        model=parent.model,
        source_type=parent.source_type,
        parent_job_id=parent.id,
        transcript_key=parent.transcript_key,
        attachment_keys=body.attachment_keys,
        # 續問預設回同一台 worker，但不強制 —— transcript 在 MinIO 上，
        # 任何 worker 都拿得到。那台剛好離線時不該讓使用者卡住。
        requested_worker_id=None,
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
