#!/usr/bin/env bash
# spike #8：長期 OAuth token 當環境變數，能不能取代掛載 .credentials.json？
#
# 這是那條路的第一關。到目前為止**還沒有任何一個 job 用那個環境變數跑起來過**，
# 所以在驗過之前不要往下實作（板子 85151bb）。
#
# 形狀刻意照 worker/run-job.sh 現在的樣子：同一個 image、同一組旗標、同樣的
# 乾淨 HOME。唯一的差別是憑證從「唯讀掛載檔案」換成「環境變數」。
# 不一樣的東西越多，失敗時越難知道是哪個變因造成的。
#
# 用法：把 token 放進 worker/.env（那個檔案已經被 gitignore），然後跑這支。
#   echo 'CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-…' >> worker/.env
#   bash playground/spike8-oauth-token.sh
#
# token 不會被印出來，也不會寫進工作目錄。

set -euo pipefail
cd "$(dirname "$0")/.."

# shellcheck disable=SC1091
set -a; . worker/.env; set +a

if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
  echo "worker/.env 裡沒有 CLAUDE_CODE_OAUTH_TOKEN，停在這裡。" >&2
  exit 2
fi
echo "token 已載入（長度 ${#CLAUDE_CODE_OAUTH_TOKEN}，前綴 ${CLAUDE_CODE_OAUTH_TOKEN:0:11}…）"

IMAGE="${WORKER_IMAGE:-claude-boba-worker:2.1.278}"
NETWORK="${JOB_NETWORK:-claude-boba-worker_jobnet}"
# job network 是內部網路，出口走白名單 proxy。漏掉這個會得到
# EAI_AGAIN，看起來像認證失敗，其實是 DNS 出不去（2026-09-22 踩過）。
PROXY="${EGRESS_PROXY:-http://egress-proxy:8888}"
WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/spike8.XXXXXX")"
trap 'rm -rf "$WORKDIR"' EXIT
mkdir -p "$WORKDIR/.home/.claude"
chmod 700 "$WORKDIR"

# 問一句只有真的跑起來才答得出來、又便宜的東西。
PROMPT='回答 42，只回答這兩個字，不要呼叫任何工具。'

echo "--- 跑一個 job（乾淨 HOME、沒有掛 .credentials.json）---"
set +e
OUT="$(docker run --rm \
  --network "$NETWORK" \
  -e HTTPS_PROXY="$PROXY" -e HTTP_PROXY="$PROXY" \
  --user "$(id -u):$(id -g)" \
  -e HOME=/job/.home \
  -e CLAUDE_CODE_OAUTH_TOKEN \
  -v "$WORKDIR:/job" \
  -w /job \
  "$IMAGE" \
  claude -p "$PROMPT" \
    --model haiku \
    --output-format json \
    --max-budget-usd 0.50 \
    --settings '{"availableModels": ["haiku"]}' \
    --allowedTools "Read,Edit,Bash" \
    --permission-mode acceptEdits --permission-prompts none \
    < /dev/null 2>"$WORKDIR/stderr.txt")"
rc=$?
set -e

if [ $rc -ne 0 ]; then
  echo "❌ 1. 認證：容器結束碼 $rc" >&2
  # --output-format json 的錯誤寫在 stdout，不是 stderr。
  echo "--- stdout ---" >&2; echo "$OUT" | head -c 900 >&2
  echo >&2; echo "--- stderr ---" >&2; head -5 "$WORKDIR/stderr.txt" >&2
  exit 1
fi

echo "$OUT" | python3 -c '
import json, sys
d = json.load(sys.stdin)
ok = True

def check(n, cond, detail):
    global ok
    ok = ok and cond
    print(("✅" if cond else "❌") + f" {n}：{detail}")

check("1. 乾淨 HOME 下認證過", d.get("is_error") is not True and bool(d.get("result")),
      repr(d.get("result", ""))[:60])
# 這一項最關鍵：人情債整本帳靠它。沒有它這條路不能走。
cost = d.get("total_cost_usd")
check("2. total_cost_usd 還在", isinstance(cost, (int, float)) and cost > 0, f"{cost!r}")
check("3. usage 分項還在", bool(d.get("usage")),
      ",".join(sorted((d.get("usage") or {}).keys()))[:70])
print()
print("跑完了。" if ok else "有項目不成立 —— 停下來回報，不要自己繞。")
sys.exit(0 if ok else 1)
'

echo
echo "--- 4. availableModels 白名單還有效？（要求一個不在白名單裡的 model，應該被擋）---"
set +e
BAD="$(docker run --rm --network "$NETWORK" --user "$(id -u):$(id -g)" \
  -e HTTPS_PROXY="$PROXY" -e HTTP_PROXY="$PROXY" \
  -e HOME=/job/.home -e CLAUDE_CODE_OAUTH_TOKEN \
  -v "$WORKDIR:/job" -w /job "$IMAGE" \
  claude -p 'hi' --model sonnet --output-format json \
    --settings '{"availableModels": ["haiku"]}' \
    --permission-prompts none < /dev/null 2>&1)"
badrc=$?
set -e
if [ $badrc -ne 0 ]; then
  echo "✅ 4. 白名單有效：不在清單裡的 model 被擋（結束碼 $badrc）"
else
  echo "❌ 4. 白名單失效：sonnet 不在 availableModels 裡卻跑起來了"
  echo "$BAD" | head -3
fi

echo
echo "--- 5. max-budget-usd 還有效？（上限設成極小值，應該被擋或立刻中止）---"
set +e
docker run --rm --network "$NETWORK" --user "$(id -u):$(id -g)" \
  -e HTTPS_PROXY="$PROXY" -e HTTP_PROXY="$PROXY" \
  -e HOME=/job/.home -e CLAUDE_CODE_OAUTH_TOKEN \
  -v "$WORKDIR:/job" -w /job "$IMAGE" \
  claude -p '寫一篇 500 字的文章' --model haiku --output-format json \
    --max-budget-usd 0.0001 \
    --settings '{"availableModels": ["haiku"]}' \
    --permission-prompts none < /dev/null >"$WORKDIR/budget.json" 2>&1
budrc=$?
set -e
if [ $budrc -ne 0 ] || grep -qi "budget" "$WORKDIR/budget.json"; then
  echo "✅ 5. 預算上限有效（結束碼 $budrc）"
  grep -io "budget[^\"]*" "$WORKDIR/budget.json" | head -2 || true
else
  echo "❌ 5. 預算上限失效：0.0001 美元的上限沒有擋下 500 字的文章"
fi

echo
echo "工作目錄跑完即刪，token 沒有寫進任何檔案。"
