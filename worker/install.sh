#!/usr/bin/env bash
# 出租端的安裝腳本。做完六步裡機械性的那五步，剩下的 token 要你自己貼 ——
# 那要登入網頁才拿得到，腳本代勞不了。
#
# 完整說明（包含「你實際上答應了什麼」）在 README.md。出事時看那份，不是這支。
set -euo pipefail

cd "$(dirname "$0")"
say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
die() { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

IMAGE_TAG="claude-boba-worker:2.1.278"
# 出租者通常不是 hub 那台，所以預設問一次，不要沿用 .env.example 的 127.0.0.1。
DEFAULT_HUB="${HUB_URL:-http://127.0.0.1:8787}"

# --- 0. 先擋掉跑到一半才發現的失敗 ---------------------------------------
say "檢查環境"
command -v docker >/dev/null || die "找不到 docker"
docker info >/dev/null 2>&1 || die "docker 裝了但沒跑起來（或你不在 docker 群組）"
command -v python3 >/dev/null || die "找不到 python3"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)' \
  || die "需要 Python 3.12 以上，目前是 $(python3 -V)"
echo "  docker、python3 都在"

# 憑證：這是整件事的前提，沒有它後面五步都白做。
CRED="${CLAUDE_CREDENTIALS:-$HOME/.claude/.credentials.json}"
[[ -f "$CRED" ]] || die "找不到 $CRED
  這個檔案是你要出借的東西。先在終端機跑一次 claude 登入，再跑這支腳本。"
echo "  憑證：$CRED"

# --- 1. egress proxy ------------------------------------------------------
say "起 egress proxy"
docker compose up -d
echo "  job 容器只連得到 api.anthropic.com，白名單在這裡生效"

# --- 2. job 容器的 image --------------------------------------------------
if docker image inspect "$IMAGE_TAG" >/dev/null 2>&1; then
  say "image $IMAGE_TAG 已存在，跳過 build"
else
  say "建 image（這步最久，去泡杯茶）"
  docker build -t "$IMAGE_TAG" .
fi

# --- 3. venv --------------------------------------------------------------
say "裝 Python 相依"
[[ -d .venv ]] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
echo "  好了"

# --- 4. .env --------------------------------------------------------------
# 已經有 .env 就不覆蓋 —— 裡面可能有你調過的上限與已經貼好的 token。
if [[ -f .env ]]; then
  say ".env 已存在，不動它"
else
  say "產生 .env"
  cp .env.example .env
  # 只填憑證路徑。token 要登入網頁才拿得到，留白給你貼。
  python3 - "$CRED" <<'PY'
import pathlib, sys
p = pathlib.Path(".env")
p.write_text(p.read_text(encoding="utf-8").replace(
    "CLAUDE_CREDENTIALS=/home/you/.claude/.credentials.json",
    f"CLAUDE_CREDENTIALS={sys.argv[1]}"), encoding="utf-8")
PY
  echo "  憑證路徑已填好"

  # HUB_URL 的預設值只對「hub 跑在自己這台」的人正確。出租者多半是別台機器，
  # 而填錯的症狀是「網頁一直顯示離線」、沒有任何錯誤訊息 —— 所以直接問，
  # 不要讓人事後去猜。
  printf '\nhub 在哪裡？（直接按 Enter 用 %s）\nHUB_URL: ' "$DEFAULT_HUB"
  read -r HUB_INPUT </dev/tty || HUB_INPUT=""
  HUB_URL="${HUB_INPUT:-$DEFAULT_HUB}"
  python3 - "$HUB_URL" <<'ENVPY'
import pathlib, re, sys
p = pathlib.Path(".env")
p.write_text(re.sub(r"^HUB_URL=.*$", f"HUB_URL={sys.argv[1]}",
                    p.read_text(encoding="utf-8"), count=1, flags=re.M),
             encoding="utf-8")
ENVPY
  echo "  HUB_URL=$HUB_URL"
fi

# --- 5. 剩下的只有人能做 ---------------------------------------------------
if grep -q '^WORKER_TOKEN=$' .env 2>/dev/null; then
  cat <<'MSG'

差最後一步，這步腳本做不了：

  1. 打開 claude-boba 網頁 →「我的 worker」→ 填個名字 →「產生 worker token」
  2. 那串 token 只會顯示一次，複製它
  3. 貼進 worker/.env 的 WORKER_TOKEN=

然後：

  .venv/bin/python worker.py

MSG
else
  cat <<'MSG'

都好了。跑起來：

  .venv/bin/python worker.py

網頁的「我的 worker」應該在幾秒內變成線上。

MSG
fi
