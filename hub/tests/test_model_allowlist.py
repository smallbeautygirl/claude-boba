"""model 白名單。

**Hub 是唯一擋得住的位置。** 2026-09-22 spike #8 實測：`--settings availableModels`
根本不擋 model —— 掛 `.credentials.json` 的舊路徑與環境變數 token 的新路徑都一樣，
CLI 只拿它擋了 fast mode。而 `run-job.sh` 是把 `--model` 直接帶給 CLI 的，前端的
下拉只是方便。

所以這裡擋不住的話，整條鏈就沒有人在擋：借用者直接打 API 帶 `model: "opus"`，
就能用出租者的額度跑 Opus。這不是「跑錯 model」，是**讓別人欠十倍的錢** ——
Opus 的 output 單價是 Haiku 的 10 倍，而債務照實際花費算（SPEC §4.6）。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from app.routers.jobs import _check_model
from fastapi import HTTPException


def _worker(*models: str, wid: uuid.UUID | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=wid or uuid.uuid4(),
        available_models=list(models),
        accepting=True,
        last_seen_at=datetime.now(UTC),
    )


class _FakeSession:
    """只回答 _check_model 會問的那一件事：現在有哪些可接單的 worker。

    查詢條件（accepting、last_seen_at）由 SQL 負責，這裡不重現它 ——
    重現一次等於測到自己寫的假貨，不是測那條規則。
    """

    def __init__(self, workers: list[SimpleNamespace]) -> None:
        self.workers = workers

    async def scalars(self, _stmt):
        return self.workers


def check(model: str, worker_id=None, workers=None):
    return asyncio.run(_check_model(model, worker_id, _FakeSession(workers or [])))


# --- 站台白名單 -----------------------------------------------------------


def test_model_outside_the_site_allowlist_is_refused() -> None:
    """就算有出租者開放 opus，站台不收就是不收。"""
    with pytest.raises(HTTPException) as e:
        check("opus", workers=[_worker("sonnet", "haiku", "opus")])
    assert e.value.status_code == 400
    assert "opus" in e.value.detail


def test_made_up_model_is_refused() -> None:
    with pytest.raises(HTTPException) as e:
        check("gpt-4", workers=[_worker("sonnet", "haiku")])
    assert e.value.status_code == 400


def test_allowed_models_pass() -> None:
    for m in ("sonnet", "haiku"):
        check(m, workers=[_worker("sonnet", "haiku")])  # 不拋就是過


# --- 指定出租者 -----------------------------------------------------------


def test_requested_worker_must_offer_it() -> None:
    wid = uuid.uuid4()
    with pytest.raises(HTTPException) as e:
        check("sonnet", worker_id=wid, workers=[_worker("haiku", wid=wid)])
    assert e.value.status_code == 400
    assert "沒有開放" in e.value.detail


def test_requested_worker_that_offers_it_passes() -> None:
    wid = uuid.uuid4()
    check("sonnet", worker_id=wid, workers=[_worker("sonnet", "haiku", wid=wid)])


# --- 自動派單 -------------------------------------------------------------


def test_auto_requires_every_worker_to_offer_it() -> None:
    """交集不是聯集。

    派單不按 model 過濾（poll 只看 requested_worker_id），所以只要有一台
    可接單的機器跑不動它，這個 job 就可能派給那台然後失敗 —— 而失敗不計債，
    那台機器的額度就白燒了。
    """
    with pytest.raises(HTTPException) as e:
        check("sonnet", workers=[_worker("sonnet", "haiku"), _worker("haiku")])
    assert e.value.status_code == 400
    assert "自動派單" in e.value.detail


def test_auto_passes_when_all_workers_offer_it() -> None:
    check("haiku", workers=[_worker("sonnet", "haiku"), _worker("haiku")])


# --- 沒人在線 -------------------------------------------------------------


def test_no_workers_online_is_not_the_users_fault() -> None:
    """一台都沒有就不在這裡擋 —— job 排隊等人上線是正常狀態。

    在這裡回 400 的話，使用者會以為自己挑錯 model，而實際上只是還沒人上工。
    派不出去的處理在別處（expired）。
    """
    check("sonnet", workers=[])
