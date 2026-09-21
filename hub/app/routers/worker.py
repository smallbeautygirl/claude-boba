"""Worker 面向的 API。SPEC.md §9「Hub ↔ Worker 協定」。

worker 跑在出租者的機器上，可能在 NAT 後、可能隨時關機，所以一律由它主動連出；
Hub 從不主動連 worker。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import events, notify, storage
from ..config import settings
from ..db import get_session
from ..enums import DebtStatus, DebtTier, JobStatus
from ..models import Artifact, Debt, Job, JobEvent, Usage, User, Worker
from ..pricing import LABELS, MIN_DEBT_USD, tier_for
from ..schemas import (
    MAX_ARTIFACT_BYTES,
    ArtifactManifest,
    EventBatch,
    JobResult,
    WorkerConfig,
    WorkerJob,
)

router = APIRouter(prefix="/api/worker", tags=["worker"])


async def require_worker(
    x_worker_token: str = Header(...), session: AsyncSession = Depends(get_session)
) -> Worker:
    worker = await session.scalar(select(Worker).where(Worker.token == x_worker_token))
    if worker is None:
        raise HTTPException(status_code=401, detail="unknown worker token")
    worker.last_seen_at = datetime.now(UTC)
    worker.online = True
    await session.commit()
    return worker


@router.post("/config")
async def report_config(
    body: WorkerConfig,
    worker: Worker = Depends(require_worker),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """worker 啟動時回報自己的設定。

    這裡沒有註冊行為 —— worker 的身分是出租者在網頁按「產生 token」拿到的。
    這樣 worker 完全不需要出租者的 Observ 帳密，不用把公司密碼寫進 .env。
    """
    worker.allow_full_network = body.allow_full_network
    worker.available_models = body.available_models
    worker.job_budget_usd = body.job_budget_usd
    worker.max_concurrency = body.max_concurrency
    worker.claude_code_version = body.claude_code_version
    await session.commit()

    warnings: list[str] = []
    if worker.job_budget_usd < MIN_DEBT_USD:
        # 上限低於債務門檻 = 能跑完的 job 一定在門檻以下 = 永遠不會掛債。
        # 這不是錯誤（有人可能就是想這樣），但幾乎一定是沒注意到，所以要講。
        warnings.append(
            f"JOB_BUDGET_USD={worker.job_budget_usd} 低於債務門檻 US${MIN_DEBT_USD}，"
            f"這台 worker 跑的 job 永遠不會產生人情債。"
        )
    return {"worker_id": str(worker.id), "name": worker.name, "warnings": warnings}


@router.get("/poll", response_model=None)
async def poll(
    response: Response,
    worker: Worker = Depends(require_worker),
    session: AsyncSession = Depends(get_session),
) -> WorkerJob | Response:
    """Long-poll 領單。沒單就 hold 住，逾時回 204。

    選 long-poll 而非 WebSocket：要傳的是單向批次事件流，Hub 要回的只有「停止」
    一個指令（夾在 events 的回應裡）。為此維持雙向長連線不划算，
    而 long-poll 天然容忍 worker 重啟。
    """
    job = await _claim(worker, session)
    if job is None:
        await events.wait_for_job(timeout=settings.worker_poll_timeout)
        job = await _claim(worker, session)
    if job is None:
        return Response(status_code=204)

    return WorkerJob(
        job_id=job.id,
        prompt=job.prompt,
        model=job.model,
        source_type=job.source_type,
        job_budget_usd=worker.job_budget_usd,
        available_models=worker.available_models,
        resume_from_url=(
            storage.presign_get(job.transcript_key) if job.transcript_key else None
        ),
        transcript_put_url=storage.presign_put(storage.transcript_key(job.id)),
    )


async def _claim(worker: Worker, session: AsyncSession) -> Job | None:
    """取一筆 queued job 並鎖定。

    `with_for_update(skip_locked=True)` 讓多個 worker 同時 poll 時不會搶到同一筆 ——
    SPEC §4.11 明確不引入 message queue，用 DB 當佇列，這是它的併發原語。
    """
    if not worker.accepting:
        return None

    running = await session.scalar(
        select(func.count())
        .select_from(Job)
        .where(
            Job.worker_id == worker.id,
            Job.status.in_([JobStatus.CLAIMED, JobStatus.RUNNING]),
        )
    )
    if running and running >= worker.max_concurrency:
        return None

    stmt = (
        select(Job)
        .where(
            Job.status == JobStatus.QUEUED,
            # 不能寫成 in_([None, worker.id]) —— SQL 裡 `NULL IN (...)` 永遠不為真，
            # 自動派單（requested_worker_id 為 NULL）的 job 會一筆都領不到，
            # 而且不會報錯，只是靜靜地什麼都不派。
            or_(
                Job.requested_worker_id.is_(None), Job.requested_worker_id == worker.id
            ),
        )
        .order_by(Job.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = await session.scalar(stmt)
    if job is None:
        return None

    job.status = JobStatus.CLAIMED
    job.worker_id = worker.id
    job.claimed_at = datetime.now(UTC)
    await session.commit()
    _emit(job.id, 0, {"type": "claimed", "worker": worker.name})
    return job


@router.post("/jobs/{job_id}/events")
async def push_events(
    job_id: uuid.UUID,
    body: EventBatch,
    worker: Worker = Depends(require_worker),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """接收批次事件，並在回應裡夾帶控制指令（目前只有停止）。"""
    job = await _owned_job(job_id, worker, session)

    if job.status is JobStatus.CLAIMED and body.events:
        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(UTC)

    seq = body.from_seq
    for payload in body.events:
        session.add(JobEvent(job_id=job.id, seq=seq, payload=payload))
        _emit(job.id, seq, payload)
        _absorb_rate_limit(worker, payload)
        seq += 1

    await session.commit()
    # 出租者按了停止 → 這裡回 True，worker 殺掉容器。SPEC §9 的控制通道。
    return {"next_seq": seq, "cancel": job.status is JobStatus.CANCELLED}


@router.post("/jobs/{job_id}/artifacts")
async def declare_artifacts(
    job_id: uuid.UUID,
    body: ArtifactManifest,
    worker: Worker = Depends(require_worker),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """worker 宣告產出檔案，換回預簽 PUT URL。

    Hub 是唯一持有 MinIO 憑證的一方；worker 只拿短效期的 URL。
    """
    job = await _owned_job(job_id, worker, session)

    uploads: list[dict] = []
    skipped: list[str] = []
    for decl in body.files:
        if decl.size_bytes > MAX_ARTIFACT_BYTES:
            # 略過但要說 —— 悄悄丟掉檔案比擋下更糟。
            skipped.append(decl.name)
            continue
        key = storage.artifact_key(job.id, decl.name)
        session.add(
            Artifact(job_id=job.id, name=decl.name, key=key, size_bytes=decl.size_bytes)
        )
        uploads.append({"name": decl.name, "put_url": storage.presign_put(key)})

    await session.commit()
    return {"uploads": uploads, "skipped": skipped}


@router.post("/jobs/{job_id}/result")
async def push_result(
    job_id: uuid.UUID,
    body: JobResult,
    worker: Worker = Depends(require_worker),
    session: AsyncSession = Depends(get_session),
) -> dict:
    job = await _owned_job(job_id, worker, session)

    job.status = body.status
    job.result_text = body.result_text
    job.total_cost_usd = body.total_cost_usd
    job.error_kind = body.error_kind
    job.error_detail = body.error_detail
    job.lender_cli_version = body.lender_cli_version
    job.finished_at = datetime.now(UTC)
    if body.transcript_uploaded:
        # 成功上傳才記 key，否則續問會指向一個不存在的物件。
        job.transcript_key = storage.transcript_key(job.id)

    # 金額直接採信 CLI 的 costUSD（SPEC §7）。我們不自己算費率 ——
    # costBasis 為 "list" 表示它已經是 API 標價，正是 §4.6 要的「API 等價金額」。
    for model_id, usage in body.model_usage.items():
        session.add(
            Usage(
                job_id=job.id,
                model=model_id,
                canonical_model=usage.get("canonicalModel"),
                cost_basis=usage.get("costBasis"),
                input_tokens=usage.get("inputTokens", 0),
                output_tokens=usage.get("outputTokens", 0),
                cache_read_tokens=usage.get("cacheReadInputTokens", 0),
                cache_creation_tokens=usage.get("cacheCreationInputTokens", 0),
                thinking_tokens=usage.get("thinkingTokens", 0),
                cost_usd=Decimal(str(usage.get("costUSD", 0))),
            )
        )

    debt = await _maybe_create_debt(job, worker, session)

    await session.commit()

    borrower = await session.get(User, job.borrower_id)
    notify.job_finished(
        borrower.teams_webhook_url if borrower else None,
        job.id,
        str(job.status),
        job.total_cost_usd,
    )
    if debt is not None:
        lender = await session.get(User, worker.owner_user_id)
        notify.debt_created(
            borrower.teams_webhook_url if borrower else None,
            lender.teams_webhook_url if lender else None,
            borrower.display_name if borrower else "?",
            lender.display_name if lender else "?",
            LABELS[debt.tier],
            debt.amount_usd,
            job.id,
        )
    _emit(job.id, -1, {"type": "stream_end", "status": str(job.status)})
    return {"ok": True}


async def _maybe_create_debt(
    job: Job, worker: Worker, session: AsyncSession
) -> Debt | None:
    """只有成功的 job 會掛債（SPEC.md §5），而且要超過最低級距。

    `< US$1 不用還` 是級距表最重要的一列：多數 job 會落在那一格，而讓多數互動
    不欠債，真正欠債時才顯得慎重；每次都掛帳，債務會變成沒人理的噪音。
    這種 job 的成本仍完整記在 usages 表裡，只是不產生人情債。
    """
    if not job.status.creates_debt or job.total_cost_usd is None:
        return None
    if job.borrower_id == worker.owner_user_id:
        # 自己跑自己的不算欠自己。
        return None
    tier = tier_for(job.total_cost_usd)
    if tier is DebtTier.NONE:
        return None
    debt = Debt(
        job_id=job.id,
        borrower_id=job.borrower_id,
        lender_id=worker.owner_user_id,
        amount_usd=job.total_cost_usd,
        tier=tier,
        status=DebtStatus.OPEN,
    )
    session.add(debt)
    return debt


async def _owned_job(job_id: uuid.UUID, worker: Worker, session: AsyncSession) -> Job:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.worker_id != worker.id:
        raise HTTPException(status_code=403, detail="job belongs to another worker")
    return job


def _absorb_rate_limit(worker: Worker, payload: dict[str, Any]) -> None:
    """記下出租者的額度使用率（SPEC §4.6）。

    只用於派單與紅綠燈顯示，不用於記帳。對外只顯示紅綠燈，不顯示百分比 ——
    精確數字會讓借用者盤算「他還有 66%，再送一個沒差」（docs/web-spec.md §3）。
    """
    if payload.get("type") != "rate_limit_event":
        return
    windows = payload.get("rate_limit_info", {}).get("unifiedWindows", {})
    if five := windows.get("five_hour"):
        worker.utilization_five_hour = five.get("utilization")
    if seven := windows.get("seven_day"):
        worker.utilization_seven_day = seven.get("utilization")


def _emit(job_id: uuid.UUID, seq: int, payload: dict) -> None:
    events.publish(job_id, {"seq": seq, "payload": payload})
