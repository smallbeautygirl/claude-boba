"""認證來源沒設就拒絕啟動。

2026-09-23 加的。在此之前，端點與 service id 寫死在 `config.py` 的預設值裡 ——
那讓這個 repo 一旦公開，就連同某個組織的內部位址與服務註冊編號一起發布。
它們不是憑證，但也沒有任何理由由程式碼提供。

改成必填之後，真正的風險換成另一個：**Hub 會正常啟動、登入頁會正常顯示，
然後每一次登入都失敗**，而錯誤長得像「帳號密碼錯了」。所以要在啟動時就爆。

這個測試釘的是那道檢查本身。有人為了「本地跑起來方便」把預設值加回去時，
它要炸。
"""

from __future__ import annotations

import pytest
from app import main
from app.config import Settings, settings


def test_no_endpoint_is_baked_into_the_code() -> None:
    """預設值必須是空的 —— 讀不到 `.env` 時也不該有一個位址冒出來。"""
    fresh = Settings(_env_file=None)
    assert fresh.observ_base_url == ""
    assert fresh.observ_service_id == ""
    # 2026-09-24：middleware 的位址同一條規則。它是內網位址，「不用想就有值」由
    # hub.env.example 負責，不由程式碼負責。
    assert fresh.middleware_base_url == ""


@pytest.mark.parametrize(
    ("base_url", "service_id", "middleware", "expected"),
    [
        ("", "sid", "https://mw.example.com", "OBSERV_BASE_URL"),
        ("https://x.example.com", "", "https://mw.example.com", "OBSERV_SERVICE_ID"),
        ("   ", "sid", "https://mw.example.com", "OBSERV_BASE_URL"),
        ("https://x.example.com", "sid", "", "MIDDLEWARE_BASE_URL"),
    ],
)
def test_startup_refuses_when_missing(
    monkeypatch, base_url: str, service_id: str, middleware: str, expected: str
) -> None:
    monkeypatch.setattr(settings, "observ_base_url", base_url)
    monkeypatch.setattr(settings, "observ_service_id", service_id)
    monkeypatch.setattr(settings, "middleware_base_url", middleware)
    with pytest.raises(RuntimeError) as err:
        main._check_observ()
    # 訊息要點名是哪一個沒設，不然設了一個漏一個的人會重跑三次才問對問題。
    assert expected in str(err.value)


def test_startup_passes_when_all_are_set(monkeypatch) -> None:
    monkeypatch.setattr(settings, "observ_base_url", "https://x.example.com")
    monkeypatch.setattr(settings, "observ_service_id", "sid")
    monkeypatch.setattr(settings, "middleware_base_url", "https://mw.example.com")
    main._check_observ()
