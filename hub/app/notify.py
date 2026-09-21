"""Teams 通知。

預設走**共用頻道 + @mention**：使用者零設定，而 mention 實測可用
（用 email 當 `mentioned.id` 即可，不需要 AAD object id），所以當事人會收到
紅點通知，不只是「頻道裡多了一則訊息」。

想要更詳細或更隱私的人，可以在設定頁填自己的 webhook，之後就改走私訊。

⚠️ 舊的 Office 365 Incoming Webhook 連接器已於 2026-05-22 停用。現在要用
Teams 的 Workflows（Power Automate）範本。

**通知只放 metadata。** job 的 prompt、對話、產出一律不進通知
（.claude/rules/security.md 紅線 1），預簽下載連結也不放 —— 那本身就是憑證，
不該留在會被長期保存的訊息裡。

**頻道訊息比私訊更克制**：不含金額。頻道會讓「誰在什麼時候用了這個工具」
變成公開資訊，那是零設定的代價；但「花了多少」沒有必要一起公開。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0
_GOOD = "1f7a4d"
_BAD = "b3261e"

_pending: set[asyncio.Task] = set()


def _fire(url: str | None, payload: dict) -> None:
    if not url:
        return
    task = asyncio.create_task(_post(url, payload))
    # 保留參照，否則 task 可能在跑完前被 GC 掉。
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def _post(url: str, payload: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        # 通知送不出去不該影響任何主流程 —— job 已經跑完了。
        logger.warning("Teams 通知失敗: %r", exc)


def _card(title: str, text: str, colour: str) -> dict:
    """MessageCard。透過 Workflows webhook 可用，但**按鈕不會被渲染**，
    所以連結一律寫在內文的 markdown 裡（實測可點）。"""
    return {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": colour,
        "summary": title,
        "title": title,
        "text": text,
    }


def _mention_card(email: str, name: str, text: str) -> dict:
    """Adaptive Card 加 @mention。實測：`mentioned.id` 放 email 就能運作。"""
    tag = f"<at>{name}</at>"
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "type": "AdaptiveCard",
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "version": "1.4",
                    "body": [
                        {
                            "type": "TextBlock",
                            "text": text.replace("{@}", tag),
                            "wrap": True,
                        }
                    ],
                    "msteams": {
                        "entities": [
                            {
                                "type": "mention",
                                "text": tag,
                                "mentioned": {"id": email, "name": name},
                            }
                        ]
                    },
                },
            }
        ],
    }


def link(job_id: object) -> str:
    return f"{settings.web_base_url.rstrip('/')}/jobs/{job_id}"


def ledger_link() -> str:
    return f"{settings.web_base_url.rstrip('/')}/ledger"


def test_message(url: str) -> None:
    _fire(
        url, _card("🧋 claude-boba 通知已開啟", "之後 job 跑完會在這裡通知你。", _GOOD)
    )


def job_finished(user: Any, job_id: object, status: str, cost: object) -> None:
    ok = status == "succeeded"
    if user is not None and user.teams_webhook_url:
        # 私訊：可以帶金額，因為只有本人看得到。
        title = "🧋 你的 job 跑完了" if ok else "你的 job 沒跑完"
        body = (
            f"花費 US${cost}\n\n[看結果]({link(job_id)})"
            if ok
            else f"狀態：{status}（這次不計債）\n\n[看詳情]({link(job_id)})"
        )
        _fire(user.teams_webhook_url, _card(title, body, _GOOD if ok else _BAD))
        return

    if not settings.teams_channel_webhook or user is None:
        return
    # 頻道：不帶金額。
    verb = "跑完了" if ok else f"沒跑完（{status}）"
    _fire(
        settings.teams_channel_webhook,
        _mention_card(
            user.email,
            user.display_name,
            f"🧋 {{@}} 你的 job {verb}\n\n[看結果]({link(job_id)})",
        ),
    )


def debt_created(
    borrower: Any, lender: Any, label: str, amount: object, job_id: object
) -> None:
    """掛債通知。

    私訊帶金額；頻道只帶級距（「一杯手搖」）。級距本來就是刻意做粗的 ——
    公開它不會讓人算計，公開金額會。

    頻道是否要發掛債通知由 `TEAMS_CHANNEL_DEBTS` 控制。開著比較符合這個產品的
    社交機制，但也可能讓人不好意思借 —— 關掉的話，沒設個人 webhook 的人
    就只在網頁帳本看得到。
    """
    if borrower is not None and borrower.teams_webhook_url:
        _fire(
            borrower.teams_webhook_url,
            _card(
                label,
                f"你欠 {lender.display_name} 一份人情。剛才那個 job 花了 US${amount}。"
                f"\n\n[看帳本]({ledger_link()})",
                _GOOD,
            ),
        )
    elif settings.teams_channel_debts and settings.teams_channel_webhook and borrower:
        _fire(
            settings.teams_channel_webhook,
            _mention_card(
                borrower.email,
                borrower.display_name,
                f"{label} — {{@}} 欠 {lender.display_name} 一份人情"
                f"\n\n[看帳本]({ledger_link()})",
            ),
        )

    if lender is not None and lender.teams_webhook_url:
        _fire(
            lender.teams_webhook_url,
            _card(
                f"{borrower.display_name} 欠你 {label}",
                f"US${amount}\n\n[看帳本]({ledger_link()})",
                _GOOD,
            ),
        )
