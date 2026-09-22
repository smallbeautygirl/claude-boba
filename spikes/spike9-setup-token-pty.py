#!/usr/bin/env python3
"""spike #9：後端能不能自動驅動 `claude setup-token`？

託管模型的第一關（板子 4710787）。使用者選了「頁面驅動授權」而不是「自己貼
token」，理由是今天的實際教訓：只要那串東西需要人類複製貼上，它就會出現在
剪貼簿、瀏覽器歷史、對話紀錄、截圖裡 —— 今天真的發生過一次。

所以要確認的是：**Hub 能不能自己跑起那個指令、把授權網址撈出來給網頁顯示、
然後等它完成。**

這支只驗到「撈得到網址」為止，**刻意不完成授權** —— 完成就會真的產生一組
一年期憑證，而 spike 不該留下那種東西。剩下的（完成後 token 印在哪／存在哪）
在回報裡列為未驗。

兩個刻意的設計：

- **scratch HOME**。不碰使用者真正的 ~/.claude —— 那裡有他現在在用的
  .credentials.json，而這支要跑的指令本來就會寫憑證。
- **pty**。直接用 subprocess 管道跑會沒有輸出然後 timeout（板子上寫的，
  也是這類互動指令的常見行為：它偵測到不是終端機就不印互動提示）。
"""

from __future__ import annotations

import os
import pty
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time

TIMEOUT_SECONDS = 45
# 授權網址長什麼樣不預設，抓所有 https:// 開頭的東西再判斷。
URL_RE = re.compile(rb"https://[^\s\x1b\"'<>]+")


def main() -> int:
    exe = shutil.which("claude")
    if not exe:
        print("找不到 claude，停在這裡。", file=sys.stderr)
        return 2

    home = tempfile.mkdtemp(prefix="spike9-home-")
    env = os.environ | {"HOME": home, "TERM": "xterm-256color"}
    # 這支不該用到既有憑證，也不該被它影響。
    for k in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "CLAUDE_CREDENTIALS"):
        env.pop(k, None)

    print(f"HOME = {home}（scratch，跑完刪掉）")
    print("--- 用 pty 跑 claude setup-token ---")

    master, slave = pty.openpty()
    proc = subprocess.Popen(
        [exe, "setup-token"],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
        cwd=home,
        start_new_session=True,
    )
    os.close(slave)

    buf = b""
    url: bytes | None = None
    deadline = time.monotonic() + TIMEOUT_SECONDS
    try:
        while time.monotonic() < deadline:
            r, _, _ = select.select([master], [], [], 0.5)
            if r:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                found = URL_RE.findall(buf)
                if found and url is None:
                    url = found[0]
                    # 抓到網址之後**再多讀幾秒**：要看它接著問什麼。
                    # 那決定了「頁面驅動授權」實際要做成什麼樣子 ——
                    # 如果它在等一個貼回來的授權碼，網頁就得有那個欄位。
                    deadline = min(deadline, time.monotonic() + 8)
            if proc.poll() is not None:
                break
    finally:
        # 殺掉整個 process group：**不要完成授權**。
        with open(os.devnull):
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        os.close(master)

    text = buf.decode("utf-8", "replace")
    # 去掉 ANSI 控制碼，只留看得懂的部分
    plain = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)
    plain = re.sub(r"\n{3,}", "\n\n", plain).strip()

    print("--- 它印了什麼（前 1500 字）---")
    print(plain[:1500] or "（什麼都沒印）")
    print("--- 結束 ---")
    print()

    ok = url is not None
    print(("✅" if ok else "❌") + " 1. pty 下有輸出：", f"{len(buf)} bytes")
    print(
        ("✅" if ok else "❌") + " 2. 解析得到授權網址：",
        url.decode() if url else "沒有",
    )
    print("⏸ 3. 完成授權後 token 在哪：**未驗**（刻意不完成，見檔頭）")

    shutil.rmtree(home, ignore_errors=True)
    print("\nscratch HOME 已刪除。沒有產生任何憑證。")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
