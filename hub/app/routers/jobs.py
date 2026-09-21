"""借用者面向的 API。身分由 Observ 提供（SPEC.md §4.10）。"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .. import events, storage
from ..auth import require_user
from ..db import get_session
from ..enums import JobStatus
from ..failures import classify
from ..models import Artifact, Job, JobEvent, User, Worker
from ..pricing import label_for
from ..schemas import FollowUp, JobCreate, JobDetail, JobSummary

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


def _detail(job: Job) -> JobDetail:
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


@router.post("", response_model=JobDetail, status_code=201)
async def create_job(
    body: JobCreate,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> JobDetail:
    job = Job(
        borrower_id=user.id,
        prompt=body.prompt,
        model=body.model,
        source_type=body.source_type,
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
    return _detail(await _get_job(job_id, session, user))


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

    job = Job(
        borrower_id=user.id,
        prompt=body.prompt,
        model=parent.model,
        source_type=parent.source_type,
        parent_job_id=parent.id,
        transcript_key=parent.transcript_key,
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
