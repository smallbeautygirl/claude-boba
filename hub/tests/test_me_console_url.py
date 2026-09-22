"""`/me` 給前端的 MinIO console 網址。

**只回給管理者**（2026-09-22）：那個 console 沒有「只看自己」的權限，登進去
看得到所有人的對話與檔案。靠前端不渲染是不夠的 —— 沒渲染不等於沒送出去。

這裡驗的是**預設值是空的**，不是覆蓋率。空字串是安全相關的決定：MinIO console
沒有「只看自己」這種權限，登進去就看得到所有人的 transcript、附件與產出，
所以只有「我的 worker」頁會顯示它，而且沒設 `S3_CONSOLE_URL` 的站台整段不顯示。

有人日後把預設改成某個值（例如從 `S3_PUBLIC_ENDPOINT` 推一個埠號），沒有這個
測試就沒有東西會擋 —— 每個站台的出租者頁面會自己長出一個入口。
"""

from __future__ import annotations

import uuid

import pytest
from app.auth import require_user
from app.config import Settings, settings
from app.main import app
from app.models import User
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    app.dependency_overrides[require_user] = lambda: User(
        id=uuid.uuid4(), email="v@example.com", display_name="vivianfan"
    )
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_default_is_empty() -> None:
    """沒有任何 env 時的預設值。`.env` 讀不進來才是這條要問的事。"""
    assert Settings(_env_file=None).s3_console_url == ""


def test_me_reports_no_console_when_unset(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "s3_console_url", "")
    body = client.get("/api/auth/me").json()
    # 鍵一定要在，否則前端拿到 undefined —— 那跟「沒設」是不同的意思。
    assert "s3_console_url" in body
    assert body["s3_console_url"] == ""


def test_non_admin_never_gets_the_console_url(client, monkeypatch) -> None:
    """設了網址、但這個人不是管理者 —— 欄位要是空的，不只是前端不顯示。"""
    monkeypatch.setattr(settings, "s3_console_url", "http://10.0.0.1:9001")
    monkeypatch.setattr(settings, "admin_emails", "someone.else@example.com")
    body = client.get("/api/auth/me").json()
    assert body["is_admin"] is False
    assert body["s3_console_url"] == ""


def test_admin_gets_the_configured_url(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "s3_console_url", "http://10.0.0.1:9001")
    # 大小寫與空白都跟設定裡不一樣 —— 比對前要正規化，不然「設了卻不生效」。
    monkeypatch.setattr(settings, "admin_emails", "  V@Example.COM , other@x.com ")
    body = client.get("/api/auth/me").json()
    assert body["is_admin"] is True
    assert body["s3_console_url"] == "http://10.0.0.1:9001"
