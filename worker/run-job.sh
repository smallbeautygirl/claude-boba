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
CREDS="${CLAUDE_CREDENTIALS:?出租者憑證檔絕對路徑}"
TIMEOUT="${TIMEOUT_SECONDS:-600}"
BUDGET="${JOB_BUDGET_USD:-5}"
MODELS="${AVAILABLE_MODELS:-sonnet,haiku}"
IMAGE="${WORKER_IMAGE:-claude-boba-worker:2.1.278}"
NETWORK="${JOB_NETWORK:-claude-boba-worker_jobnet}"
PROXY="${EGRESS_PROXY:-http://egress-proxy:8888}"

resume=()
[[ -n "$TRANSCRIPT" ]] && resume=(--resume "/job/$TRANSCRIPT")

# availableModels 吃 JSON 陣列
models_json=$(printf '%s' "$MODELS" | python3 -c \
  'import sys,json;print(json.dumps([m for m in sys.stdin.read().split(",") if m]))')

exec timeout --signal=TERM --kill-after=20 "$TIMEOUT" \
  docker run --rm \
    --name "boba-job-$JOB_ID" \
    --network "$NETWORK" \
    -e HTTPS_PROXY="$PROXY" -e HTTP_PROXY="$PROXY" \
    --user "$(id -u):$(id -g)" \
    -e HOME=/home/runner \
    --tmpfs "/home/runner:rw,size=256m,uid=$(id -u),gid=$(id -g)" \
    -v "$CREDS:/home/runner/.claude/.credentials.json:ro" \
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
      --no-session-persistence \
      < /dev/null
