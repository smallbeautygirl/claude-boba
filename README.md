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

尚未實作：認證（Observ）、人情債帳本、排行榜、Teams 通知。

檔案上傳已完成（2026-09-22）：session 檔 `.jsonl` 走 `--resume`，其他附件平鋪進工作
目錄；要帶整個專案就壓成一個含 `.git` 的 zip。

## 跑起來

三個元件，三個終端機：

```bash
# 1. Hub（含 postgres + minio）
cd hub && docker compose up -d
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                       # 填 OBSERV_* 與 TOKEN_ENCRYPTION_KEY
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

認證來源（`OBSERV_BASE_URL` / `OBSERV_SERVICE_ID`）**沒設 Hub 就不會啟動** ——
它們刻意不寫在程式裡（內部位址不該由原始碼提供），而少了它們沒有人登得進來，
失敗又長得像「帳號密碼錯了」。內部部署的人跟站台管理者要這兩個值。

`hub/smoke.sh` 可以在沒有 worker 的情況下打過一遍協定。

## 代跑者交出的是什麼（2026-09-22 改過）

**原本這裡寫「憑證從頭到尾不離開出租者的機器」。那句話已經不成立。**

代跑者現在跑 `claude setup-token` 拿一組**一年期** token 貼進網頁，存在共用主機
上（加密），所有 job 在那台主機跑。換來的是沒有人需要安裝任何東西。

放棄的是這個專案原本最硬的那條論述。誠實地說：憑證會離開你的機器、job 不在你的
機器上跑、外洩的損失上限從 8 小時變成一年。**風險仍然由代跑者承擔，而且他少了
兩樣控制手段。**

還成立的：每個人交自己的 token、燒自己的額度，不是一個帳號大家共用；不收錢。

完整的取捨與理由寫在 [SPEC.md §1](SPEC.md)，**動這件事之前先讀它**。

## 授權

**還沒有。** 這個 repo 目前沒有 LICENSE，法律上就是保留所有權利 —— 看得到，
但不要拿去用。

不是忘了加：這是用公司帳號、在公司脈絡下寫的 side project，IP 歸屬還沒確認過，
現在掛一個開源授權等於替公司做了決定。確認完會補上。

## 正式環境

上面那三個終端機是**開發**的跑法。正式環境進容器：`hub/Dockerfile`（非 root、
`.env` 靠 `.dockerignore` 擋在外面）與 `web/Dockerfile`（vite build → nginx，
`/api/` 同源轉給 hub，`web/nginx.conf`）。compose、環境範本與 `deploy.sh`
放在 repo 外的部署目錄（目前是這台機器的 `/home/vivianfan/money/production`）——
那裡有真的密碑，不該進版控。跟開發環境同一台機器、port 與 volume 全部錯開，
細節見該目錄的 README。

## 這不是什麼

- **不是付費市集。** 不收錢、不轉帳。
- **不是公司正式系統。** Side project。
- **許願板不是需求媒合。** 那面牆（[web-spec §12](docs/web-spec.md)）收的是「這個產品
  哪裡不好用」，不是「誰能幫我跑這個」，牆上也**沒有接單這回事** ——
  「接單」在這個專案裡專指代跑者要不要收 job。它是試玩期的鷹架，有下架條件。

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
