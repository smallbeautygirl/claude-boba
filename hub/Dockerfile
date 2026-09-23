# Hub 的正式環境映像。開發時 hub 用 uvicorn 直接跑在 host 上（README.md），
# production 進容器 —— 不該依賴某台 host 上的 venv 剛好裝對版本。
#
# ⚠️ `.env` 絕不進 image（security.md「憑證只存在 .env」）。靠 .dockerignore 擋，
# 執行時用 compose 的 env_file 注入。改 .dockerignore 之前先讀它。

FROM python:3.12-slim

# 不用 root 跑。這個行程手上有所有代跑者的憑證解密金鑰。
RUN useradd --create-home --uid 10001 hub
WORKDIR /app

# 先裝相依再複製程式，改程式碼時不用重裝套件。版本全部釘死（requirements.txt）。
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=hub:hub app ./app
COPY --chown=hub:hub alembic ./alembic
COPY --chown=hub:hub alembic.ini .

USER hub
EXPOSE 8787

# 沒有 --reload：那是開發用的。migration 不在這裡自動跑 —— hub 啟動時會檢查版本，
# 落後就拒絕啟動並講清楚要跑什麼（app/main.py）。deploy.sh 負責先 upgrade。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8787"]
