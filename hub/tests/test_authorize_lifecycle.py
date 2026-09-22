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


def test_pressing_authorize_again_reuses_the_live_session() -> None:
    """🚨 **再按一次不可以換掉還活著的 session。**

    每起一次就在 Claude 那邊產生一組新的 code_challenge（PKCE），舊的當場作廢 ——
    連同使用者可能已經開著、甚至已經按完同意的那一頁。他回來貼碼時，碼對應的是
    被殺掉的那個 session，而 CLI 只會說 "Invalid code"，看起來像他複製錯了。

    2026-09-22 真的發生過，從 log 才看得出來：貼碼之前 POST /authorize 被打了
    兩次。最容易觸發的是重新整理頁面 —— 畫面回到「開始出借」，再按一次就死了。

    這條原本釘的是相反的行為（「再按一次就換一個新的」），而那正是 bug 本身。
    重用同樣不會累積行程：根本不 spawn 第二個。
    """
    wid = uuid.uuid4()
    first_url = asyncio.run(authorize.start(wid))
    first = authorize._sessions[wid].proc.pid

    second_url = asyncio.run(authorize.start(wid))
    second = authorize._sessions[wid].proc.pid

    assert first == second, "又 spawn 了一個，使用者手上的碼會作廢"
    assert first_url == second_url
    assert len(authorize._sessions) == 1
    assert _alive(first)


def test_a_dead_session_is_replaced_not_reused() -> None:
    """重用只限還活著的。行程已經死掉的話，回一個永遠不會成功的網址更糟。"""
    wid = uuid.uuid4()
    asyncio.run(authorize.start(wid))
    first = authorize._sessions[wid].proc.pid
    authorize._sessions[wid].close()
    time.sleep(0.2)

    asyncio.run(authorize.start(wid))
    assert authorize._sessions[wid].proc.pid != first


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


def test_the_pty_is_wide_enough_that_a_token_cannot_wrap(monkeypatch) -> None:
    """🚨 80 欄是 pty 的預設值，而一年期 token 比它長。

    不設視窗大小的話，CLI 會把 token 折成兩行 —— 中間多一個換行字元，
    `_TOKEN_RE` 就永遠比對不到完整的那串，而症狀是「等 60 秒然後說授權沒完成」。
    這不是保險起見，是不設就穩定地壞掉。
    """
    monkeypatch.setattr(
        authorize,
        "COMMAND",
        [
            "python3",
            "-c",
            (
                "import os, sys, time;"
                "print('https://claude.com/x cols=%d' % os.get_terminal_size().columns,"
                " flush=True);"
                "time.sleep(30)"
            ),
        ],
    )
    wid = uuid.uuid4()
    asyncio.run(authorize.start(wid))
    sess = authorize._sessions[wid]
    cols = int(sess.buf.split(b"cols=")[1].split()[0])
    assert cols >= 200, f"pty 只有 {cols} 欄，token 會被折行"


def test_a_rejected_code_keeps_the_session_alive_and_quotes_the_cli(
    monkeypatch,
) -> None:
    """貼錯一次不該讓人重跑整個流程。

    CLI 自己說 "Press Enter to retry" —— 它還活著、還在同一個 PKCE 上等下一次
    輸入。收掉 session 的話使用者得從「開始出借」重來，而重來會換一組
    code_challenge，連他手上那個已經開著的授權頁也一起作廢。

    錯誤訊息也要用 CLI 的原話：「full code was copied」指向一個很具體的動作，
    而我們原本那句「可能是碼貼錯或過期了」把兩件不同的事混在一起。
    """
    monkeypatch.setattr(
        authorize,
        "COMMAND",
        [
            "python3",
            "-c",
            (
                "import sys, time;"
                "print('https://claude.com/x', flush=True);"
                "sys.stdin.readline();"
                "print('OAuth error: Invalid code. "
                "Please make sure the full code was copied', flush=True);"
                "time.sleep(30)"
            ),
        ],
    )
    wid = uuid.uuid4()
    asyncio.run(authorize.start(wid))
    pid = authorize._sessions[wid].proc.pid

    with pytest.raises(authorize.AuthorizeError) as exc:
        asyncio.run(authorize.submit_code(wid, "wrong-code"))

    assert "full code was copied" in str(exc.value)
    assert wid in authorize._sessions, "session 被收掉了，使用者得從頭再來"
    assert _alive(pid)


def test_the_cli_is_not_allowed_to_open_a_browser_on_the_host(monkeypatch) -> None:
    """🚨 那個授權連結是**別人帳號的**。

    `claude setup-token` 啟動時會自己 xdg-open 授權網址，而這個行程跑在站台
    主機上 —— 所以彈窗會跳在主機管理者面前，不是代跑者面前。他順手點下去並
    登入的話，會把自己的 Claude 帳號授權進別人的那一格。

    這裡驗的是 spawn 的環境，不是「有沒有真的開起來」—— 後者取決於那台機器
    裝了什麼，測不出穩定的結果。
    """
    captured: dict = {}

    class _Fake:
        pid = 1

        def poll(self):
            return None

    def fake_popen(cmd, **kwargs):
        captured.update(kwargs.get("env") or {})
        return _Fake()

    monkeypatch.setattr(authorize.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(authorize, "COMMAND", ["true"])
    authorize._spawn(uuid.uuid4())

    assert captured.get("BROWSER") == "/bin/true"
    assert "DISPLAY" not in captured
    assert "WAYLAND_DISPLAY" not in captured
    # 憑證也不該漏進去（原本就有的約束，順手一起釘）。
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in captured
    assert "ANTHROPIC_API_KEY" not in captured


def test_a_rejected_code_fails_immediately(monkeypatch) -> None:
    """碼被拒絕時不該讓人盯著「確認中…」一分鐘。

    `_read_until` 原本只盯 token，所以 CLI 早就印了拒絕訊息，我們還站在那裡
    等滿 TOKEN_TIMEOUT_SECONDS。使用者看到的是一分鐘的靜止，然後才拿到一句
    其實早就在畫面上的話。
    """
    monkeypatch.setattr(
        authorize,
        "COMMAND",
        [
            "python3",
            "-c",
            (
                "import sys, time;"
                "print('https://claude.com/x', flush=True);"
                "sys.stdin.readline();"
                "print('OAuth error: Invalid code.', flush=True);"
                "time.sleep(30)"
            ),
        ],
    )
    wid = uuid.uuid4()
    asyncio.run(authorize.start(wid))

    started = time.monotonic()
    with pytest.raises(authorize.AuthorizeError):
        asyncio.run(authorize.submit_code(wid, "wrong"))
    elapsed = time.monotonic() - started

    assert elapsed < 10, (
        f"花了 {elapsed:.0f} 秒才說失敗，逾時是 {authorize.TOKEN_TIMEOUT_SECONDS} 秒"
    )


def test_the_code_is_written_in_small_pieces(monkeypatch) -> None:
    """🚨 一次把整串碼寫進 pty，CLI 會**完全沒有反應**。

    2026-09-22 實測：87 字元一次寫入 → 等 75 秒也沒有任何輸出（遮罩星號都出現了，
    代表字元有進去，但它從來沒有把碼送出去）；21 字元一次寫入 → 立刻回應。
    真實的授權碼是 90 幾個字元，所以這條路一次都沒有成功過。

    這條測試擋的是「順手簡化回一次寫完」—— 那個寫法看起來完全合理，
    而且用短字串測起來是好的。
    """
    writes: list[bytes] = []
    monkeypatch.setattr(authorize.os, "write", lambda _fd, b: writes.append(b))
    monkeypatch.setattr(authorize.time, "sleep", lambda _s: None)

    code = "x" * 95
    authorize._write_code(7, code)

    assert b"".join(writes) == code.encode() + b"\r"
    assert len(writes) > 2, "又變成一次寫完了"
    assert all(len(w) <= authorize._CODE_CHUNK_BYTES for w in writes)
