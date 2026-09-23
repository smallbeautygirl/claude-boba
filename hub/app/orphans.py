"""worker 死掉之後，它的 job 要有人結案。

2026-09-23 的真實情境：worker 讀 job 輸出時撞上 asyncio 的 64 KiB 行上限，例外一路
拋出 `run_job`，result 從沒送到 hub。hub 只知道那個 job 02:38 開始，於是它「執行中」
了半個多小時，秒數一直走，沒有錯誤、沒有期限、也沒有任何東西會來救它 —— 跟
`expiry.py` 記的那個 queued 版本是同一種病，只是換了一個狀態。

worker 那頭已經修了（`_report_crash`），但那是 worker 自己還活著才做得到的事。
worker 整個被 kill、主機斷電、網路分割 —— 這些情境只有 hub 這邊看得到，所以這裡
不是重複防禦，是唯一的防禦。

判定依據是 `jobs.last_heartbeat_at`：worker 每 ~3 秒為每個執行中的 job 打一次
`/events`（空批次也算），超過 `job_heartbeat_timeout_seconds` 沒打就是死了。

> 🚨 這個模組的 `sweeper()` **必須在 `main.py` 的 lifespan 裡被 create_task**，
> 而且 `tests/test_sweeper_is_wired.py` 要多守一條。理由見 expiry.py 開頭那段。
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import events, notify
from .config import settings
from .db import SessionLocal
from .enums import JobStatus
from .models import Job, User

ERROR_KIND = "worker_error"  # failures.py 把它歸為系統問題：不計債、不顯示明細


async def sweep_once(session: AsyncSession, *, now: datetime | None = None) -> int:
    """把 worker 已經沒在回報的 claimed / running job 標成 failed，回結案了幾個。

    不動 queued（那是 expiry.py 的事）、不動已結束的。
    **不計債**（SPEC §5）：failed 本來就不計。worker 若其實還活著、稍後才把 result
    送到，`push_result` 會直接覆寫這裡的判定 —— 那時資料更準，讓它贏。
    """
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(seconds=settings.job_heartbeat_timeout_seconds)
    last_sign = func.coalesce(Job.last_heartbeat_at, Job.started_at, Job.claimed_at)
    silent = list(
        await session.scalars(
            select(Job).where(
                Job.status.in_([JobStatus.CLAIMED, JobStatus.RUNNING]),
                last_sign < cutoff,
            )
        )
    )
    if not silent:
        return 0

    for job in silent:
        last = job.last_heartbeat_at or job.started_at or job.claimed_at
        quiet_for = int((now - last).total_seconds()) if last else -1
        job.status = JobStatus.FAILED
        job.error_kind = ERROR_KIND
        job.error_detail = (
            f"worker 已 {quiet_for} 秒沒有回報這個 job，hub 替它結案。"
            "通常是 worker 在 job 中途崩潰或被關掉；看 worker log。"
        )
        job.finished_at = now
    await session.commit()

    # 通知與畫面更新放在 commit 之後（理由同 expiry.py）。
    for job in silent:
        # 開著的 job 頁靠這個事件停掉計時器，不然它會繼續「已執行 …」到重新整理。
        events.publish(
            job.id, {"seq": -1, "payload": {"type": "stream_end", "status": "failed"}}
        )
        borrower = await session.get(User, job.borrower_id)
        notify.job_finished(borrower, job.id, str(JobStatus.FAILED), None)
    return len(silent)


async def sweeper(interval: float = 30.0, warmup: float | None = None) -> None:
    """背景清潔工。

    `warmup`：hub 剛啟動的前一段時間**不掃**。hub 自己重啟時（graceful shutdown
    會等 long-poll，實測要 60 秒）worker 打不進來，所有執行中 job 的心跳都會過期；
    開機就掃等於把每一個健康的 job 誤判成孤兒。等滿一個 timeout，worker 早就
    重新連上、心跳也補上了。預設等於 `job_heartbeat_timeout_seconds`。

    一次例外不該讓迴圈死掉 —— 它死了沒有任何症狀，只會讓 job 再度變成永遠執行中。
    """
    if warmup is None:
        warmup = float(settings.job_heartbeat_timeout_seconds)
    await asyncio.sleep(warmup)
    while True:
        with contextlib.suppress(Exception):
            async with SessionLocal() as session:
                await sweep_once(session)
        await asyncio.sleep(interval)
