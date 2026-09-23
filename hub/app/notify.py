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
import uuid
from typing import TYPE_CHECKING, Any

import httpx

from .config import settings

if TYPE_CHECKING:
    from .models import User

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


def account_needs_reauth(lender: Any, account_name: str) -> None:
    """某個出借帳號的授權失效了，通知它的主人。

    **沒有這個通知，他不會知道。** 那個帳號停掉之後站台照常運作（其他帳號會接），
    所以不會有任何東西壞給他看 —— 而他每少一個能跑的帳號，同事就多等一輪。

    不帶金額也不帶 job 內容：這件事跟哪一個 job 無關，是帳號的狀態。
    """
    text = (
        f"你的出借帳號「{account_name}」授權失效了，已經停止接單。\n\n"
        "到「我來代跑」重新授權一次就好。**要把授權碼貼回來完成流程** —— "
        "中途放棄的話它會維持停用。\n\n"
        f"[去重新授權]({settings.web_base_url.rstrip('/')}/worker)"
    )
    if lender is not None and lender.teams_webhook_url:
        _fire(lender.teams_webhook_url, _card("出借帳號要重新授權", text, _BAD))
        return
    if not settings.teams_channel_webhook or lender is None:
        return
    _fire(
        settings.teams_channel_webhook,
        _mention_card(lender.email, lender.display_name, "{@} " + text),
    )


def account_needs_credits(lender: Any, account_name: str, model: str) -> None:
    """某個出借帳號被回「這個 model 要買 usage credits」，通知它的主人。

    **跟 `account_needs_reauth` 同一個理由：沒有這個通知，他不會知道。** 站台只是
    安靜地不再把這個 model 的 job 派給那個帳號，其他照跑，所以不會有東西壞給他看。
    而他自己以為 Fable 是開著的。

    不帶 job 內容，只講帳號與 model —— 這是帳號的狀態，跟哪一個 job 無關。
    """
    text = (
        f"你的出借帳號「{account_name}」跑 {model} 需要 usage credits，"
        f"所以站台先不把 {model} 的 job 派給它了（其他 model 照常）。\n\n"
        "那個 job 沒有花到你的額度，也沒有讓對方欠債。"
        "買了 credits 之後，到「我來代跑」按一下〔再試一次〕就會恢復。\n\n"
        f"[去看看]({settings.web_base_url.rstrip('/')}/worker)"
    )
    if lender is not None and lender.teams_webhook_url:
        _fire(lender.teams_webhook_url, _card("出借帳號需要 usage credits", text, _BAD))
        return
    if not settings.teams_channel_webhook or lender is None:
        return
    _fire(
        settings.teams_channel_webhook,
        _mention_card(lender.email, lender.display_name, "{@} " + text),
    )


def job_stopped(borrower, lender_name: str, job_id: object, note: str | None) -> None:
    """出租者中止了 job。

    那句話一定要帶進通知。沒有它，「你的 job 被中止了」讀起來就是拒絕
    （docs/web-spec.md §8）。
    """
    body = f"{lender_name} 中止了你的 job"
    if note:
        body += f"：\n\n> {note}"
    body += f"\n\n這次不計債。\n\n[看詳情]({link(job_id)})"

    if borrower is not None and borrower.teams_webhook_url:
        _fire(borrower.teams_webhook_url, _card("你的 job 被中止了", body, _BAD))
    elif settings.teams_channel_webhook and borrower is not None:
        _fire(
            settings.teams_channel_webhook,
            _mention_card(borrower.email, borrower.display_name, f"{{@}} {body}"),
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


def wishes_link(wish_id: uuid.UUID) -> str:
    return f"{settings.web_base_url.rstrip('/')}/wishes#{wish_id}"


def wish_created(author: User, wish_id: uuid.UUID, category_label: str) -> None:
    """有人貼了新願望 → 共用頻道廣播一行。

    **這則不是通知目的地。** 通知目的地的定義是「一個人的通知會送到哪裡，
    每人恰好一個」（CONTEXT.md），而這則廣播沒有特定當事人 —— 所以它直接用
    `TEAMS_CHANNEL_WEBHOOK`，而且**不因為貼文的人有私訊 webhook 就改送私訊**，
    那會讓別人看不到新願望。

    **這三支通知一律不收願望或留言的內文**，連參數都沒有 —— 那比「收了但不用」
    強：它不是一句承諾，是呼叫端根本傳不進來。貼文的人若在共用頻道看到自己剛打的
    字被原樣廣播出去，會學到「這裡講話要小心」，而許願板要的正好相反。
    """
    if not settings.teams_channel_webhook or author is None:
        return
    _fire(
        settings.teams_channel_webhook,
        _card(
            f"🧋 {author.display_name} 許了一個願",
            f"分類：{category_label}\n\n[去看看]({wishes_link(wish_id)})",
            _GOOD,
        ),
    )


def wish_commented(author: User, wish_id: uuid.UUID, who: str) -> None:
    """有人回了你的願望 → 送給貼文者。

    這是整個許願板價值最高的一則：回饋不足的死法是「貼了沒動靜」，這則在堵那個。
    **不上頻道廣播** —— 一則熱門願望的十條留言會把共用頻道洗掉，
    然後下次真的有新願望時沒有人會看。
    """
    _to_author(author, "你的願望有人回了", f"{who} 回了你的願望", wish_id)


def wish_fulfilled(author: User, wish_id: uuid.UUID, who: str) -> None:
    """你的願望實現了 → 送給貼文者。

    這則是刻意補上的。docs/web-spec.md §11 已經記下一個同構的缺口 ——
    「結清沒有通知，債主按了之後借用者不會收到任何訊息」，成因一模一樣
    （單方面宣告、對方不看就不知道）。在一份已經寫下這個缺陷的文件裡，
    新做的東西不該再犯一次。
    """
    _to_author(author, "🎉 你的願望實現了", f"{who} 把它做掉了", wish_id)


def _to_author(author: User, title: str, text: str, wish_id: uuid.UUID) -> None:
    """走通知目的地：有私訊 webhook 就私訊，否則頻道 + @，都沒有就不送。

    **一律不含內容。** 頻道定義上是公開的，而留言裡遲早會出現有人手打進去的
    job 細節；CONTEXT.md 已經立過「頻道通知不含金額」的先例，這裡照同一條走：
    公開目的地一律降級內容，不開特例。
    """
    body = f"{text}\n\n[去看看]({wishes_link(wish_id)})"
    if author is None:
        return
    if author.teams_webhook_url:
        _fire(author.teams_webhook_url, _card(title, body, _GOOD))
        return
    if not settings.teams_channel_webhook:
        return
    _fire(
        settings.teams_channel_webhook,
        _mention_card(author.email, author.display_name, f"{{@}} {body}"),
    )
