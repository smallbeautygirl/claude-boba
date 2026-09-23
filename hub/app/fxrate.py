"""美金 → 台幣的粗估匯率。**只給管理頁的累計花費用。**

這個數字是給站台管理者一個量級感（「這個月燒掉的是一杯咖啡還是一頓飯」），
不是帳。帳單是美金，對帳一律以美金為準 —— 所以呈現時一定要有「約」，
而且美金要在台幣前面。

### 為什麼不直接抓 rate.bot.com.tw

台銀官網對非瀏覽器的請求一律回一段 JavaScript 的 bot challenge（2026-09-22
實測 `/xrt/flcsv/0/day`、`/xrt/fltxt/0/day`、`/xrt?Lang=zh-TW` 三個路徑，
換成瀏覽器的 User-Agent 也一樣）。拿得到的是挑戰頁，不是牌價。

硬解那段挑戰等於在 hub 裡養一隻爬蟲，而它會在對方調整防護的那天無聲壞掉 ——
管理頁的第一條規則就是不要有假的綠燈。所以改走 FinMind 的
`TaiwanExchangeRate`，它是**台銀牌價的鏡像**（現金買入／賣出、即期買入／賣出
四欄跟台銀對得起來）。

**它是鏡像，不是台銀本人。** 前端那句話寫的是「臺灣銀行牌價（經 FinMind）」，
不是「臺灣銀行」—— 換來源要一起改那句話。

### 取即期賣出（spot_sell）

這個數字要回答的是「這些美金的帳換成台幣大概要付多少」，付錢的那一側是賣出。
跟即期買入差幾角，對粗估沒有差別，但寫下來省得下一個人自己猜一個。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

import httpx

from .config import settings

_DATASET = "TaiwanExchangeRate"
# 快取 30 分鐘。台銀一天只調幾次牌價，而這個數字只出現在管理頁 ——
# 每開一次頁就打一次外部服務，換到的精度是零。
_TTL_SECONDS = 30 * 60
# 假日沒有牌價。往回要一週，連假第三天才不會變成「查不到」。
_LOOKBACK_DAYS = 7


@dataclass(frozen=True)
class Rate:
    """一筆牌價。`quoted_on` 是牌價的日期，不是我們抓到它的時間。

    兩者要分開：連假期間抓到的是假期前最後一個營業日的價，
    而畫面上要能看出這件事，不然「即時」會是一句謊話。
    """

    twd_per_usd: Decimal
    quoted_on: str
    fetched_at: datetime

    @property
    def source_label(self) -> str:
        return "臺灣銀行牌價 · 即期賣出（經 FinMind）"


_cached: Rate | None = None
_cached_at = 0.0
_error = ""


async def usd_to_twd() -> tuple[Rate | None, str]:
    """回 `(匯率, 說明)`。拿不到就回 `(None, 原因)` —— **絕不回一個猜的數字。**

    上一次成功的值會留著：來源掛掉時回那一筆，而不是讓台幣整個消失。
    這時候說明欄會講「這是幾點抓的、現在打不到」，日期也還在 `quoted_on` 裡，
    所以看的人知道自己在看舊的價。
    """
    global _cached, _cached_at, _error

    if not settings.fx_api_url:
        return None, "沒有設定匯率來源（FX_API_URL）"

    if _cached is not None and time.monotonic() - _cached_at < _TTL_SECONDS:
        return _cached, ""

    start = (datetime.now(UTC) - timedelta(days=_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    try:
        async with httpx.AsyncClient(timeout=settings.fx_timeout) as client:
            resp = await client.get(
                settings.fx_api_url,
                params={
                    "dataset": _DATASET,
                    "data_id": "USD",
                    "start_date": start,
                },
            )
        resp.raise_for_status()
        rate = _parse(resp.json())
    except Exception as exc:  # noqa: BLE001 —— 匯率抓不到不該讓管理頁 500
        _error = f"{type(exc).__name__}: {str(exc)[:120]}"
        if _cached is not None:
            fetched = _cached.fetched_at.strftime("%m-%d %H:%M")
            return _cached, f"這是 {fetched}（UTC）抓的，現在打不到來源"
        return None, f"打不到匯率來源（{_error}）"

    if rate is None:
        _error = "來源回了 200，但裡面沒有可用的 USD 牌價"
        return _cached, _error if _cached is None else f"沿用上一次的：{_error}"

    _cached, _cached_at, _error = rate, time.monotonic(), ""
    return rate, ""


def _parse(payload: object) -> Rate | None:
    """挑**最新一天**、而且 `spot_sell` 是正數的那一筆。

    FinMind 的 `data` 是日期由舊到新。不假設它一定排好，直接照日期取最大的 ——
    排序反過來的那天，錯法會是「悄悄用了一週前的價」，那種錯不會有人發現。
    """
    if not isinstance(payload, dict):
        return None
    rows = payload.get("data")
    if not isinstance(rows, list):
        return None

    best: tuple[str, Decimal] | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        day = row.get("date")
        try:
            value = Decimal(str(row.get("spot_sell")))
        except (InvalidOperation, TypeError):
            continue
        if not isinstance(day, str) or value <= 0:
            continue
        if best is None or day > best[0]:
            best = (day, value)

    if best is None:
        return None
    return Rate(twd_per_usd=best[1], quoted_on=best[0], fetched_at=datetime.now(UTC))


def _reset_for_tests() -> None:
    """清掉模組層的快取。測試之間不清的話，前一個測試抓到的價會留到下一個。"""
    global _cached, _cached_at, _error
    _cached, _cached_at, _error = None, 0.0, ""
