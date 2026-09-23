"""台幣粗估的匯率。

釘的是**一條規則**：拿不到牌價時回 `None` 加上原因，**絕不回一個猜的數字**。

管理頁的第一條規則是「量不到的東西連同原因一起講，不要放一顆永遠綠的燈」
（`app/routers/admin.py` 開頭）。一個在來源掛掉時默默退回寫死係數的匯率，
就是那顆燈的金額版 —— 它看起來像查到的，而且沒有人會發現它不是。
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
from app import fxrate
from app.config import settings

# 用 anyio 的 plugin（隨 anyio 一起裝的），不是 pytest-asyncio。
pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _clean_cache():
    # 模組層的快取會跨測試留下來：不清的話，前一個測試抓到的價會讓
    # 「來源掛掉」那幾個測試變成在測快取，而不是在測失敗路徑。
    fxrate._reset_for_tests()
    yield
    fxrate._reset_for_tests()


def _payload(rows: list[dict]) -> dict:
    return {"msg": "success", "status": 200, "data": rows}


def test_parse_takes_the_newest_day_not_the_last_row():
    """照日期取最大的，不信任回傳順序。

    信任順序的話，來源哪天把排序反過來，錯法會是「悄悄用了一週前的價」——
    那種錯不會讓任何東西變紅，只會讓數字慢慢變得不對。
    """
    rate = fxrate._parse(
        _payload(
            [
                {"date": "2026-09-22", "spot_sell": 31.75},
                {"date": "2026-09-19", "spot_sell": 31.81},
            ]
        )
    )
    assert rate is not None
    assert rate.twd_per_usd == Decimal("31.75")
    assert rate.quoted_on == "2026-09-22"


def test_parse_skips_rows_without_a_usable_spot_sell():
    """假日的列會有 0 或 null。挑到那一筆的話台幣會變成 0，而畫面不會喊。"""
    rate = fxrate._parse(
        _payload(
            [
                {"date": "2026-09-22", "spot_sell": 0},
                {"date": "2026-09-21", "spot_sell": None},
                {"date": "2026-09-19", "spot_sell": 31.81},
            ]
        )
    )
    assert rate is not None
    assert rate.quoted_on == "2026-09-19"


@pytest.mark.parametrize(
    "payload",
    [
        {"msg": "success", "status": 200, "data": []},
        {"msg": "success", "status": 200, "data": [{"date": "2026-09-22"}]},
        {"data": "不是清單"},
        "根本不是 JSON 物件",
    ],
)
def test_parse_returns_none_instead_of_guessing(payload):
    assert fxrate._parse(payload) is None


async def test_disabled_source_says_so(monkeypatch):
    monkeypatch.setattr(settings, "fx_api_url", "")
    rate, note = await fxrate.usd_to_twd()
    assert rate is None
    assert "FX_API_URL" in note


async def test_unreachable_source_gives_no_number_and_a_reason(monkeypatch):
    monkeypatch.setattr(settings, "fx_api_url", "https://example.invalid/api")

    class _Boom:
        def __init__(self, *a, **kw): ...
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **kw):
            raise httpx.ConnectError("nope")

    monkeypatch.setattr(httpx, "AsyncClient", _Boom)
    rate, note = await fxrate.usd_to_twd()
    assert rate is None
    assert "打不到" in note


async def test_last_good_rate_survives_a_later_failure(monkeypatch):
    """來源掛掉時回上一次的價，而且說出它是什麼時候抓的。

    整個台幣欄位消失也是一種選擇，但那會讓管理者以為「這功能壞了」。
    舊的價 + 一句「現在打不到」講的是實話，而牌價日期本來就印在畫面上。
    """
    monkeypatch.setattr(settings, "fx_api_url", "https://example.invalid/api")

    state = {"fail": False}

    class _Client:
        def __init__(self, *a, **kw): ...
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **kw):
            if state["fail"]:
                raise httpx.ConnectError("nope")
            return httpx.Response(
                200,
                json=_payload([{"date": "2026-09-22", "spot_sell": 31.75}]),
                request=httpx.Request("GET", "https://example.invalid/api"),
            )

    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    rate, note = await fxrate.usd_to_twd()
    assert rate is not None and rate.twd_per_usd == Decimal("31.75")
    assert note == ""

    state["fail"] = True
    monkeypatch.setattr(fxrate, "_cached_at", 0.0)  # 讓快取過期，強迫它再去打一次
    rate, note = await fxrate.usd_to_twd()
    assert rate is not None and rate.twd_per_usd == Decimal("31.75")
    assert "打不到來源" in note


async def test_cache_stops_a_second_call_from_hitting_the_network(monkeypatch):
    """管理頁每次重新整理都打一次外部服務的話，換到的精度是零。"""
    monkeypatch.setattr(settings, "fx_api_url", "https://example.invalid/api")
    calls = {"n": 0}

    class _Client:
        def __init__(self, *a, **kw): ...
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **kw):
            calls["n"] += 1
            return httpx.Response(
                200,
                json=_payload([{"date": "2026-09-22", "spot_sell": 31.75}]),
                request=httpx.Request("GET", "https://example.invalid/api"),
            )

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    await fxrate.usd_to_twd()
    await fxrate.usd_to_twd()
    assert calls["n"] == 1
