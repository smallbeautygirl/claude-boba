# claude-boba 🧋

同事之間的 **Claude 代跑互助平台**。

額度用完的人把手上的對話丟進來，還有額度的同事用**自己的帳號、在自己的機器上**代為跑完，
結果交還。系統算出這次代跑在外面要花多少錢，換算成「請喝飲料 / 請吃飯」的人情債。

**不收錢，只記人情。**

---

> ### 路過的人請先看這段
>
> 這是一個**特定公司內部的 side project**，不是可以拿去用的產品。
>
> - **跑不起來。** 登入綁死在該公司內部的認證服務上（`OBSERV_BASE_URL`），
>   那個位址不在這個 repo 裡，外面的人拿不到，也沒有其他登入方式。
> - **沒有授權條款。** 沒有 `LICENSE` 就是保留所有權利 —— 看得到，但不要拿去用。
>   原因寫在下面的「授權」一節。
> - **沒有維護承諾。** 沒有支援、沒有版本、沒有相容性保證，隨時可能整包不做了。
> - **文件很坦白，那是刻意的。** `SPEC.md` 與 `docs/web-spec.md` 記錄的包含
>   **對自己不利的取捨**，特別是 §1 那段合規論述。那些不是還沒修掉的問題，
>   是知情的決定與它的代價 —— 照著抄之前先把理由讀完。
>
> 如果你是來看「一個把設計理由寫下來的 repo 長什麼樣」的，歡迎。
> 如果你想在自己的組織裡做類似的事，**請先讀 [SPEC.md](SPEC.md) §1**：
> 它講的是這種系統把風險放在誰身上。

---

## 現在的狀態（2026-09-23）

**Phase 0、1 完成，Phase 2 進行中；正式環境已在公司內網上線，開始給同事試玩。**

- **Phase 0**：spike 全數通過，紀錄在 [SPEC.md §11](SPEC.md) —— 前六項裡有四項推翻了
  原本的設計，之後又補到 #13（OAuth 授權流程的可行性）。
- **Phase 1**：提交一個 job，站台的領單主機真的執行，事件即時串回畫面。
- **Phase 2**：Observ 登入、人情債帳本、Teams 通知（頻道 + 個人 webhook）、管理頁、
  許願板、介紹頁都已實作。帳本用兩個真使用者走完過一整圈；其餘還沒用真實帳號
  完整驗過。

已完成的里程碑：

| 日期 | 事 |
|---|---|
| 2026-09-22 | 附件上傳：session 檔 `.jsonl` 走 `--resume`，其他附件平鋪進工作目錄；整個專案就壓成含 `.git` 的 zip |
| 2026-09-22 | 代跑者改成交出憑證、job 在共用主機跑（見下一節，這是整份文件最重要的改動） |
| 2026-09-23 | 憑證從一年期的 `setup-token` 改成站台自己跑 OAuth：8 小時 access token + 可續期的 refresh token，「不再出借」會真的撤銷。帳號卡顯示對應的 Claude email，同一個 Claude 帳號不會重複出借 |
| 2026-09-23 | 正式環境容器化上線：`hub/Dockerfile`、`web/Dockerfile`，部署設定在 repo 外（見「正式環境」） |

還沒做、或做了還沒驗：

- **排行榜**：頁面是佔位，SPEC §4.9 的內容還沒實作。
- **OAuth 授權還沒端到端走通過一次**：實作完成當天以為被限流擋住，傍晚查明是
  hub 送的 User-Agent 抄錯、被 Cloudflare 一律回 429（SPEC §11 #13 的更正）。
  已修，但還沒有人用真的授權碼走過。開放給同事前站台管理者要自己先走一次。
- **隱私勾選的文案**還點名代跑者，但新模型下看得到內容的是主機管理者，不是他
  （[CLAUDE.md](CLAUDE.md) 列的兩條待辦）。
- **LICENSE**：見「授權」。

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

## 代跑者交出的是什麼（2026-09-22 改過，2026-09-23 再改）

**原本這裡寫「憑證從頭到尾不離開出租者的機器」。那句話已經不成立。**

代跑者現在在網頁按「開始出借」，到 Anthropic 的授權頁登入、把授權碼貼回來。
站台拿到一張 **8 小時**的 access token 和一張**可續期**的 refresh token，加密存在
共用主機上，所有 job 在那台主機跑。換來的是沒有人需要安裝任何東西。

（2026-09-22 到 09-23 之間是 `claude setup-token` 的一年期 token。換掉的理由：
那種 token 的授權範圍問不到帳號 email，代跑者看不出自己出借的是哪個帳號；而
OAuth 的 refresh token 雖然效期會一直往後推，但**能撤銷** —— 按「不再出借」站台
會真的去 revoke，不是只刪掉自己那份。SPEC.md §11 #12、#13。）

放棄的是這個專案原本最硬的那條論述。誠實地說：憑證會離開你的機器、job 不在你的
機器上跑、外洩的損失上限從「8 小時」變成「直到有人發現並撤銷」。**風險仍然由
代跑者承擔，而且他少了兩樣控制手段。**

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
