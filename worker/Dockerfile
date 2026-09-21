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
 && apt-get install -y --no-install-recommends ca-certificates git python3 python3-pip \
 && rm -rf /var/lib/apt/lists/* \
 && npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
 && npm cache clean --force

# 文件類 skill（pptx / xlsx / docx / pdf）的相依套件。
#
# 這些在 Anthropic 自家沙箱是預裝的，skill 也寫著「preinstalled」。我們的容器
# 沒有，而 egress 白名單只放行 api.anthropic.com，所以 job 執行時裝不起來 ——
# 實測現象是 `npm install pptxgenjs` 回 403，然後 Claude 退而求其次產 HTML。
# 預裝比放寬白名單安全：不用為了裝套件而讓借用者的 job 能連 npm。
#
# `--break-system-packages`：Debian bookworm 的 python3 是 PEP 668 externally
# managed。容器裡沒有其他 Python 應用要保護，所以直接裝進系統環境。
RUN pip3 install --no-cache-dir --break-system-packages \
      defusedxml==0.7.1 \
      python-pptx==1.0.2 \
      openpyxl==3.1.5 \
      python-docx==1.2.0 \
      pdfplumber==0.11.10 \
      pypdf==6.19.0 \
      reportlab==5.0.1 \
      pillow==12.3.0 \
      markitdown==0.1.7 \
 && npm install -g \
      pptxgenjs@4.0.1 \
      docx@9.7.1 \
      react@19.3.0 \
      react-dom@19.3.0 \
      react-icons@5.7.0 \
      sharp@0.35.4 \
 && npm cache clean --force

# 沒有裝 LibreOffice。它是 skill 的 `scripts/thumbnail.py` 與 pdf 轉檔所需，
# 但**不是** `scripts/office/validate.py` 所需 —— 而 validate 才是 skill 明列
# 為 required 的 QA 步驟（實測：deck 建得出來、驗證也過，只是沒有截圖預覽）。
#
# 代價是 Debian 上 500MB+，image 會從 1.5GB 漲到 2GB 以上。想要視覺 QA 的
# 出租者可以自行加上這段再 build：
#
#   RUN apt-get update && apt-get install -y --no-install-recommends \
#         libreoffice-impress libreoffice-calc libreoffice-writer fonts-noto-cjk \
#    && rm -rf /var/lib/apt/lists/*

# 讓 job 裡的 `node -e "require('pptxgenjs')"` 找得到全域套件。
ENV NODE_PATH=/usr/local/lib/node_modules

# 非 root 執行 job。
# HOME 是隔離的核心，不是 --bare：容器的 HOME 由 worker 每個 job 建一份新的，
# 裡面只有唯讀掛入的 .credentials.json 與 org 同步的 skills。見 SPEC.md §9、§11。
RUN useradd -m -u 10001 -s /usr/sbin/nologin runner
USER runner
ENV HOME=/home/runner
WORKDIR /job

# 版本自報，供 Hub 寫入 jobs.claude_code_version_lender
RUN claude --version
