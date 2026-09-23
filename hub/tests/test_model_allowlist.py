"""model 白名單。

**Hub 是唯一擋得住的位置。** 2026-09-22 spike #8 實測：`--settings availableModels`
根本不擋 model —— 掛 `.credentials.json` 的舊路徑與環境變數 token 的新路徑都一樣，
CLI 只拿它擋了 fast mode。而 `run-job.sh` 是把 `--model` 直接帶給 CLI 的，前端的
下拉只是方便。

所以這裡擋不住的話，整條鏈就沒有人在擋：委託者直接打 API 帶 `model: "opus"`，
就能用代跑者的額度跑 Opus。這不是「跑錯 model」，是**讓別人欠十倍的錢** ——
Opus 的 output 單價是 Haiku 的 10 倍，而債務照實際花費算（SPEC §4.6）。

⚠️ 2026-09-22（SPEC §4.12）：自動派單那條從「**每一位**都要跑得動」放寬成
「**至少一位**跑得動」。舊的那條是因為當時 Hub 挑不了人（誰先 poll 誰拿到），
只能事先保證每個人都行；現在派單在 Hub，跑不動的人不會被挑中。
站台白名單那條沒有變，它擋的是完全不同的東西。
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from app.routers.jobs import _check_model
from fastapi import HTTPException


def _lender(
    *models: str,
    lid: uuid.UUID | None = None,
    blocked: tuple[str, ...] = (),
) -> SimpleNamespace:
    """一位可接單的代跑者（出借設定）。

    **「他願意」和「他能」是兩件事**（2026-09-23）：`available_models` 是他勾的，
    屬於人；帳號有沒有 usage credits 屬於帳號。`_check_model` 看的是兩者的交集，
    也就是 `runnable_models()` —— 這裡照真的那個算一次，不要只回 available_models，
    否則這組測試會對「勾了但跑不動」完全沒有感覺。

    `blocked` = 他每一個帳號都被回過「要買 usage credits」的 model。
    """
    return SimpleNamespace(
        id=lid or uuid.uuid4(),
        available_models=list(models),
        accepting=True,
        runnable_models=lambda: [m for m in models if m not in blocked],
    )


class _FakeSession:
    """只回答 _check_model 會問的兩件事：

      scalars  現在有哪些可接單的代跑者
      scalar   站台上還有沒有**任何**跑得動的出借帳號（`_anyone_can_run`）

    查詢條件（accepting、needs_reauth）由 SQL 負責，這裡不重現它 ——
    重現一次等於測到自己寫的假貨，不是測那條規則。
    """

    def __init__(self, lenders: list[SimpleNamespace], *, alive: bool) -> None:
        self.lenders = lenders
        self.alive = alive

    async def scalars(self, _stmt):
        return self.lenders

    async def scalar(self, _stmt):
        return uuid.uuid4() if self.alive else None


def check(model: str, lending_id=None, lenders=None, alive: bool = True):
    """`alive` 預設為真 —— 多數測試在意的是 model，不是憑證死活。"""
    return asyncio.run(
        _check_model(model, lending_id, _FakeSession(lenders or [], alive=alive))
    )


# --- 站台白名單 -----------------------------------------------------------


def test_model_outside_the_site_allowlist_is_refused() -> None:
    """就算有代跑者開放 opus，站台不收就是不收。"""
    with pytest.raises(HTTPException) as e:
        check("opus", lenders=[_lender("sonnet", "haiku", "opus")])
    assert e.value.status_code == 400
    assert "opus" in e.value.detail


def test_made_up_model_is_refused() -> None:
    with pytest.raises(HTTPException) as e:
        check("gpt-4", lenders=[_lender("sonnet", "haiku")])
    assert e.value.status_code == 400


def test_allowed_models_pass() -> None:
    for m in ("sonnet", "haiku", "fable"):
        check(m, lenders=[_lender("sonnet", "haiku", "fable")])  # 不拋就是過


# --- Fable（2026-09-23）------------------------------------------------------
#
# Fable 在站台白名單上，但不在任何人的預設條件裡（schemas.DEFAULT_MODELS）。
# 所以擋住 Fable 的不是第一段（站台不收），是第二段（沒人開）。這兩個測試
# 釘住的是「Fable 是 opt-in，不是 opt-out」—— 改成預設就開的話這裡會先叫。


def test_fable_is_on_the_site_allowlist_but_off_by_default() -> None:
    from app.schemas import DEFAULT_MODELS, SITE_MODELS

    assert "fable" in SITE_MODELS
    assert "fable" not in DEFAULT_MODELS


def test_fable_refused_when_nobody_opened_it() -> None:
    """一群只開預設 model 的代跑者，Fable 的 job 要在送出時就被退回，不是排隊。"""
    with pytest.raises(HTTPException) as e:
        check("fable", lenders=[_lender("sonnet", "haiku"), _lender("haiku")])
    assert e.value.status_code == 400
    assert "fable" in e.value.detail


def test_fable_passes_when_one_lender_opened_it() -> None:
    check("fable", lenders=[_lender("sonnet", "haiku"), _lender("sonnet", "fable")])


def test_opened_fable_but_no_account_has_credits_is_refused() -> None:
    """勾了 Fable ≠ 跑得動 Fable。

    帳號被回過 `credits_required` 之後就不該再算數 —— 派給他只會再失敗一次，
    而委託者看到的是一個「送出去才發現沒用」的下拉選項。
    """
    with pytest.raises(HTTPException) as e:
        check("fable", lenders=[_lender("sonnet", "fable", blocked=("fable",))])
    assert e.value.status_code == 400
    assert "fable" in e.value.detail


def test_other_models_still_pass_when_only_fable_is_blocked() -> None:
    """credits 標記只擋那一個 model —— Sonnet 與 Haiku 完全不受影響。"""
    check("sonnet", lenders=[_lender("sonnet", "fable", blocked=("fable",))])


# --- 指定代跑者 -----------------------------------------------------------


def test_requested_lender_must_offer_it() -> None:
    wid = uuid.uuid4()
    with pytest.raises(HTTPException) as e:
        check("sonnet", lending_id=wid, lenders=[_lender("haiku", lid=wid)])
    assert e.value.status_code == 400
    assert "沒有開放" in e.value.detail


def test_requested_lender_that_offers_it_passes() -> None:
    wid = uuid.uuid4()
    check("sonnet", lending_id=wid, lenders=[_lender("sonnet", "haiku", lid=wid)])


# --- 自動派單 -------------------------------------------------------------


def test_auto_passes_when_at_least_one_lender_offers_it() -> None:
    """聯集，不是交集 —— 而且這是 2026-09-22 改掉的方向（SPEC §4.12）。

    派單現在在 Hub：`_claim` 只會把 job 派給 available_models 含這個 model 的人。
    所以「有一個人關掉 Sonnet」不再是「全站不能送 Sonnet」的理由，
    而舊的交集規則正是那個效果。
    """
    check("sonnet", lenders=[_lender("sonnet", "haiku"), _lender("haiku")])


def test_auto_refused_when_nobody_offers_it() -> None:
    """一個人都跑不動就要當場說 —— 讓它排隊到過期，使用者會以為是沒人上線。"""
    with pytest.raises(HTTPException) as e:
        check("sonnet", lenders=[_lender("haiku"), _lender("haiku")])
    assert e.value.status_code == 400
    assert "sonnet" in e.value.detail


# --- 沒人在線 -------------------------------------------------------------


def test_nobody_accepting_but_credentials_alive_is_not_the_users_fault() -> None:
    """一位都沒在接單就不在這裡擋 —— job 排隊等人上線是正常狀態。

    在這裡回 400 的話，使用者會以為自己挑錯 model，而實際上只是還沒人上工。
    半夜送一個、早上有人開機才跑，是這個工具該支援的用法。
    """
    check("sonnet", lenders=[], alive=True)


def test_every_credential_dead_is_a_dead_end_not_a_queue() -> None:
    """⚠️ 2026-09-22：「沒人在線」與「一個可用帳號都沒有」是兩件事。

    後者不是等待，是死路 —— 排隊只會把失敗延後 15 分鐘，而使用者會以為
    自己在等一個會來的人。實際發生過：唯一的帳號授權失效，job 停在排隊中。
    """
    with pytest.raises(HTTPException) as e:
        check("sonnet", lenders=[], alive=False)
    assert e.value.status_code == 400
    assert "授權" in e.value.detail
