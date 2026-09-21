# claude-boba 🧋

同事之間的 **Claude 代跑互助平台**。

額度用完的人把手上的對話丟進來，還有額度的同事用**自己的帳號、在自己的機器上**代為跑完，
結果交還。系統算出這次代跑在外面要花多少錢，換算成「請喝飲料 / 請吃飯」的人情債。

**不收錢，只記人情。**

---

## 現在的狀態

**Phase 1 可跑。** 提交一個 job，它會真的在出租者的機器上執行，事件即時串回畫面。

所有決策與理由在 [SPEC.md](SPEC.md)（系統）與 [docs/web-spec.md](docs/web-spec.md)（畫面）。
Phase 0 的六項 spike 全數通過，紀錄在 SPEC.md §11 —— 其中四項推翻了原本的設計。

尚未實作：認證（Observ）、人情債帳本、排行榜、Teams 通知、檔案上傳。

## 跑起來

三個元件，三個終端機：

```bash
# 1. Hub（含 postgres + minio）
cd hub && docker compose up -d
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload --port 8787

# 2. Worker（出租者端。跑在 host 上，不在容器裡）
cd worker && docker compose up -d          # egress 白名單 proxy
docker build -t claude-boba-worker:2.1.278 .
cp .env.example .env                       # 填 CLAUDE_CREDENTIALS
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python worker.py

# 3. Web
cd web && npm install && npm run dev        # http://localhost:5173
```

`hub/smoke.sh` 可以在沒有 worker 的情況下打過一遍協定。

## 這不是什麼

- **不是帳號共享平台。** 憑證從頭到尾不離開出租者的機器。
  Anthropic 官方沒有任何支援「A 把訂閱額度給 B」的機制，分享憑證會違反使用條款，
  而且風險非對稱 —— 被停權的是出租者。
- **不是付費市集。** 不收錢、不轉帳。
- **不是公司正式系統。** Side project。

## 開發環境

```bash
python3 -m venv .venv
.venv/bin/pip install ruff==0.16.1 pre-commit
.venv/bin/pre-commit install
```

`ruff` 釘在 0.16.1 —— 跟 `lighthouse-saas-api` 同一版，換版本會格式化出不同結果。

本機 venv 目前是 Python 3.12，而部署目標是 3.11（見 SPEC.md §3）。`pyproject.toml`
的 `target-version = "py311"` 會擋下 3.12-only 的語法，所以這個落差不致於讓東西
溜進去；但等 Dockerfile 出現時，image 要用 3.11。

## 給 agent 的說明

見 [CLAUDE.md](CLAUDE.md) 與 [.claude/rules/](.claude/rules/)。
