"""派不出去的 job 要自己作廢。

SPEC §5 定了 `expired`，`failures.py` 寫好了它的文案，`config.py` 有
`job_queue_expiry_seconds`，`routers/jobs.py` 的註解寫著「派不出去的處理在別處」
—— **而那個「別處」在 2026-09-22 之前不存在**。四個地方都在描述一個沒有被實作的
機制，所以它看起來像做好了，實際上 queued 的 job 會永遠排隊。

暴露它的是一個真實情境：唯一的出借帳號授權失效，使用者送了一個 job，然後那個
job 就停在「排隊中」——沒有錯誤、沒有期限、也沒有任何東西會來救它。

> 🚨 這個模組的 `sweeper()` **必須在 `main.py` 的 lifespan 裡被 create_task**。
> 這個 repo 已經因為「測試呼叫得到內層函式，不代表外層有人啟動它」踩過一次坑
> （見 main.py 裡 authorize sweeper 的那段註解）。`sweep_once()` 可以單獨測，
> 但那不證明它有在跑。
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import notify
from .config import settings
from .db import SessionLocal
from .enums import JobStatus
from .models import Job, User


async def sweep_once(session: AsyncSession) -> int:
    """把排太久的 queued job 標成 expired，回作廢了幾個。

    只動 `queued` —— 已經被領走的 job 有 worker 在照顧它；worker 死掉之後的結案
    是 `orphans.py` 的事（依心跳判定），管理頁的 stuck-jobs 則只列不動。

    **不計債**（SPEC §5）：沒有跑成任何東西，也沒有花到任何人的額度。
    """
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.job_queue_expiry_seconds)
    stale = list(
        await session.scalars(
            select(Job).where(Job.status == JobStatus.QUEUED, Job.created_at < cutoff)
        )
    )
    if not stale:
        return 0

    now = datetime.now(UTC)
    for job in stale:
        job.status = JobStatus.EXPIRED
        job.finished_at = now
    await session.commit()

    # 通知放在 commit 之後：先確保狀態真的寫進去了，再去講。反過來的話，
    # commit 失敗會留下一個「已經通知你作廢、但畫面上還在排隊」的 job。
    for job in stale:
        borrower = await session.get(User, job.borrower_id)
        notify.job_finished(borrower, job.id, str(JobStatus.EXPIRED), None)
    return len(stale)


async def sweeper(interval: float = 60.0) -> None:
    """背景清潔工。

    間隔不需要精準：作廢的門檻是 15 分鐘，晚一分鐘處理沒有差別。
    一次例外不該讓這個迴圈死掉 —— 它死了之後不會有任何症狀，
    只會讓 job 再度變成永遠排隊。
    """
    while True:
        await asyncio.sleep(interval)
        with contextlib.suppress(Exception):
            async with SessionLocal() as session:
                await sweep_once(session)
