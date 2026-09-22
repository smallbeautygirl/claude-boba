"""Worker 面向的 API。SPEC.md §9「Hub ↔ Worker 協定」。

worker 一律主動連出，Hub 從不主動連 worker。

⚠️ **2026-09-22：worker 的身分從「某位代跑者」改成「這台主機」**（SPEC §4.12）。
舊協定是一個 token 對一列 worker，job 只能用**那一列自己的** oauth token 跑 ——
於是 Hub 根本沒有「挑帳號」這個動作，帳號是自己跑來搶單的。那讓「一位代跑者
出借多個帳號」做不出來。現在 Hub 在領單時決定**哪一份出借設定、哪一個出借帳號**，
把 token 隨 job 派下去。
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .. import events, notify, secrets_box, storage
from ..config import settings
from ..db import get_session
from ..enums import DebtStatus, DebtTier, JobStatus
from ..models import (
    Artifact,
    Debt,
    Job,
    JobEvent,
    LendingAccount,
    LendingSetting,
    Usage,
    User,
    WorkerHost,
)
from ..pricing import LABELS, MIN_DEBT_USD, tier_for
from ..schemas import (
    MAX_ARTIFACT_BYTES,
    ArtifactManifest,
    Attachment,
    EventBatch,
    JobResult,
    WorkerConfig,
    WorkerJob,
)
from .workers import QUOTA_FRESH_SECONDS

router = APIRouter(prefix="/api/worker", tags=["worker"])


async def require_host(
    x_worker_token: str = Header(...), session: AsyncSession = Depends(get_session)
) -> WorkerHost:
    """領單主機的身分。

    token 來自 hub 的 `WORKER_SHARED_TOKEN`，不再由網頁產生 —— 它識別的是
    「這台主機」，不是某位代跑者（SPEC §4.12）。

    **沒設就一律拒絕**：給預設值等於讓一個空設定檔變成任何人都能領走別人的 job，
    連同一年期 OAuth token。
    """
    expected = settings.worker_shared_token
    if not expected or not secrets.compare_digest(x_worker_token, expected):
        raise HTTPException(status_code=401, detail="unknown worker token")
    host = await session.scalar(select(WorkerHost).limit(1))
    if host is None:
        host = WorkerHost()
        session.add(host)
    host.last_seen_at = datetime.now(UTC)
    await session.commit()
    return host


@router.post("/config")
async def report_config(
    body: WorkerConfig,
    host: WorkerHost = Depends(require_host),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """主機啟動時回報自己。

    **不再回報出借條件。** 上限／model／外網是代跑者在網頁上設的，屬於人不屬於
    主機 —— 讓 worker/.env 回報它們，等於主機每次重啟就把他改過的條件蓋掉一次。

    低於債務門檻的上限仍然要提醒，但那是**每位代跑者各自**的設定，所以在這裡
    一起掃一遍：能跑完的 job 一定在門檻以下 = 永遠不會掛債。這不是錯誤
    （有人可能就是想這樣），但幾乎一定是沒注意到。
    """
    host.name = body.name
    host.max_concurrency = body.max_concurrency
    host.claude_code_version = body.claude_code_version
    await session.commit()

    lows = list(
        await session.scalars(
            select(LendingSetting).where(LendingSetting.job_budget_usd < MIN_DEBT_USD)
        )
    )
    warnings: list[str] = []
    if lows:
        warnings.append(
            f"有 {len(lows)} 位代跑者的花費上限低於債務門檻 US${MIN_DEBT_USD}，"
            f"他們跑的 job 永遠不會產生人情債。"
        )
    return {"host_id": str(host.id), "name": host.name, "warnings": warnings}


@router.get("/poll", response_model=None)
async def poll(
    response: Response,
    host: WorkerHost = Depends(require_host),
    session: AsyncSession = Depends(get_session),
) -> WorkerJob | Response:
    """Long-poll 領單。沒單就 hold 住，逾時回 204。

    選 long-poll 而非 WebSocket：要傳的是單向批次事件流，Hub 要回的只有「停止」
    一個指令（夾在 events 的回應裡）。為此維持雙向長連線不划算，
    而 long-poll 天然容忍 worker 重啟。
    """
    claimed = await _claim(host, session)
    if claimed is None:
        await events.wait_for_job(timeout=settings.worker_poll_timeout)
        claimed = await _claim(host, session)
    if claimed is None:
        return Response(status_code=204)

    job, setting, account = claimed
    return WorkerJob(
        # 託管模型：job 跑在這台主機上，用 Hub 挑中的那個出借帳號的長期 token。
        oauth_token=secrets_box.open_(account.oauth_token_enc),
        job_id=job.id,
        prompt=job.prompt,
        model=job.model,
        source_type=job.source_type,
        job_budget_usd=setting.job_budget_usd,
        available_models=setting.available_models,
        allow_full_network=setting.allow_full_network,
        resume_from_url=(
            storage.presign_get(job.transcript_key) if job.transcript_key else None
        ),
        transcript_put_url=storage.presign_put(storage.transcript_key(job.id)),
        attachments=[
            Attachment(name=storage.attachment_name(key), url=storage.presign_get(key))
            for key in (job.attachment_keys or [])
        ],
    )


async def _pick_account(
    setting: LendingSetting, session: AsyncSession
) -> LendingAccount | None:
    """在一位代跑者的出借帳號之間挑一個（SPEC §4.12）。

    **挑 utilization 最低的那個** —— 既然帳本不分來源（ADR-0001），兩個帳號對
    委託者完全等價，那就平均磨損真正稀缺的東西：額度。不是輪流，因為兩個帳號的
    額度週期不同步，輪流只保證次數平均。

    **冷啟動與過期都退回輪流。** 那兩個數字只有跑過 job 之後才有，而代跑者自己在
    別的地方也在燒同一個帳號，站台不會知道 —— 拿一個過期的 12% 當「最空」去派，
    比不知道更糟。輪流用 last_assigned_at，NULL 排最前面（沒跑過的先上）。
    """
    now = datetime.now(UTC)
    usable = []
    for a in setting.accounts:
        if not a.usable:
            continue
        running = await session.scalar(
            select(func.count())
            .select_from(Job)
            .where(
                Job.account_id == a.id,
                Job.status.in_([JobStatus.CLAIMED, JobStatus.RUNNING]),
            )
        )
        if running and running >= a.max_concurrency:
            continue
        usable.append(a)
    if not usable:
        return None

    def fresh(a: LendingAccount) -> float | None:
        if a.quota_updated_at is None:
            return None
        if (now - a.quota_updated_at).total_seconds() > QUOTA_FRESH_SECONDS:
            return None
        return a.utilization("five_hour")

    known = [(u, a) for a in usable if (u := fresh(a)) is not None]
    if known:
        return min(known, key=lambda pair: pair[0])[1]
    return min(
        usable, key=lambda a: a.last_assigned_at or datetime.min.replace(tzinfo=UTC)
    )


async def _claim(
    host: WorkerHost, session: AsyncSession
) -> tuple[Job, LendingSetting, LendingAccount] | None:
    """取一筆 queued job、決定由誰的哪個帳號跑，並鎖定。

    `with_for_update(skip_locked=True)` 讓多次同時 poll 不會搶到同一筆 ——
    SPEC §4.11 明確不引入 message queue，用 DB 當佇列，這是它的併發原語。

    **派單的決定在這裡，不在 worker。** 這是 2026-09-22 改的重點：worker 只問
    「有沒有工作」，挑哪位代跑者、哪個出借帳號都是 Hub 說了算（SPEC §4.12）。
    """
    running = await session.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.status.in_([JobStatus.CLAIMED, JobStatus.RUNNING]))
    )
    if running and running >= host.max_concurrency:
        return None

    # 候選 job 不只看第一筆：第一筆可能指名了一位現在接不了單的代跑者，
    # 而後面那筆是自動派單、派得出去。只看第一筆會讓整個佇列被它擋住。
    jobs = list(
        await session.scalars(
            select(Job)
            .where(Job.status == JobStatus.QUEUED)
            .order_by(Job.created_at)
            .limit(20)
            .with_for_update(skip_locked=True)
        )
    )
    if not jobs:
        return None

    settings_by_id: dict[uuid.UUID, LendingSetting] = {
        s.id: s
        for s in await session.scalars(
            select(LendingSetting)
            .where(LendingSetting.accepting.is_(True))
            .options(selectinload(LendingSetting.accounts))
        )
    }

    for job in jobs:
        if job.requested_lending_id is not None:
            candidates = [settings_by_id.get(job.requested_lending_id)]
        else:
            # 自動派單只考慮跑得動這個 model 的人。舊版是「有人跑不動就整批擋在
            # 提交時」—— 那是因為當時 Hub 挑不了，只能事先保證每個人都行。
            candidates = [
                s
                for s in settings_by_id.values()
                if job.model in (s.available_models or [])
            ]
        for setting in candidates:
            if setting is None:
                continue
            if job.model not in (setting.available_models or []):
                continue
            account = await _pick_account(setting, session)
            if account is None:
                continue
            job.status = JobStatus.CLAIMED
            job.lending_id = setting.id
            job.account_id = account.id
            job.claimed_at = datetime.now(UTC)
            account.last_assigned_at = job.claimed_at
            await session.commit()
            _emit(
                job.id,
                0,
                # 事件裡只講**人**。哪個出借帳號跑的不對委託者顯示（ADR-0001）。
                {
                    "type": "claimed",
                    "worker": (
                        await session.get(User, setting.owner_user_id)
                    ).display_name,
                },
            )
            return job, setting, account
    return None


@router.post("/jobs/{job_id}/events")
async def push_events(
    job_id: uuid.UUID,
    body: EventBatch,
    host: WorkerHost = Depends(require_host),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """接收批次事件，並在回應裡夾帶控制指令（目前只有停止）。"""
    job = await _owned_job(job_id, session)

    if job.status is JobStatus.CLAIMED and body.events:
        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(UTC)

    seq = body.from_seq
    for payload in body.events:
        session.add(JobEvent(job_id=job.id, seq=seq, payload=payload))
        _emit(job.id, seq, payload)
        await _absorb_rate_limit(job, payload, session)
        seq += 1

    await session.commit()
    # 出租者按了停止 → 這裡回 True，worker 殺掉容器。SPEC §9 的控制通道。
    return {"next_seq": seq, "cancel": job.status is JobStatus.CANCELLED}


@router.post("/jobs/{job_id}/artifacts")
async def declare_artifacts(
    job_id: uuid.UUID,
    body: ArtifactManifest,
    host: WorkerHost = Depends(require_host),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """worker 宣告產出檔案，換回預簽 PUT URL。

    Hub 是唯一持有 MinIO 憑證的一方；worker 只拿短效期的 URL。
    """
    job = await _owned_job(job_id, session)

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
    host: WorkerHost = Depends(require_host),
    session: AsyncSession = Depends(get_session),
) -> dict:
    job = await _owned_job(job_id, session)

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

    debt = await _maybe_create_debt(job, session)

    await session.commit()

    borrower = await session.get(User, job.borrower_id)
    notify.job_finished(borrower, job.id, str(job.status), job.total_cost_usd)
    if debt is not None:
        lender = await session.get(User, debt.lender_id)
        notify.debt_created(
            borrower, lender, LABELS[debt.tier], debt.amount_usd, job.id
        )
    _emit(job.id, -1, {"type": "stream_end", "status": str(job.status)})
    return {"ok": True}


async def _maybe_create_debt(job: Job, session: AsyncSession) -> Debt | None:
    """只有成功的 job 會掛債（SPEC.md §5），而且要超過最低級距。

    `< US$1 不用還` 是級距表最重要的一列：多數 job 會落在那一格，而讓多數互動
    不欠債，真正欠債時才顯得慎重；每次都掛帳，債務會變成沒人理的噪音。
    這種 job 的成本仍完整記在 usages 表裡，只是不產生人情債。

    債主是**人**，不是帳號：他借出去的是自己的額度使用權與帳號的風險敞口，
    而公司帳號跑的 job 照樣計債（ADR-0001）。
    """
    if not job.status.creates_debt or job.total_cost_usd is None:
        return None
    setting = (
        await session.get(LendingSetting, job.lending_id) if job.lending_id else None
    )
    if setting is None:
        return None
    if job.borrower_id == setting.owner_user_id:
        # 自己跑自己的不算欠自己。
        return None
    tier = tier_for(job.total_cost_usd)
    if tier is DebtTier.NONE:
        return None
    debt = Debt(
        job_id=job.id,
        borrower_id=job.borrower_id,
        lender_id=setting.owner_user_id,
        amount_usd=job.total_cost_usd,
        tier=tier,
        status=DebtStatus.OPEN,
    )
    session.add(debt)
    return debt


async def _owned_job(job_id: uuid.UUID, session: AsyncSession) -> Job:
    """主機正在跑的 job。

    託管模型下只有一台主機，所以「這是不是你的 job」退化成「它派出去了嗎」——
    派出去的對象是出借帳號，不是主機。沒派出去的 job 主機不該有話要說。
    """
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.account_id is None:
        raise HTTPException(status_code=403, detail="job has not been dispatched")
    return job


async def _absorb_rate_limit(
    job: Job, payload: dict[str, Any], session: AsyncSession
) -> None:
    """記下**這個出借帳號**的額度使用率（SPEC §4.6）。

    只用於派單與紅綠燈顯示，不用於記帳。對外只顯示紅綠燈，不顯示百分比 ——
    精確數字會讓委託者盤算「他還有 66%，再送一個沒差」（docs/web-spec.md §3）。
    本人看得到精確數字與重置時間（web-spec §8）。

    整包 `unifiedWindows` 原樣存下來，不挑 key：Anthropic 加窗的頻率我們控制不了
    （截圖裡已經出現第三個窗），而「顯示哪些窗」應該是顯示層的決定，
    不是一次 migration。
    """
    if payload.get("type") != "rate_limit_event":
        return
    windows = payload.get("rate_limit_info", {}).get("unifiedWindows")
    if not isinstance(windows, dict) or not windows:
        return
    account = await session.get(LendingAccount, job.account_id)
    if account is None:
        return
    account.rate_limit_windows = windows
    # 沒有這個時間戳，過期的數字會裝成即時的 —— 代跑者自己在別的地方也在燒同一個
    # 帳號，站台不會知道（SPEC §11 spike #10）。
    account.quota_updated_at = datetime.now(UTC)


def _emit(job_id: uuid.UUID, seq: int, payload: dict) -> None:
    events.publish(job_id, {"seq": seq, "payload": payload})
