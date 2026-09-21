#!/usr/bin/env bash
# 啟動單一 job 容器。每次呼叫都是全新的 HOME 與工作目錄，跑完即銷毀。
#
# 這個形狀的每一項都有實測依據，見 SPEC.md §11。改動前先讀那節。
set -euo pipefail

JOB_ID="${1:?job id}"
PROMPT="${2:?prompt}"
MODEL="${3:-sonnet}"
TRANSCRIPT="${4:-}"            # 選填：借用者上傳的 .jsonl（已放進 workdir）
WORKDIR="${JOB_WORKDIR:?job 工作目錄}"
CREDS="${CLAUDE_CREDENTIALS:?出租者憑證檔絕對路徑}"
TIMEOUT="${TIMEOUT_SECONDS:-600}"
IMAGE="${WORKER_IMAGE:-claude-boba-worker:2.1.278}"

resume=()
[[ -n "$TRANSCRIPT" ]] && resume=(--resume "/job/$TRANSCRIPT")

exec timeout "$TIMEOUT" docker run --rm \
  --name "boba-job-$JOB_ID" \
  --network claude-boba-worker_jobnet \
  -e HTTPS_PROXY=http://egress-proxy:8888 \
  -e HTTP_PROXY=http://egress-proxy:8888 \
  --user "$(id -u):$(id -g)" \
  -e HOME=/home/runner \
  --tmpfs "/home/runner:rw,size=256m,uid=$(id -u),gid=$(id -g)" \
  -v "$CREDS:/home/runner/.claude/.credentials.json:ro" \
  -v "$WORKDIR:/job" \
  -w /job \
  "$IMAGE" \
  claude -p "$PROMPT" "${resume[@]}" \
    --model "$MODEL" --output-format json \
    --allowedTools "Read,Edit,Bash" \
    --permission-mode acceptEdits --permission-prompts none \
    --no-session-persistence \
    < /dev/null
