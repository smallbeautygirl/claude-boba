"""借用者面向的 API。Phase 1 尚無認證（SPEC.md §12）。"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import events
from ..db import get_session
from ..enums import JobStatus
from ..models import Job, JobEvent
from ..pricing import label_for
from ..schemas import JobCreate, JobDetail, JobSummary

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

# SSE 心跳。沒有它，中介的 proxy 會把閒置連線切掉，而 job 可能十分鐘沒有事件。
_HEARTBEAT_SECONDS = 15.0


def _detail(job: Job) -> JobDetail:
    debt = None
    if job.status.creates_debt and job.total_cost_usd is not None:
        debt = label_for(job.total_cost_usd)
    return JobDetail(
        id=job.id,
        status=job.status,
        borrower_label=job.borrower_label,
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
    )


async def _get_job(job_id: uuid.UUID, session: AsyncSession) -> Job:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.post("", response_model=JobDetail, status_code=201)
async def create_job(
    body: JobCreate, session: AsyncSession = Depends(get_session)
) -> JobDetail:
    job = Job(
        borrower_label=body.borrower_label,
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
    events.notify_new_job()
    return _detail(job)


@router.get("", response_model=list[JobSummary])
async def list_jobs(
    session: AsyncSession = Depends(get_session), limit: int = Query(default=50, le=200)
) -> list[JobSummary]:
    rows = await session.scalars(
        select(Job).order_by(Job.created_at.desc()).limit(limit)
    )
    return [JobSummary.model_validate(j, from_attributes=True) for j in rows]


@router.get("/{job_id}", response_model=JobDetail)
async def get_job(
    job_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> JobDetail:
    return _detail(await _get_job(job_id, session))


@router.get("/{job_id}/events")
async def get_events(
    job_id: uuid.UUID,
    from_seq: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Poll fallback。SSE 斷線時前端改打這裡，資料與串流完全相同。"""
    await _get_job(job_id, session)
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
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """SSE。先重播已存事件，再接上即時流。

    「先重播再接上」是為了讓使用者能關掉分頁再回來（docs/web-spec.md §4）。
    重連時帶上最後看到的 seq，從那裡續傳。
    """
    job = await _get_job(job_id, session)
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


def _sse(item: dict) -> str:
    return f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
