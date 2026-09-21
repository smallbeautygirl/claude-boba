# Worker 的執行環境：跑借用者的 job。
#
# 兩個版本都必須釘死，理由見 SPEC.md §11「版本漂移的產生機制」：
# claude-code 2.1.278 要求 node >= 22。若 base image 是 node 20，
# `npm install -g @anthropic-ai/claude-code` 不會報錯，而是靜靜裝一個舊 81 版的
# 2.1.197 —— 每個出租者的 worker 會裝到不同版本，且沒有人會發現。
ARG NODE_VERSION=22.23.2
FROM node:${NODE_VERSION}-bookworm-slim

ARG CLAUDE_CODE_VERSION=2.1.278
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates git \
 && rm -rf /var/lib/apt/lists/* \
 && npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
 && npm cache clean --force

# 非 root 執行 job。
# HOME 是隔離的核心，不是 --bare：容器的 /home/runner/.claude/ 裡只會有
# 執行時唯讀掛入的 .credentials.json，不會有 settings.json / plugins / skills / MCP，
# 所以出租者的個人設定根本不存在、無從載入。見 SPEC.md §9、§11。
RUN useradd -m -u 10001 -s /usr/sbin/nologin runner
USER runner
ENV HOME=/home/runner
WORKDIR /job

# 版本自報，供 Hub 寫入 jobs.claude_code_version_lender
RUN claude --version
