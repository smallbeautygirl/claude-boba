#!/usr/bin/env bash
# 啟動單一 job 容器，把 stream-json 事件逐行寫到 stdout。
#
# 這個形狀的每一項都有實測依據，見 SPEC.md §9 與 §11。改動前先讀那兩節。
# 特別是：不要加 --bare（它不讀 OAuth，會回 Not logged in），
# 以及 HOME 必須是每個 job 全新的 tmpfs（共用會造成跨 job 的任意程式碼執行）。
set -euo pipefail

JOB_ID="${1:?job id}"
PROMPT="${2:?prompt}"
MODEL="${3:-sonnet}"
TRANSCRIPT="${4:-}"            # 選填：借用者上傳的 .jsonl，相對於 workdir

WORKDIR="${JOB_WORKDIR:?job 工作目錄}"
# 憑證有兩種通道，不可互換（.claude/rules/security.md 紅線 2）：
#
#   CLAUDE_CODE_OAUTH_TOKEN  託管模型。一年期 token，只以環境變數注入。
#   CLAUDE_CREDENTIALS       舊模型。代跑者機器上的 .credentials.json，唯讀掛入。
#
# 有 token 就走 token（它是本機沒有憑證檔時唯一可行的通道），
# 沒有才退回掛載。兩條都要能跑 —— 舊模型還在線上。
if [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
  CRED_ARGS=(-e CLAUDE_CODE_OAUTH_TOKEN)
else
  CREDS="${CLAUDE_CREDENTIALS:?沒有 CLAUDE_CODE_OAUTH_TOKEN，就要有憑證檔絕對路徑}"
  CRED_ARGS=(-v "$CREDS:/job/.home/.claude/.credentials.json:ro")
fi
TIMEOUT="${TIMEOUT_SECONDS:-600}"
BUDGET="${JOB_BUDGET_USD:-5}"
MODELS="${AVAILABLE_MODELS:-sonnet,haiku}"
IMAGE="${WORKER_IMAGE:-claude-boba-worker:2.1.278}"
NETWORK="${JOB_NETWORK:-claude-boba-worker_jobnet}"
PROXY="${EGRESS_PROXY:-http://egress-proxy:8888}"

resume=()
[[ -n "$TRANSCRIPT" ]] && resume=(--resume "/job/$TRANSCRIPT")

mkdir -p "$WORKDIR/.home/.claude"

# availableModels 吃 JSON 陣列
models_json=$(printf '%s' "$MODELS" | python3 -c \
  'import sys,json;print(json.dumps([m for m in sys.stdin.read().split(",") if m]))')

# HOME 是工作目錄底下的 .home，不是 tmpfs。
#
# 隔離性完全相同 —— 每個 job 一個全新目錄，worker 跑完就刪，出租者的個人
# settings / plugins / skills 一樣不存在。差別只在 transcript 活得過容器，
# 這是「接著問」的前提（SPEC.md §4.2）。
exec timeout --signal=TERM --kill-after=20 "$TIMEOUT" \
  docker run --rm \
    --name "boba-job-$JOB_ID" \
    --network "$NETWORK" \
    -e HTTPS_PROXY="$PROXY" -e HTTP_PROXY="$PROXY" \
    --user "$(id -u):$(id -g)" \
    -e HOME=/job/.home \
    "${CRED_ARGS[@]}" \
    -v "$WORKDIR:/job" \
    -w /job \
    "$IMAGE" \
    claude -p "$PROMPT" "${resume[@]}" \
      --model "$MODEL" \
      --output-format stream-json --verbose \
      --max-budget-usd "$BUDGET" \
      --settings "{\"availableModels\": $models_json}" \
      --allowedTools "Read,Edit,Bash" \
      --permission-mode acceptEdits --permission-prompts none \
      < /dev/null
