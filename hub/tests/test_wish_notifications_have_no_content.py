"""許願板的三則通知一律不含內容，而且是**結構上**不含。

為什麼要專門一組測試：這三則裡有一則會廣播到**共用頻道**，另外兩則在當事人沒填
webhook 時也會落到頻道，而牆上的字是使用者自由打的 —— 留言裡遲早會出現有人手打
進去的 job 細節。CONTEXT.md 已經立過「頻道通知不含金額」的先例，
web-spec §12 把它擴成「公開目的地一律降級內容」。

初版的三支函式收了 `_body` 卻不使用，靠這裡斷言「內文沒有出現在 payload 裡」。
那是一句承諾。**現在它們的簽章根本收不到內文** —— 下面第一個測試釘的就是這件事，
因為它擋得住的東西比字串比對多：下一個人想把內容放進通知，得先改簽章。
"""

from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace

import pytest
from app import notify
from app.config import settings

CHANNEL = "https://example.invalid/channel"
PRIVATE = "https://example.invalid/private"

WISH_NOTIFIERS = (notify.wish_created, notify.wish_commented, notify.wish_fulfilled)


@pytest.fixture
def sent(monkeypatch):
    out: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        notify, "_fire", lambda url, payload: out.append((url, payload))
    )
    monkeypatch.setattr(settings, "teams_channel_webhook", CHANNEL)
    return out


def _person(*, webhook: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        email="someone@example.com",
        display_name="Vivian",
        teams_webhook_url=webhook,
    )


def _urls(sent) -> list[str]:
    return [url for url, _ in sent]


@pytest.mark.parametrize("fn", WISH_NOTIFIERS, ids=lambda f: f.__name__)
def test_the_body_cannot_even_be_passed_in(fn) -> None:
    """簽章裡沒有內文參數。

    這比「有傳但沒用到」強：要把願望或留言的字放進通知，得先改這個簽章，
    而那一刻這個測試會紅。
    """
    params = set(inspect.signature(fn).parameters)
    assert not params & {"body", "_body", "text", "content", "comment"}


def test_new_wish_goes_to_the_channel(sent) -> None:
    notify.wish_created(_person(webhook=None), uuid.uuid4(), "壞掉了")
    assert _urls(sent) == [CHANNEL]


def test_new_wish_broadcast_ignores_personal_webhooks(sent) -> None:
    """這則沒有特定當事人，所以它**不是通知目的地**（CONTEXT.md）——
    不能因為貼文的人有私訊 webhook 就改送私訊，那會讓別人看不到新願望。"""
    notify.wish_created(_person(webhook=PRIVATE), uuid.uuid4(), "壞掉了")
    assert _urls(sent) == [CHANNEL]


def test_comment_notifies_the_author_not_the_channel(sent) -> None:
    """留言不上頻道廣播：一則熱門願望的十條留言會把共用頻道洗掉。"""
    notify.wish_commented(_person(webhook=PRIVATE), uuid.uuid4(), "阿明")
    assert _urls(sent) == [PRIVATE]


def test_comment_falls_back_to_the_channel_when_there_is_no_webhook(sent) -> None:
    """沒填 webhook 的人走頻道通知（＋@），那是零設定的預設目的地。"""
    notify.wish_commented(_person(webhook=None), uuid.uuid4(), "阿明")
    assert _urls(sent) == [CHANNEL]


def test_fulfilled_notifies_the_author(sent) -> None:
    """§11 已經記下「結清沒有通知」這個同構的缺口，這裡不再犯一次。"""
    notify.wish_fulfilled(_person(webhook=PRIVATE), uuid.uuid4(), "阿明")
    assert _urls(sent) == [PRIVATE]


def test_nothing_is_sent_when_there_is_no_destination_at_all(monkeypatch) -> None:
    """「沒有目的地」是三種目的地之一，不是錯誤狀態（CONTEXT.md）。"""
    out: list = []
    monkeypatch.setattr(notify, "_fire", lambda url, payload: out.append(url))
    monkeypatch.setattr(settings, "teams_channel_webhook", None)
    notify.wish_commented(_person(webhook=None), uuid.uuid4(), "阿明")
    notify.wish_created(_person(webhook=None), uuid.uuid4(), "壞掉了")
    assert out == []
