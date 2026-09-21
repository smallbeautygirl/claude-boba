"""Teams 通知。

每個使用者自己在 Teams 建一個 Workflows webhook（`Send webhook alerts to a chat`）
並貼進個人設定 —— 因為那個範本的目的地是建立時固定的，沒辦法靠 payload 指定對象。
好處是不需要管理員權限，也不需要 email 對照表，每個人能自己關掉通知。

⚠️ 舊的 Office 365 Incoming Webhook 連接器已於 2026-05-22 停用，不要再用。

**通知內容只放 metadata。** job 的 prompt、對話、產出一律不進通知
（.claude/rules/security.md 紅線 1），預簽下載連結也不放 ——
那本身就是憑證，不該留在會被長期保存的訊息裡。
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from .config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0

# MessageCard 透過 Workflows webhook 可用，但**按鈕不會被渲染**
# （微軟遷移說明明確提到）。所以連結寫在內文裡，不用 potentialAction。
_GOOD = "1f7a4d"
_BAD = "b3261e"
_NOTE = "b4693a"


def _card(title: str, text: str, colour: str) -> dict:
    return {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": colour,
        "summary": title,
        "title": title,
        "text": text,
    }


async def _post(url: str, card: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, json=card)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        # 通知送不出去不該影響任何主流程 —— job 已經跑完了。
        logger.warning("Teams 通知失敗: %r", exc)


def send(webhook_url: str | None, title: str, text: str, *, ok: bool = True) -> None:
    """非阻塞地送出一則通知。沒設 webhook 就靜靜跳過。"""
    if not webhook_url:
        return
    task = asyncio.create_task(
        _post(webhook_url, _card(title, text, _GOOD if ok else _BAD))
    )
    # 保留參照，避免 task 被 GC 掉（asyncio 不持有弱參照以外的東西）。
    _pending.add(task)
    task.add_done_callback(_pending.discard)


_pending: set[asyncio.Task] = set()


def job_link(job_id: object) -> str:
    return f"{settings.web_base_url.rstrip('/')}/jobs/{job_id}"


def job_finished(
    webhook_url: str | None, job_id: object, status: str, cost: object
) -> None:
    ok = status == "succeeded"
    title = "🧋 你的 job 跑完了" if ok else "你的 job 沒跑完"
    body = (
        f"花費 US${cost}\n\n[看結果]({job_link(job_id)})"
        if ok
        else f"狀態：{status}（這次不計債）\n\n[看詳情]({job_link(job_id)})"
    )
    send(webhook_url, title, body, ok=ok)


def debt_created(
    borrower_hook: str | None,
    lender_hook: str | None,
    borrower: str,
    lender: str,
    label: str,
    amount: object,
    job_id: object,
) -> None:
    link = job_link(job_id)
    send(
        borrower_hook,
        f"{label}",
        f"你欠 {lender} 一份人情。剛才那個 job 花了 US${amount}。\n\n[看詳情]({link})",
        ok=True,
    )
    send(
        lender_hook,
        f"{borrower} 欠你 {label}",
        f"US${amount}。\n\n[看詳情]({link})",
        ok=True,
    )
