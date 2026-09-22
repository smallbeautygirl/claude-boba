"""代跑者的授權流程：用 pty 驅動 `claude setup-token`。

為什麼要這麼麻煩，而不是請代跑者自己貼 token：只要那串東西需要人類複製貼上，
它就會出現在剪貼簿、瀏覽器歷史、對話紀錄、截圖裡 —— 2026-09-22 真的發生過
一次。走這條路的話，**一年期 token 從頭到尾不經過人的手**。

人還是要貼一個東西（SPEC §11 spike #9：授權網址的 redirect_uri 是
platform.claude.com，不是 localhost，所以授權完不會自動回來）。但貼的變成
**一次性授權碼** —— 用過即失效，跟一年期 token 不是同一個量級的東西。

流程：

    POST /authorize        → 起一個 pty session，回授權網址
    （人去 claude.com 授權，拿到一個碼）
    POST /authorize/code   → 把碼寫進 pty，等 token，加密存起來，收掉 session

## 這個模組最需要小心的是生命週期，不是 pty

一個子行程要活好幾分鐘等人回來。做不好的話它會漏、會卡、會在每次部署之後
留下一堆要手動收屍的殭屍。三條對策：

1. **硬性逾時。** 每個 session 有 deadline，背景清潔工定期掃掉過期的。
   沒有這條的話，任何一個人開了授權頁又關掉，就留下一個永遠不會結束的行程。
2. **父行程一死，子行程跟著死**（`PR_SET_PDEATHSIG`）。這是 Hub 重啟那題的
   答案：重啟後記憶體裡的 session 表沒了，我們**認不出**那些子行程，所以不能
   靠「重啟時去收屍」—— 要讓核心替我們收。
3. **一人一個。** 同一個代跑者再按一次授權，先殺掉前一個。不然重複點按鈕
   就會累積。
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import pty
import re
import select
import shutil
import signal
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field

# 授權網址長怎樣不預設，抓 https:// 開頭的第一個。
_URL_RE = re.compile(rb"https://[^\s\x1b\"'<>]+")
# token 的格式（`claude setup-token` 產生的長期 OAuth token）。
_TOKEN_RE = re.compile(rb"sk-ant-oat[0-9]{2}-[A-Za-z0-9_-]{20,}")

# 人要開瀏覽器、登入、授權、複製碼、切回來貼上。五分鐘不算寬鬆。
SESSION_TTL_SECONDS = 300
# 等授權網址出現。它只是本機啟動 + 印字，不該要這麼久。
URL_TIMEOUT_SECONDS = 45
# 貼完碼之後等 token。這一段要連 Anthropic，給寬一點。
TOKEN_TIMEOUT_SECONDS = 60

_PR_SET_PDEATHSIG = 1
# **在 import 時就載入，不要在 preexec_fn 裡載。**
#
# preexec_fn 跑在 fork 之後、exec 之前的子行程裡，而 fork 只複製呼叫的那條
# thread —— 其他 thread 當時持有的鎖會在子行程裡永遠鎖著。`ctypes.CDLL()` 會
# dlopen，而 dlopen 要拿鎖。我們是從 asyncio.to_thread 裡 spawn 的，所以那是
# 真的會死鎖，不是理論風險。提前載入之後，子行程裡只剩 setsid 與 prctl
# 兩個 syscall，兩個都不碰鎖。
_LIBC = ctypes.CDLL("libc.so.6", use_errno=True)


def _die_with_parent() -> None:
    """子行程在 Hub 死掉時跟著死。

    這是「Hub 重啟怎麼辦」的答案。重啟後記憶體裡的 session 表是空的，所以
    我們認不出那些子行程 —— 不能靠重啟時去收屍，要讓核心替我們收。

    也 setsid：這樣可以用 killpg 一次殺掉 `claude` 連同它開的東西，而不會
    誤殺到 Hub 自己那一群。

    這個函式裡**只能有 syscall**，理由見 _LIBC 上面那段。
    """
    os.setsid()
    _LIBC.prctl(_PR_SET_PDEATHSIG, signal.SIGKILL)


@dataclass
class Session:
    worker_id: uuid.UUID
    proc: subprocess.Popen
    master_fd: int
    home: str
    authorize_url: str = ""
    buf: bytes = b""
    expires_at: float = field(
        default_factory=lambda: time.monotonic() + SESSION_TTL_SECONDS
    )

    def close(self) -> None:
        try:
            os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        # 殺完要收屍。少了這個 wait()，被殺掉的行程會停在 zombie 直到下一次
        # spawn 才被順手回收 —— 一台長時間不重啟的 Hub 會慢慢累積。
        # （測試就是這樣抓到的：kill(pid, 0) 對 zombie 仍然成功。）
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.close(self.master_fd)
        except OSError:
            pass
        # HOME 是臨時的，但 `claude setup-token` 會把憑證寫進去 ——
        # 一定要刪掉（security.md 紅線 2：絕不寫進檔案系統）。
        shutil.rmtree(self.home, ignore_errors=True)


class AuthorizeError(RuntimeError):
    pass


_sessions: dict[uuid.UUID, Session] = {}


def _read_until(
    sess: Session, pattern: re.Pattern[bytes], timeout: float
) -> bytes | None:
    """一直讀 pty 直到吐出符合的東西，或逾時。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r, _, _ = select.select([sess.master_fd], [], [], 0.5)
        if r:
            try:
                chunk = os.read(sess.master_fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            sess.buf += chunk
            found = pattern.search(sess.buf)
            if found:
                return found.group(0)
        if sess.proc.poll() is not None:
            break
    return None


# 換掉這個就能在測試裡驗生命週期，不用真的去要一組憑證。
COMMAND: list[str] = ["claude", "setup-token"]


def _spawn_and_read_url(worker_id: uuid.UUID) -> Session:
    """整段放進一個 thread：fork/exec 與 select 都是阻塞的。"""
    home = tempfile.mkdtemp(prefix="boba-auth-")
    master, slave = pty.openpty()
    env = os.environ | {"HOME": home, "TERM": "xterm-256color"}
    for k in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"):
        env.pop(k, None)

    proc = subprocess.Popen(
        COMMAND,
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
        cwd=home,
        # 安全性見 _LIBC 那段：子行程裡只有兩個 syscall，沒有 dlopen。
        preexec_fn=_die_with_parent,  # noqa: PLW1509
    )
    os.close(slave)
    sess = Session(worker_id=worker_id, proc=proc, master_fd=master, home=home)

    url = _read_until(sess, _URL_RE, URL_TIMEOUT_SECONDS)
    if not url:
        sess.close()
        raise AuthorizeError("拿不到授權網址，請再試一次")
    sess.authorize_url = url.decode()
    return sess


async def start(worker_id: uuid.UUID) -> str:
    """起一個授權 session，回授權網址。"""
    # 同一個人再按一次就換一個新的 —— 不然重複點按鈕會累積行程。
    if old := _sessions.pop(worker_id, None):
        old.close()

    sess = await asyncio.to_thread(_spawn_and_read_url, worker_id)
    _sessions[worker_id] = sess
    return sess.authorize_url


async def submit_code(worker_id: uuid.UUID, code: str) -> str:
    """把授權碼寫進 pty，回拿到的 token。

    **回傳值是明文 token。** 呼叫端必須立刻加密存起來，而且絕不放進回應、
    log 或例外訊息裡（security.md 紅線 2）。
    """
    sess = _sessions.get(worker_id)
    if sess is None:
        raise AuthorizeError("這個授權已經過期了，請重新開始")

    sess.buf = b""  # 只看貼碼之後的輸出，不要撈到前面那段
    os.write(sess.master_fd, code.strip().encode() + b"\r")

    token = await asyncio.to_thread(_read_until, sess, _TOKEN_RE, TOKEN_TIMEOUT_SECONDS)
    _sessions.pop(worker_id, None)
    sess.close()

    if not token:
        # 不要把 pty 的輸出放進錯誤訊息 —— 那裡面可能有 token 的片段。
        raise AuthorizeError("授權沒有完成。可能是碼貼錯或過期了，請重新開始")
    return token.decode()


def cancel(worker_id: uuid.UUID) -> None:
    if sess := _sessions.pop(worker_id, None):
        sess.close()


def sweep() -> int:
    """收掉過期的 session。回收掉幾個。"""
    now = time.monotonic()
    dead = [wid for wid, s in _sessions.items() if s.expires_at <= now]
    for wid in dead:
        _sessions.pop(wid).close()
    return len(dead)


async def sweeper(interval: float = 60.0) -> None:
    """背景清潔工。沒有它，開了授權頁又關掉的人會留下一個永不結束的行程。"""
    while True:
        await asyncio.sleep(interval)
        sweep()
