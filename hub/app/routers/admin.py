"""站台管理者的維運介面。

**這一頁的分界：看得到什麼壞了，不能瀏覽誰欠誰。**

`_get_job()` 刻意限制「誰能看到誰的 job」，而在一個以人情債為核心的產品裡，
「誰用得兇、誰欠誰多少」是社交敏感資訊 —— 那不是技術問題，是這個工具會不會
被同事信任的問題。所以這裡只有系統健康、**不指名的**聚合數字，以及卡住的 job。
沒有全站 job 瀏覽，也沒有任何 job 內容（security.md 紅線 1）。

🚨 **每一個綠燈都要是真的量到的。** 量不到的東西放在 `unknown` 裡並寫出原因，
不要放一顆永遠綠的燈 —— 那比沒有指示器危險，因為它會被相信。
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import is_admin, require_user
from ..config import settings
from ..db import get_session
from ..enums import JobStatus
from ..models import Job, User, Worker
from .workers import OFFLINE_AFTER_SECONDS

router = APIRouter(prefix="/api/admin", tags=["admin"])

# 「卡住」的判定寫死在這裡並附理由，不要每個人自己抓一個數字。
#
# 排隊 10 分鐘還沒被領走就算卡住 —— 站台在 15 分鐘（`job_queue_expiry_seconds`）
# 會把它作廢，所以這個門檻讓管理者有五分鐘可以反應。改其中一個要看另一個。
QUEUED_STUCK_SECONDS = 600
# 執行中超過這個時間算卡住。worker 的預設逾時是 600 秒（worker/worker.py），
# 加兩分鐘寬限給收尾與上傳。
#
# ⚠️ **worker 沒有把自己的逾時回報給 hub**（`WorkerConfig` 只有 network／models／
# budget／concurrency／CLI 版本），所以這是一個假設，不是量到的值。有代跑者把
# 逾時調長的話，他的 job 會提早被列進來。要精確就得讓 worker 回報那個值。
RUNNING_STUCK_SECONDS = 600 + 120


async def require_admin(user: User = Depends(require_user)) -> User:
    """每一支都擋在後端，不是靠前端不顯示。

    前端不渲染不等於沒送出去 —— 打開開發者工具就繞過去了。
    """
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="這一頁只有站台管理者看得到")
    return user


@router.get("/health")
async def health(
    _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)
) -> dict:
    """各元件的狀態。三種分類刻意分開，因為它們的可信度不同：

    - `checks`：**這次真的去問過**，綠燈代表剛剛量到的事實
    - `facts`：讀得出來但不是健康檢查（例如 schema 版本）
    - `unknown`：從 hub 檢查不到的東西，連同原因一起講
    """
    checks: list[dict] = []

    # 1. 資料庫：真的跑一個 query。
    t0 = time.monotonic()
    try:
        await session.scalar(select(1))
        # 本機的 SELECT 1 常常不到 1 毫秒，取整數會印出「0 ms」——
        # 那是真的，但在健康面板上讀起來像「沒量到」，而可信是這頁唯一的資產。
        ms = (time.monotonic() - t0) * 1000
        checks.append(
            {
                "key": "db",
                "label": "資料庫",
                "ok": True,
                "detail": f"查詢往返 {'< 0.1' if ms < 0.1 else f'{ms:.1f}'} ms",
            }
        )
    except Exception as exc:  # noqa: BLE001 —— 這裡就是要把任何失敗變成紅燈
        checks.append(
            {"key": "db", "label": "資料庫", "ok": False, "detail": str(exc)[:200]}
        )

    # 2. MinIO：打它自己的健康端點。不要用「我們存得進去嗎」當檢查 ——
    #    那會在正常運作時寫入垃圾物件。
    url = settings.s3_endpoint.rstrip("/") + "/minio/health/live"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(url)
        checks.append(
            {
                "key": "storage",
                "label": "檔案儲存（MinIO）",
                "ok": r.status_code == 200,
                # 位址要標明是「hub 這邊看到的」—— 它常常是 localhost，
                # 管理者會以為 console 開在自己的機器上。
                "detail": f"健康端點回 {r.status_code}（hub 這邊連的是 {url}）",
            }
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(
            {
                "key": "storage",
                "label": "檔案儲存（MinIO）",
                "ok": False,
                "detail": f"hub 連不到 {url}：{str(exc)[:120]}",
            }
        )

    # 3. 代跑者：last_seen_at 是 worker 真的回報過才會動的。
    cutoff = datetime.now(UTC) - timedelta(seconds=OFFLINE_AFTER_SECONDS)
    total = await session.scalar(select(func.count()).select_from(Worker)) or 0
    online = (
        await session.scalar(
            select(func.count()).select_from(Worker).where(Worker.last_seen_at > cutoff)
        )
        or 0
    )
    checks.append(
        {
            "key": "workers",
            "label": "領單的 worker",
            "ok": online > 0,
            "detail": f"{online} / {total} 台在過去 {OFFLINE_AFTER_SECONDS} 秒內回報過",
        }
    )

    facts = [
        {
            "label": "資料庫 schema 版本",
            "value": await _schema_revision(session),
            # 這個**不放成綠燈**：hub 啟動時就檢查，版本不符會拒絕啟動，
            # 所以它在畫面上永遠會是綠的 —— 一顆永遠綠的燈沒有資訊量。
            "note": "hub 啟動時就檢查，落後會直接拒絕啟動",
        }
    ]

    unknown = [
        {
            "label": "job 容器的對外白名單（egress proxy）",
            "why": (
                "它是代跑者機器上的 docker 容器，hub 連不到它，"
                "也沒有任何回報管道 —— 這一項無法從 hub 確認"
            ),
        }
    ]

    return {"checks": checks, "facts": facts, "unknown": unknown}


async def _schema_revision(session: AsyncSession) -> str:
    """alembic 自己的版本表。讀不到就講讀不到，不要回一個假的版本字串。"""
    try:
        return (
            await session.scalar(text("SELECT version_num FROM alembic_version"))
        ) or "（未初始化）"
    except Exception:  # noqa: BLE001
        return "（讀不到）"


@router.get("/stats")
async def stats(
    _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)
) -> dict:
    """聚合數字，**不指名**。

    「誰用得兇」「誰欠誰」不放這裡 —— 見模組開頭。所以這裡只有總數，
    沒有任何 per-user 的拆解。
    """
    users = await session.scalar(select(func.count()).select_from(User)) or 0
    rows = await session.execute(select(Job.status, func.count()).group_by(Job.status))
    by_status = {str(s): n for s, n in rows}
    done = sum(by_status.get(s, 0) for s in (JobStatus.SUCCEEDED, JobStatus.FAILED))
    spend = await session.scalar(select(func.sum(Job.total_cost_usd))) or Decimal(0)
    return {
        "users": users,
        "jobs_total": sum(by_status.values()),
        "jobs_by_status": by_status,
        # 成功率的分母只算跑完的（成功 + 失敗）—— 把排隊中的算進去的話，
        # 剛丟一批 job 進來就會讓成功率看起來掉下去。
        "success_rate": (
            round(by_status.get(str(JobStatus.SUCCEEDED), 0) / done, 3)
            if done
            else None
        ),
        "spend_usd": str(spend),
    }


@router.get("/stuck-jobs")
async def stuck_jobs(
    _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)
) -> list[dict]:
    """只列異常的 job，不是全站瀏覽。

    **不含任何 job 內容**（紅線 1）—— 只有 id、狀態、卡多久、哪一台 worker。
    """
    now = datetime.now(UTC)
    queued_cutoff = now - timedelta(seconds=QUEUED_STUCK_SECONDS)
    running_cutoff = now - timedelta(seconds=RUNNING_STUCK_SECONDS)

    rows = await session.execute(
        select(Job, Worker.name)
        .join(Worker, Job.worker_id == Worker.id, isouter=True)
        .where(
            ((Job.status == JobStatus.QUEUED) & (Job.created_at < queued_cutoff))
            | (
                Job.status.in_([JobStatus.CLAIMED, JobStatus.RUNNING])
                & (func.coalesce(Job.started_at, Job.claimed_at) < running_cutoff)
            )
        )
        .order_by(Job.created_at)
        .limit(50)
    )
    out = []
    for job, worker_name in rows:
        since = job.started_at or job.claimed_at or job.created_at
        out.append(
            {
                "id": str(job.id),
                "status": str(job.status),
                "stuck_seconds": int((now - since).total_seconds()),
                "worker": worker_name,
            }
        )
    return out
