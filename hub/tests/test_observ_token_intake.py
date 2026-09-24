"""「查 Observ 事件」收單時對委託者 Observ token 的三道檢查，以及它絕不外流。

2026-09-24。這張 token 是委託者對公司系統的完整身分（72 小時 JWT），不是代跑者的
Claude 額度。security.md 紅線 2 多了一列講它；這裡把那一列釘成測試：

1. 只有那個指令才收（其他 job 上不准有它）
2. 必須是本人的（否則任何登入者都能用別人的身分查事件）
3. 剩餘效期要夠 job 排隊＋跑完（否則花了額度才 401）
4. 不進 JobDetail、不進派單 payload 以外的任何回應
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from app import observ, secrets_box
from app.config import settings
from app.routers.jobs import _observ_token_for
from app.schemas import OBSERV_COMMAND, JobDetail, WorkerJob, needs_observ_token
from fastapi import HTTPException


def _jwt(exp: datetime) -> str:
    """一張只有 exp 的假 JWT。簽章隨便 —— hub 不驗簽章，身分由 whoami 向 Observ 驗。"""
    header = base64.urlsafe_b64encode(b'{"alg":"RS256"}').rstrip(b"=").decode()
    payload = (
        base64.urlsafe_b64encode(json.dumps({"exp": int(exp.timestamp())}).encode())
        .rstrip(b"=")
        .decode()
    )
    return f"{header}.{payload}.sig"


@pytest.fixture(autouse=True)
def key():
    old = settings.token_encryption_key
    settings.token_encryption_key = secrets_box.generate_key()
    yield
    settings.token_encryption_key = old


ME = SimpleNamespace(id=uuid.uuid4(), observ_user_id=42)
FRESH = _jwt(datetime.now(UTC) + timedelta(hours=71))


def _whoami(observ_id: int):
    async def fake(token: str) -> observ.ObservUser:
        return observ.ObservUser(id=observ_id, email="pm@example.com")

    return fake


# --- 1. 只有那個指令才收 -------------------------------------------------------


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("/observ-event-lookup 幫我找 1111954 的截圖", True),
        ("  /observ-event-lookup\n1111954", True),
        ("/observ-event-lookup", True),
        ("/pptx 幫我做簡報", False),
        ("幫我 /observ-event-lookup 查一下", False),  # 不在開頭 = 不是這個指令
        ("/observ-event-lookup-extra 1", False),
        ("", False),
    ],
)
def test_only_the_command_prefix_counts(prompt: str, expected: bool) -> None:
    assert needs_observ_token(prompt) is expected


def test_command_name_is_in_the_catalog() -> None:
    """前端鏡像了這個字串（api.ts 的 OBSERV_COMMAND）；清單上要有它，chip 才點得出來。"""
    catalog = json.loads(
        (Path(__file__).resolve().parents[1] / "app/data/commands.json").read_text()
    )
    names = [c["name"] for g in catalog["groups"] for c in g["commands"]]
    assert OBSERV_COMMAND in names


def test_other_jobs_never_store_a_token(monkeypatch) -> None:
    """瀏覽器不該送；送了也不存。一張 72 小時的身分沒有理由躺在用不到它的 job 上。"""
    monkeypatch.setattr(observ, "whoami", _whoami(42))
    assert asyncio.run(_observ_token_for("/pptx 做簡報", FRESH, ME)) is None


def test_the_command_without_a_token_is_refused() -> None:
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_observ_token_for(f"{OBSERV_COMMAND} 1111954", None, ME))
    assert exc.value.status_code == 400
    assert "重新登入" in exc.value.detail


# --- 2. 必須是本人的 -----------------------------------------------------------


def test_someone_elses_token_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(observ, "whoami", _whoami(99))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_observ_token_for(f"{OBSERV_COMMAND} 1", FRESH, ME))
    assert exc.value.status_code == 403


def test_expired_token_is_a_relogin_message(monkeypatch) -> None:
    async def dead(token: str):
        raise observ.ObservError(401, "expired")

    monkeypatch.setattr(observ, "whoami", dead)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_observ_token_for(f"{OBSERV_COMMAND} 1", FRESH, ME))
    assert exc.value.status_code == 400
    # 訊息要講是登入的問題，不能讓人以為指令壞了（web-spec〈缺服務憑證時〉）。
    assert "重新登入" in exc.value.detail


# --- 3. 剩餘效期 ---------------------------------------------------------------


def test_exp_is_read_from_the_jwt() -> None:
    exp = datetime.now(UTC).replace(microsecond=0) + timedelta(hours=5)
    assert observ.token_expires_at(_jwt(exp)) == exp


@pytest.mark.parametrize("token", ["not-a-jwt", "a.b", "a.!!!.c", "a.e30.c"])
def test_unreadable_exp_means_unknown_not_refused(token: str) -> None:
    """不是 JWT 就回 None；呼叫端當「不知道」，不擋 —— 身分還是由 whoami 驗。"""
    assert observ.token_expires_at(token) is None


def test_a_token_about_to_die_is_refused(monkeypatch) -> None:
    """剩 10 分鐘的 token：送出時活著、跑到一半 401，是花了額度才失敗的那種。"""
    monkeypatch.setattr(observ, "whoami", _whoami(42))
    soon = _jwt(datetime.now(UTC) + timedelta(minutes=10))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_observ_token_for(f"{OBSERV_COMMAND} 1", soon, ME))
    assert exc.value.status_code == 400
    assert "快過期" in exc.value.detail


def test_a_fresh_token_is_sealed_not_stored_plain(monkeypatch) -> None:
    monkeypatch.setattr(observ, "whoami", _whoami(42))
    sealed = asyncio.run(_observ_token_for(f"{OBSERV_COMMAND} 1111954", FRESH, ME))
    assert sealed is not None
    assert FRESH.encode() not in sealed
    assert secrets_box.open_(sealed) == FRESH


# --- 4. 不外流 -----------------------------------------------------------------


def test_job_detail_has_no_observ_field() -> None:
    """JobDetail 是給瀏覽器的。它連「有沒有」都不需要知道 —— 那是 job 的內部狀態。"""
    assert not [f for f in JobDetail.model_fields if "observ" in f]


def test_worker_job_carries_it_only_with_its_addresses() -> None:
    """派單 payload 是唯一能帶它的地方，而且要連位址一起，worker 才不用自己猜。"""
    fields = WorkerJob.model_fields
    for name in (
        "observ_token",
        "observ_base_url",
        "observ_service_id",
        "middleware_base_url",
    ):
        assert name in fields
        assert fields[name].default is None
