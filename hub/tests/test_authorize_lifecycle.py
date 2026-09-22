"""授權 session 的生命週期。

這個模組最容易出事的不是 pty，是**生命週期**：一個子行程要活好幾分鐘等人
回來。做不好會漏行程、會卡、會在每次部署之後留下要手動收屍的殭屍。

所以這裡測的是那三條對策，不是「能不能跟 Anthropic 講話」：逾時會被收掉、
重按會換掉舊的、關掉之後行程真的死了、臨時 HOME 真的被刪掉。

**用假指令跑。** 真的跑 `claude setup-token` 會產生一組一年期憑證，而測試
不該留下那種東西（SPEC §11 spike #9 的腳本也是同一個理由刻意不完成授權）。
假指令印一個假的授權網址然後靜靜地等 —— 那正是真指令在那個時間點的行為。
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid

import pytest
from app import authorize

# 印一個網址，然後卡住等 stdin。跟真指令在等人授權時的狀態一樣。
_FAKE = [
    "python3",
    "-c",
    (
        "import sys;"
        "print('open https://claude.com/cai/oauth/authorize?code=true&x=1', flush=True);"
        "sys.stdin.readline()"
    ),
]


@pytest.fixture(autouse=True)
def fake_command(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(authorize, "COMMAND", _FAKE)
    yield
    # 測試之間不要留下 session。
    for wid in list(authorize._sessions):
        authorize.cancel(wid)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_start_returns_the_authorize_url() -> None:
    wid = uuid.uuid4()
    url = asyncio.run(authorize.start(wid))
    assert url.startswith("https://claude.com/cai/oauth/authorize")


def test_pressing_authorize_again_replaces_the_old_session() -> None:
    """不然重複點按鈕會累積行程。"""
    wid = uuid.uuid4()
    asyncio.run(authorize.start(wid))
    first = authorize._sessions[wid].proc.pid

    asyncio.run(authorize.start(wid))
    second = authorize._sessions[wid].proc.pid

    assert first != second
    assert len(authorize._sessions) == 1
    time.sleep(0.2)
    assert not _alive(first), "舊的行程沒有被殺掉"


def test_close_kills_the_process_and_removes_the_temp_home() -> None:
    """臨時 HOME 一定要刪：`claude setup-token` 會把憑證寫進去
    （security.md 紅線 2：絕不寫進檔案系統）。"""
    wid = uuid.uuid4()
    asyncio.run(authorize.start(wid))
    sess = authorize._sessions[wid]
    pid, home = sess.proc.pid, sess.home
    assert os.path.isdir(home)

    authorize.cancel(wid)

    time.sleep(0.2)
    assert not _alive(pid)
    assert not os.path.exists(home), "臨時 HOME 沒有被刪掉"
    assert wid not in authorize._sessions


def test_sweep_collects_expired_sessions() -> None:
    """沒有這條，開了授權頁又關掉的人會留下一個永不結束的行程。"""
    wid = uuid.uuid4()
    asyncio.run(authorize.start(wid))
    pid = authorize._sessions[wid].proc.pid

    # 把它變成已經過期
    authorize._sessions[wid].expires_at = time.monotonic() - 1
    assert authorize.sweep() == 1

    assert wid not in authorize._sessions
    time.sleep(0.2)
    assert not _alive(pid)


def test_sweep_leaves_live_sessions_alone() -> None:
    wid = uuid.uuid4()
    asyncio.run(authorize.start(wid))
    assert authorize.sweep() == 0
    assert wid in authorize._sessions


def test_submitting_a_code_without_a_session_is_a_clean_error() -> None:
    """過期之後再貼碼，要講得出「重新開始」，不要拋 KeyError。"""
    with pytest.raises(authorize.AuthorizeError) as e:
        asyncio.run(authorize.submit_code(uuid.uuid4(), "abc"))
    assert "重新開始" in str(e.value)


def test_a_wrong_code_does_not_leak_pty_output() -> None:
    """錯誤訊息裡不能有 pty 的輸出 —— 那裡面可能有 token 的片段。"""
    wid = uuid.uuid4()
    asyncio.run(authorize.start(wid))
    # 假指令讀完一行就結束，不會吐出 token，所以這裡會走到逾時那條路。
    authorize.TOKEN_TIMEOUT_SECONDS_BACKUP = authorize.TOKEN_TIMEOUT_SECONDS
    authorize.TOKEN_TIMEOUT_SECONDS = 2
    try:
        with pytest.raises(authorize.AuthorizeError) as e:
            asyncio.run(authorize.submit_code(wid, "wrong-code"))
    finally:
        authorize.TOKEN_TIMEOUT_SECONDS = authorize.TOKEN_TIMEOUT_SECONDS_BACKUP
    msg = str(e.value)
    assert "claude.com" not in msg and "https://" not in msg


# --- token 不能被截斷（2026-09-22 沉默了一整天的 bug） ---------------------


_REAL_TOKEN = "sk-ant-oat01-" + "Aa1_-" * 18  # 103 字元，比一行寬


def _dribbling_command(token: str, chunk: int = 17, gap: float = 0.05) -> list[str]:
    """把 token 拆成好幾次寫出去 —— 真指令就是這樣吐字的。

    這是整個 bug 的成因：`_read_until` 一比對到就回傳，而 `submit_code`
    接著立刻 SIGKILL 行程，所以還沒傳到的那一半永遠不會到。
    """
    script = (
        "import sys, time;"
        "print('open https://claude.com/x', flush=True);"
        "sys.stdin.readline();"
        f"t={token!r};"
        f"[ (sys.stdout.write(t[i:i+{chunk}]), sys.stdout.flush(), time.sleep({gap}))"
        f"  for i in range(0, len(t), {chunk}) ];"
        "print(flush=True);"
        "time.sleep(30)"
    )
    return ["python3", "-c", script]


def test_a_token_written_in_pieces_is_not_truncated(monkeypatch) -> None:
    """🚨 這一條就是那個 bug 本身。

    存下來的是一個**形狀完全正確**的前綴：開頭對、字元集合法、看不出任何問題 ——
    拿去跑才 401。當時兩個帳號的 token 都剛好是 79 字元，那不是巧合。
    """
    monkeypatch.setattr(authorize, "COMMAND", _dribbling_command(_REAL_TOKEN))

    # 驗證那一關要繞過：這裡測的是讀取，不是 Anthropic。
    async def _ok(_token: str) -> bool:
        return True

    monkeypatch.setattr(authorize, "verify", _ok)

    wid = uuid.uuid4()
    asyncio.run(authorize.start(wid))
    got = asyncio.run(authorize.submit_code(wid, "code-123"))

    assert got == _REAL_TOKEN, f"讀到 {len(got)} 字元，應該是 {len(_REAL_TOKEN)}"


def test_a_token_that_fails_verification_is_not_returned(monkeypatch) -> None:
    """壞掉的 token 不能安靜地存進資料庫。

    沒有這一關，它會待到某個同事的 job 拿它去跑才爆，而那時的症狀是
    「別人的 job 失敗」—— 離成因隔了好幾層。
    """
    monkeypatch.setattr(authorize, "COMMAND", _dribbling_command(_REAL_TOKEN))

    async def _bad(_token: str) -> bool:
        return False

    monkeypatch.setattr(authorize, "verify", _bad)

    wid = uuid.uuid4()
    asyncio.run(authorize.start(wid))
    with pytest.raises(authorize.AuthorizeError) as exc:
        asyncio.run(authorize.submit_code(wid, "code-123"))
    assert "驗證" in str(exc.value)
