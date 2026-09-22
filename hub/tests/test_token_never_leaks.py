"""代跑者的長期 token 絕不外流。

`.claude/rules/security.md` 紅線 2 對這種 token 有五條約束，其中三條是「不要
出現在某個地方」—— 而那種規則沒有測試就只是一句話。這裡把它們釘住。

為什麼值得一組專門的測試：一年期 token 外洩**沒有損失上限**。8 小時的 session
token 洩漏還有天然止血點，這個沒有。而最可能發生的外洩不是有人攻進來，是我們
自己在某個回應或錯誤訊息裡把它印出去。
"""

from __future__ import annotations

import inspect
import pathlib
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from app import authorize, secrets_box
from app.config import settings
from app.routers.workers import _lending_view

FAKE_TOKEN = "sk-ant-oat01-" + "z" * 80


@pytest.fixture(autouse=True)
def key():
    old = settings.token_encryption_key
    settings.token_encryption_key = secrets_box.generate_key()
    yield
    settings.token_encryption_key = old


def _worker(*, has_token: bool) -> SimpleNamespace:
    return SimpleNamespace(
        oauth_token_enc=secrets_box.seal(FAKE_TOKEN) if has_token else None,
        job_budget_usd="5.0000",
        available_models=["sonnet", "haiku"],
        allow_full_network=False,
        accepting=True,
        last_seen_at=datetime.now(UTC),
    )


# --- 不回傳給前端 ---------------------------------------------------------


def test_lending_view_has_only_a_boolean() -> None:
    """UI 只能知道「有沒有」，不能拿到值 —— 連遮罩後的也不行。

    這條寫死成「整份回應裡不能有 sk-ant」而不是「不要有 token 欄位」：
    後者擋不住有人把它塞進別的欄位名。
    """
    view = _lending_view(_worker(has_token=True))
    assert view["has_token"] is True
    blob = repr(view)
    assert "sk-ant" not in blob
    assert "z" * 20 not in blob


def test_lending_view_without_a_token() -> None:
    assert _lending_view(_worker(has_token=False))["has_token"] is False


def test_no_endpoint_returns_the_ciphertext_either() -> None:
    """密文也不該出去。它擋得住看一眼，但它**就是** token ——
    只要金鑰也漏了就等於明文，而兩者都在同一台主機上。"""
    view = _lending_view(_worker(has_token=True))
    assert not any(isinstance(v, bytes) for v in view.values())


# --- 不進錯誤訊息 ---------------------------------------------------------


def test_authorize_errors_do_not_echo_pty_output() -> None:
    """pty 的輸出裡可能有 token 的片段，所以錯誤訊息不能把它帶出來。

    用讀原始碼的方式驗：那幾個 raise 不可以插值 buf 或 proc 的內容。
    """
    src = inspect.getsource(authorize)
    for line in src.splitlines():
        if "AuthorizeError(" in line and "raise" in line:
            assert "buf" not in line, f"錯誤訊息引用了 pty buffer：{line.strip()}"
            assert "decode()" not in line, f"錯誤訊息帶出了輸出：{line.strip()}"


def test_secrets_box_offers_no_way_to_peek() -> None:
    """這個模組刻意不提供 repr 或遮罩後的值 ——
    一旦有那個函式，就會有人在 debug 時用它。"""
    names = [n for n in dir(secrets_box) if not n.startswith("_")]
    for banned in ("mask", "masked", "preview", "peek", "hint"):
        assert banned not in names


# --- 加密真的有加 ---------------------------------------------------------


def test_ciphertext_does_not_contain_the_plaintext() -> None:
    blob = secrets_box.seal(FAKE_TOKEN)
    assert b"sk-ant" not in blob
    assert secrets_box.open_(blob) == FAKE_TOKEN


def test_no_key_means_refuse_to_start() -> None:
    """不要默默用明文存（security.md 紅線 2）。"""
    settings.token_encryption_key = ""
    with pytest.raises(secrets_box.SecretsNotConfigured) as e:
        secrets_box.check_configured()
    assert "TOKEN_ENCRYPTION_KEY" in str(e.value)


def test_wrong_key_raises_instead_of_returning_none() -> None:
    """悄悄回 None 的話，呼叫端會以為這個人沒授權過然後叫他重新授權，
    而舊的 token 還在外面有效。"""
    blob = secrets_box.seal(FAKE_TOKEN)
    settings.token_encryption_key = secrets_box.generate_key()
    with pytest.raises(secrets_box.SecretsNotConfigured):
        secrets_box.open_(blob)


# --- 不留在派單 payload 裡 -----------------------------------------------


def test_worker_pops_the_token_out_of_the_job_dict() -> None:
    """worker 收到 job 之後要立刻把 token 抽走。

    留在 dict 裡的話，任何一個 print(job) 都會變成外洩。用讀原始碼驗，
    因為這是「有沒有做那個動作」，不是行為。
    """
    src = (pathlib.Path(__file__).parents[2] / "worker" / "worker.py").read_text()
    assert 'job.pop("oauth_token"' in src, "worker 沒有把 token 從 job dict 裡拿走"
    # 而且要在 run_job 一開頭就做，不是用到才做
    body = src.split("async def run_job(")[1]
    head = body[: body.index("await _prepare_workdir")]
    assert 'job.pop("oauth_token"' in head, "token 不是在 run_job 一開頭就被抽走"
