#!/usr/bin/env bash
# 暖一個 template HOME：讓 Claude Code 把 org 層級的 skill（pptx / xlsx / docx /
# pdf …）同步下來，之後每個 job 從這裡複製。
#
# 不做的話，BD/PM 最主力的「做簡報」在每個 job 都會回「這個指令沒安裝」——
# 同步是背景預抓，而每個 job 都是全新 HOME，永遠是「第一次執行」。
set -euo pipefail

TEMPLATE="${1:?template 目錄絕對路徑}"   # 內部結構與 job 工作目錄相同：<template>/.home
CREDS="${CLAUDE_CREDENTIALS:?}"
IMAGE="${WORKER_IMAGE:-claude-boba-worker:2.1.278}"
WAIT="${WARM_WAIT_SECONDS:-60}"

mkdir -p "$TEMPLATE/.home/.claude"

# 容器的形狀必須與真實 job 相同 —— 實測：工作目錄若不是掛載進來的專案目錄
# （例如用 -w /tmp），同步根本不會觸發。
#
# 等待也必須在容器內部：同步是背景寫入，容器一結束就中斷，在外面等只會
# 等到一個已經放棄的目錄。
docker run --rm \
  --user "$(id -u):$(id -g)" \
  -e HOME=/job/.home \
  -v "$CREDS:/job/.home/.claude/.credentials.json:ro" \
  -v "$TEMPLATE:/job" \
  -w /job \
  --entrypoint sh \
  "$IMAGE" -c "
    claude -p hi --model haiku --output-format json >/dev/null 2>&1 || true
    for _ in \$(seq 1 $WAIT); do
      if [ -d /job/.home/.claude/skills/synced ]; then sleep 3; exit 0; fi
      sleep 1
    done
    exit 0
  "
