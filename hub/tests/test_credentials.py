"""派單前的 token 更新。SPEC §11 #13。

這一層的每一條分支都對應一種真實故障，而**選錯分支的代價不對稱**：
把「暫時連不到」當成「憑證死了」會停掉一個好帳號並叫人重跑授權（那會作廢他
還能用的憑證）；反過來最多只是一個 job 失敗。所以測試盯的是那個不對稱。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from app import credentials, oauth, secrets_box
from app.config import settings
from app.enums import CredentialKind


@pytest.fixture(autouse=True)
def key():
    old = settings.token_encryption_key
    settings.token_encryption_key = secrets_box.generate_key()
    yield
    settings.token_encryption_key = old


class _Session:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


def _account(*, kind=CredentialKind.OAUTH, expires_in_minutes=600, refresh=True):
    return SimpleNamespace(
        credential_kind=kind,
        oauth_token_enc=secrets_box.seal("sk-ant-oat01-current"),
        refresh_token_enc=secrets_box.seal("refresh-me") if refresh else None,
        access_expires_at=(
            datetime.now(UTC) + timedelta(minutes=expires_in_minutes)
            if expires_in_minutes is not None
            else None
        ),
        refresh_expires_at=None,
        scopes=[],
        needs_reauth=False,
    )


def test_setup_token_accounts_are_never_renewed() -> None:
    """一年期、沒有 refresh token —— 對它呼叫 refresh 只會炸。"""
    a = _account(kind=CredentialKind.SETUP_TOKEN, expires_in_minutes=None)
    assert credentials.needs_renewal(a) is False


def test_a_token_expiring_inside_the_margin_is_renewed() -> None:
    """餘裕比 CLI 的五分鐘寬，因為**判斷是在派單那一刻做的** ——
    job 最長跑十分鐘，卡五分鐘的話 token 會在 job 跑到一半時死掉。"""
    assert credentials.needs_renewal(_account(expires_in_minutes=10)) is True
    assert credentials.needs_renewal(_account(expires_in_minutes=600)) is False


def test_missing_expiry_does_not_trigger_a_guess() -> None:
    """資料有問題時放行，不要在派單路徑上亂猜 ——
    放行最多失敗一個 job，猜錯會把一個好帳號停掉。"""
    assert credentials.needs_renewal(_account(expires_in_minutes=None)) is False
    assert credentials.needs_renewal(_account(refresh=False)) is False


def test_a_fresh_token_is_handed_over_untouched(monkeypatch) -> None:
    called = False

    async def never(*a, **k):
        nonlocal called
        called = True

    monkeypatch.setattr(oauth, "refresh", never)
    got = asyncio.run(credentials.access_token(_account(), _Session()))
    assert got == "sk-ant-oat01-current" and not called


def test_renewal_stores_the_new_pair(monkeypatch) -> None:
    async def fake(_rt, **k):
        return oauth.Tokens(
            access_token="sk-ant-oat01-new",
            refresh_token="refresh-next",
            expires_in=28800,
            refresh_expires_in=2592000,
            scopes=("user:inference", "user:profile"),
        )

    monkeypatch.setattr(oauth, "refresh", fake)
    a, s = _account(expires_in_minutes=1), _Session()
    got = asyncio.run(credentials.access_token(a, s))
    assert got == "sk-ant-oat01-new"
    # 新的 refresh token 一定要存回去，否則下一次就換不到了。
    assert secrets_box.open_(a.refresh_token_enc) == "refresh-next"
    assert a.access_expires_at > datetime.now(UTC) + timedelta(hours=7)
    assert s.commits == 1


def test_a_dead_refresh_token_stops_the_account(monkeypatch) -> None:
    """只有代跑者重新授權救得回來 —— 繼續派給他只是讓每個 job 都失敗一次。"""

    async def dead(_rt, **k):
        raise oauth.ReauthorizeNeeded("死了")

    monkeypatch.setattr(oauth, "refresh", dead)
    a, s = _account(expires_in_minutes=1), _Session()
    assert asyncio.run(credentials.access_token(a, s)) is None
    assert a.needs_reauth is True


def test_a_network_blip_does_not_stop_the_account(monkeypatch) -> None:
    """**這條是這組測試存在的理由。** 一次逾時就把帳號停掉，會叫人去重跑授權，
    而重跑會作廢他還能用的憑證 —— 用一個暫時的問題換一個永久的。"""

    async def blip(_rt, **k):
        raise oauth.OAuthError("連不到")

    monkeypatch.setattr(oauth, "refresh", blip)
    a, s = _account(expires_in_minutes=1), _Session()
    got = asyncio.run(credentials.access_token(a, s))
    assert got == "sk-ant-oat01-current"  # 手上那張可能還有幾分鐘，讓它去跑
    assert a.needs_reauth is False
    assert s.commits == 0
