"""Job 事件的即時扇出。

Phase 1 是單一 Hub 程序，所以用記憶體中的 queue 就夠（SPEC §4.11 明確不做
message queue）。若哪天 Hub 要跑多個 instance，這裡要換成 Postgres LISTEN/NOTIFY
或 Redis pub/sub —— 換的只有這個檔案，呼叫端不用動。
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict

_subscribers: dict[uuid.UUID, set[asyncio.Queue[dict]]] = defaultdict(set)


def subscribe(job_id: uuid.UUID) -> asyncio.Queue[dict]:
    queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=1000)
    _subscribers[job_id].add(queue)
    return queue


def unsubscribe(job_id: uuid.UUID, queue: asyncio.Queue[dict]) -> None:
    subs = _subscribers.get(job_id)
    if not subs:
        return
    subs.discard(queue)
    if not subs:
        _subscribers.pop(job_id, None)


def publish(job_id: uuid.UUID, event: dict) -> None:
    """非阻塞。訂閱者跟不上就丟掉該事件 —— 瀏覽器會用 seq 發現缺口並重新拉取。"""
    for queue in _subscribers.get(job_id, ()):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass


# worker 的 long-poll：有新 job 時叫醒正在等的 worker，不用讓它空轉輪詢。
_job_available = asyncio.Event()


def notify_new_job() -> None:
    _job_available.set()


async def wait_for_job(timeout: float) -> None:
    try:
        await asyncio.wait_for(_job_available.wait(), timeout=timeout)
    except TimeoutError:
        return
    finally:
        _job_available.clear()
