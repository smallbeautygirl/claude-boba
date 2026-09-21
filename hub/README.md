# Hub

claude-boba 的中央服務。API 契約見 [../SPEC.md](../SPEC.md) §9「Hub ↔ Worker 協定」。

## 跑起來

```bash
cd hub
docker compose up -d                       # postgres + minio
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/alembic upgrade head             # 建/更新 schema
.venv/bin/uvicorn app.main:app --reload --port 8787
./smoke.sh                                 # 端到端打過一遍協定
```

**改了 models 之後：**

```bash
.venv/bin/alembic revision --autogenerate -m "說明這次改了什麼"
.venv/bin/alembic upgrade head
```

Hub 啟動時會檢查資料庫版本，落後就**拒絕啟動**並告訴你要跑什麼。刻意不自動
套用 migration —— 自動 upgrade 會在多個 instance 同時啟動時互相競爭，
而且會讓一個有問題的 migration 悄悄上線。失敗要響亮，不要猜。

Postgres 對外用 **55432**，不是 5432 —— 開發機上 5432 常被既有服務佔用。

MinIO 從 **quay.io** 拉，不是 Docker Hub：`minio/minio` 在 Docker Hub 上已不存在
（API 回 `object not found`，2026-09-21 實測）。

## Phase 1 的已知缺口

- **沒有認證。** 借用者用 `borrower_label` 字串識別。Phase 2 換成 Observ
  （SPEC §4.10，`require_auth` 的實作可從 `lighthouse-saas-api` 抄）。
- **事件扇出在記憶體裡**（`app/events.py`）。單一 Hub 程序可行；要跑多 instance
  時換成 Postgres LISTEN/NOTIFY，呼叫端不用動。
- **檔案上傳尚未接 MinIO。** compose 已經把 MinIO 起起來，但 Phase 1 只走貼上路徑。

## 兩個踩過的坑

**SQLAlchemy 的 enum 欄位**：`mapped_column(String(16))` 配 `Mapped[JobStatus]`
是行不通的，SQLAlchemy 不轉型，讀回來是純 `str`，而 `str` 沒有 `JobStatus.creates_debt`。
要用 `SAEnum(..., values_callable=...)`，見 `app/models.py` 的 `_enum()`。

**`NULL IN (...)` 永遠不為真**：自動派單的 job 其 `requested_worker_id` 是 NULL，
用 `in_([None, worker.id])` 篩選會一筆都領不到 —— 而且不報錯，只是靜靜地什麼都不派。
要用 `or_(col.is_(None), col == worker.id)`。

**gitleaks 掃的是 staged 內容**：修掉誤判後沒重新 `git add` 的話，它會一直回報同一筆。
熵值完全相同就是線索 —— 代表它看的還是舊版本。

## 測試

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

目前只測金額與級距（`tests/test_pricing.py`）。從這裡開始不是巧合 ——
算錯錢是這個專案裡最不能無聲發生的事，帳單是要給人看的。

> 📌 `.claude/rules/` 還沒有 `testing.md`。CLAUDE.md 說「當某個領域的第一份 code
> 出現時，再從 lighthouse-saas-api 抄對應的規則過來」—— 測試的第一份 code 現在有了，
> 所以下次擴充測試前，該把那份規則抄過來。
