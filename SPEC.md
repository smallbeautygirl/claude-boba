# Claude 代跑互助平台 — 設計規格 v0.1

> **狀態：草案，待確認。** 本文件由一次完整的設計訪談產出，記錄所有已拍板的決策及其理由。
> 尚未寫任何程式碼。確認後才進入實作。

---

## 1. 這是什麼

同事之間的 **Claude 代跑互助平台**。當某人的 Claude 額度用完，他可以把手上的對話丟進來，由另一位還有額度的同事，用**他自己的帳號、在他自己的機器上**代為跑完，把結果交還。系統計算這次代跑在外面要花多少錢，換算成「請喝飲料 / 請吃飯」的人情債。

**TA**：BD / PM **與 RD 混合**。這兩群人的使用型態差很多，是本專案最重要的設計張力——見 §4.2 與 §12。

**不是什麼：**

- 不是帳號共享平台。憑證從頭到尾不離開出租者的機器。
- 不是付費市集。不收錢、不轉帳，只記人情。
- 不是公司正式系統。side project，放個人 GitHub repo。

**為什麼是代跑而不是借帳號**：Anthropic 官方文件已確認，不存在任何支援「A 把訂閱額度提供給 B」的機制。Remote Control 的 QR code 綁定啟動者自己的帳號，官方原文：*"grants no one else access"*。Session 共享只能唯讀觀看，token 永遠算在 session 擁有者頭上。分享憑證會違反使用條款，且風險非對稱——被停權的是出租者。

---

## 2. 名詞

| 名詞 | 意思 |
|---|---|
| **借用者** (Borrower) | 額度用完、提交 job 的人 |
| **出租者** (Lender) | 有額度、跑 worker 的人 |
| **Hub** | 中央服務：Web UI + API + DB + 派單 |
| **Worker** | 出租者自己機器上的 Docker 服務，領單、跑 job、回傳 |
| **Job** | 一次代跑請求 |
| **人情債** (Debt) | job 成功後掛在借用者身上的一筆帳，單位是飲料/便當 |

---

## 3. 系統組成

```
借用者瀏覽器 ──► Hub (FastAPI)  ──►  PostgreSQL
                    │  │
                    │  └────────────►  MinIO      (輸入/輸出/transcript)
                    │  └────────────►  Observ     (認證，外部)
                    │  └────────────►  Teams      (通知，webhook)
                    │
                    ▼  長輪詢領單 / 回報
              Worker (出租者機器，docker compose)
                    │
                    └──► Docker 容器 ──► claude -p ──► api.anthropic.com
                         (egress 白名單)
```

**技術棧**：FastAPI + Python 3.11 + Ruff + PostgreSQL + MinIO + Docker Compose。
與 `lighthouse-saas-api` 保持一致，既有經驗可直接套用。

---

## 4. 核心決策

### 4.1 執行模式：代跑，不是借帳號
借用者提交工作，出租者的 worker 執行，只有 output 回傳。憑證不離開出租者機器。

### 4.2 上下文交付：兩條路

| 來源 | 方式 | 機制 |
|---|---|---|
| claude.ai / Claude app | 貼上對話文字 | 當成新 prompt **重建**上下文 |
| Claude Code | 上傳 `.jsonl` | `claude --resume <transcript-path>`，官方支援的**真續跑** |

附件另外上傳（存 MinIO input prefix）。

**兩條路都是第一級公民**，因為 TA 橫跨 BD/PM（用 claude.ai / Claude app）與 RD（用 Claude Code）。
這不是「先做一條、另一條之後補」——見 §12 的排程調整。

> ⚠️ **UI 必須明講**：貼上那條路是重建上下文，不是真續跑。不講清楚，使用者會覺得「接不上」。

### 4.3 隱私：誠實揭露 + 預設不看
job 內容跑在出租者機器上，技術上出租者有能力看到。**不宣稱看不到**——那是虛假安全感。

- 預設全自動執行，出租者不經手
- 提交介面明寫「技術上 <出租者> 有能力看到你的內容」
- 明寫「不要送公司機密、客戶個資、任何你不想被看到的東西」
- worker 用 `--no-session-persistence`，job 不留在出租者的 session 歷史

### 4.4 執行沙箱：Docker，egress 白名單
每個 job 跑在獨立容器，只掛載該 job 的工作目錄，跑完銷毀。出租者的 Claude Code 憑證以 **唯讀 volume** 掛入，不進 image。

**網路**：預設只放行 `api.anthropic.com` + MinIO。
代價：**WebFetch 與 pip/npm 安裝會失效**（兩者都是本機發出的請求）。
出租者可設 `ALLOW_FULL_NETWORK=true` 自行承擔風險開放。

### 4.5 派單：自動 + 上限
出租者一次性設定「每日最多借出 N 元 / M 個 job」，之後全自動，跑完才收通知。**併發上限預設 1**（多個 job 會互搶 rate limit，出租者自己也要用）。

保留「指定對象借」當備援——有些 job 本來就只適合給特定的人，而且點名借讓人情債更具體。

### 4.6 記帳：API 等價金額
存兩套資料，顯示 API 等價金額。

**為什麼不用 token 數當單位**：同樣 100 萬 token，Opus 輸出比 Haiku 輸入貴 25 倍。token 數在記帳上沒有意義，必須先折算。

**為什麼不用「吃掉多少額度」**：概念上更正確（Max 的邊際成本是零，真正的損失是額度被佔用），但 Max 的用量限制是滾動視窗制，換算出來既難算又難解釋。原始資料全存，未來想改隨時能重算。

### 4.7 人情債級距

| API 等價金額 | 人情債 |
|---|---|
| < US$1 | 🤝 不用還，人情而已 |
| US$1 – 3 | 🧋 一杯手搖 |
| US$3 – 8 | ☕️ 一杯咖啡 + 一份點心 |
| US$8 – 20 | 🍱 一個便當 |
| > US$20 | 🍖 一頓好料，而且你要挑餐廳 |

**最底下那條「< US$1 不用還」是整張表最重要的一列。** 多數 job 會落在這一格。讓多數互動不欠債，真正欠債時才顯得慎重；每次都掛帳，債務會變噪音。

**全站共用一張表，不給出租者自訂。** 一旦可改就會有人比價，人情變市集。

### 4.8 結清：債主說了算
出租者單方按「他還了」即結清。借用者有「我請過了，戳他確認」按鈕——把催促責任放在欠債的人身上。

**債務不設到期日，但顯示「欠了幾天」。** 比自動勾銷更有社交壓力，也更好笑。

### 4.9 社交層：帳本 + 排行榜
欠債王、金主榜、本月最大宗 job。**純聚合數字，不洩漏內容**——不做動態牆（會踩到 4.3 的隱私線）。

「欠債王」三個字本身就是最好的推廣文案。

### 4.10 認證：走 Observ
沿用 `lighthouse-saas-api` 既有機制（複製過來，不依賴該 monorepo）：

- `POST {base}/users/token` 取 token
- `GET /auth/users/me` 驗證，回傳 `{id, email}`
- 需要 `X-Service-Id` header

**Service ID**：`e39940ea-1fdf-4527-a3b7-c8d6334e5d2e` — ✅ 已取得，此項不再是阻斷項。

> ⚠️ 若 repo 設為 **public**，把這個值移出版控：只留在 `.env`，`.env.example` 留空白。
> 它本身不是憑證（仍需 email + password 才能登入），但屬於內部基礎建設資訊，沒必要公開。

### 4.11 範圍：玩具規模，四條架構讓步
先給 5–10 人用，但預留成長空間。**只做這四件事**，其餘一律 YAGNI：

1. DB 用 PostgreSQL，不用 SQLite
2. 檔案存取走 S3 介面（MinIO）
3. 認證抽一層 interface，v1 實作是 Observ
4. 每個 job 的完整紀錄都留著（誰、何時、用了多少、什麼 model）——未來要分析，資料補不回來

**明確不做**：多租戶、細緻權限、API 版本控制、微服務拆分、message queue（用 DB 當佇列）、i18n。

---

## 5. Job 狀態機

```
        ┌──────────► cancelled (借用者取消，不計債)
        │
queued ─┼──────────► expired   (15 分鐘無人接單，通知借用者，不計債)
        │
        └─► claimed ─► running ─┬─► succeeded  ★ 唯一計債的狀態
                                ├─► failed     (崩潰/拒答，不計債)
                                ├─► timeout    (超過 10 分鐘，殺容器，不計債)
                                └─► cancelled  (跑到一半取消，不計債)
```

**只有 `succeeded` 計債。**

嚴格說取消時 token 確實燒掉了，出租者有損失。但金額通常很小，而**一條簡單的規則比一條公平的規則值錢得多**——不值得為此處理「跑到一半取消該付多少」的爭議。用寬鬆規則換掉整類客訴。

**逾時**：硬上限 10 分鐘，出租者可在自己的 worker 下調，但有全站上限。

---

## 6. 資料模型

```
users        id, observ_user_id, email, display_name, created_at

workers      id, owner_user_id, name, status, last_seen_at,
             daily_budget_usd, daily_job_limit, max_concurrency (預設 1),
             allow_full_network (預設 false), timeout_seconds

jobs         id, borrower_id, worker_id, lender_id, status, model,
             source_type (paste | transcript),
             input_prefix, output_prefix,
             claude_code_version_borrower, claude_code_version_lender,
             created_at, claimed_at, started_at, finished_at,
             error_kind, error_detail

usages       id, job_id, model,
             input_tokens, output_tokens,
             cache_creation_tokens, cache_read_tokens,
             computed_usd, source (cli_json | transcript)

debts        id, job_id, borrower_id, lender_id,
             amount_usd, tier (none|drink|coffee|bento|feast),
             status (open | nudged | settled),
             created_at, nudged_at, settled_at, settled_by
```

---

## 7. 計價公式

```
job_usd = Σ over usages:
      input_tokens          / 1e6 × input_rate(model)
    + output_tokens         / 1e6 × output_rate(model)
    + cache_creation_tokens / 1e6 × cache_write_rate(model)
    + cache_read_tokens     / 1e6 × cache_read_rate(model)
```

**已確認的價格（每 MTok）：**

| Model | Input | Output |
|---|---|---|
| Opus 5 | $5 | $25 |
| Sonnet 5 | $2 | $10 |
| Haiku 4.5 | $1 | $5 |

> ⚠️ **cache read / cache write 的費率尚未確認**，實作前必須查官方定價頁。
> 這件事影響很大：Claude Code 的 session 有極高比例是 cache read，
> 若誤按一般 input 價計算，帳單會灌水數倍。

價格表存 DB，不寫死在程式裡——model 和價格都會變。

**資料來源**：以 `claude -p --output-format json` 的回傳值為準（官方承諾給腳本使用的穩定介面），`.jsonl` 全份存進 MinIO 當明細與稽核。

官方對 `.jsonl` 的警告：*"The entry format is internal to Claude Code and changes between versions, so scripts that parse these files directly can break on any release."* 若計費邏輯依賴 parse transcript，會週期性壞掉；改用 CLI 的 JSON 輸出，壞掉的頂多是看不到明細，不會算錯錢。

---

## 8. 儲存配置（MinIO）

**單一 bucket + prefix 分區**，不開個別帳號或 bucket：

```
claude-rental/
  jobs/{job_id}/input/      借用者上傳的附件、貼上的對話、.jsonl
  jobs/{job_id}/output/     產出檔案
  jobs/{job_id}/transcript.jsonl
```

- 下載用 **presigned URL**（本身就是有時效的授權），不用替每個人開 MinIO 帳號或寫 bucket policy
- 輸入輸出同 bucket，worker 拿 job id 就能取齊所有東西
- **lifecycle rule 30 天自動刪**，同時解決儲存無限成長與「別人的資料躺在我硬碟上多久」

---

## 9. Worker 部署（出租者端）

`docker compose up -d` + 一份 `.env`：

```
OBSERV_EMAIL=
OBSERV_PASSWORD=
OBSERV_SERVICE_ID=e39940ea-1fdf-4527-a3b7-c8d6334e5d2e
HUB_URL=
DAILY_BUDGET_USD=5
DAILY_JOB_LIMIT=10
MAX_CONCURRENCY=1
TIMEOUT_SECONDS=600
ALLOW_FULL_NETWORK=false
```

Claude Code 憑證以 **唯讀 volume** 掛入 worker 容器。

worker 執行 job 的指令形狀：

```bash
claude -p "$task" [--resume "$transcript"] \
  --model "$model" --output-format json \
  --bare \
  --allowedTools "Read,Edit,Bash" \
  --permission-mode acceptEdits --permission-prompts none \
  --no-session-persistence \
  --add-dir "$workdir"
```

`--bare` 跳過 hook / skill / MCP 自動載入——不能讓借用者的 job 載到出租者的個人設定。

---

## 10. 通知（Teams webhook）

| 事件 | 通知對象 |
|---|---|
| Job 完成 | 借用者 |
| Job 失敗 / 逾時 | 借用者 |
| 15 分鐘無人接單 | 借用者 |
| 掛債 | 雙方 |
| 被戳（借用者宣稱已請客） | 出租者 |
| 債務結清 | 雙方 |

站內通知作為 fallback。

---

## 11. 尚未驗證的風險 — 第一週 spike 清單

**按這個順序做，每一項都可能推翻上面的設計。**

| # | 要驗什麼 | 失敗的話 |
|---|---|---|
| 1 | `claude -p --output-format json` 回傳值裡到底有沒有**分 model、分類型**的 token 用量與成本欄位 | 計費改回 parse `.jsonl`（§7 要改寫） |
| 2 | `claude --resume <別台機器來的 .jsonl 絕對路徑>` 實際能不能跑 | Claude Code 那條路砍掉，只留貼上 |
| 3 | **版本漂移**：借用者與出租者 Claude Code 版本不同時，resume 的行為 | 提交時強制比對版本，不符就擋下 |
| ~~4~~ | ~~Observ service id~~ | ✅ 已取得：`e39940ea-1fdf-4527-a3b7-c8d6334e5d2e` |
| 5 | egress 白名單下 Claude Code 能否正常運作（含 server-side 工具） | 放寬白名單或改設計 |
| 6 | cache read / cache write 的官方費率 | — 必查，不做會算錯錢 |

### TA 橫跨 BD/PM 與 RD 的後果

原本假設 TA 只有 BD/PM，現已確認 RD 也算。這推翻了三個設計前提，spike 時要一併重新評估：

| 受影響的決策 | 原本的假設 | RD 加入後 |
|---|---|---|
| §4.4 egress 白名單 | BD/PM 的對話型任務用不到外網，擋掉沒差 | RD 的 job 常需要 `pip install` / `npm install` / WebFetch，**白名單會經常擋到人** |
| §5 逾時 10 分鐘 | 對話型任務幾分鐘就結束 | RD 的重構或測試任務**很容易超過 10 分鐘** |
| §12 交付順序 | Claude Code 路徑排在最後 | RD 是 `.jsonl` 真續跑的唯一受眾，**排最後等於半個 TA 拿不到最好的體驗** |

**待決**（建議在 Phase 0 結束時拍板，屆時 spike #2/#3 的結果也出來了）：

- **網路**：把 `ALLOW_FULL_NETWORK` 的預設值改成由**出租者在安裝時明確選擇**（不給預設），並在 UI 上標示每個 worker 的網路模式，讓借用者自己挑。
- **逾時**：預設拉到 30 分鐘，出租者可自行調低。對話型任務本來就不會跑那麼久，上限拉高不影響它們。
- **交付順序**：見 §12。

---

## 12. 交付順序

**Phase 0 — spike**（§11，約 2–3 天）
先把六個風險驗完，該改的設計先改。

**Phase 1 — 能跑通一次**
單一出租者、單一借用者、貼上路徑、無認證、輸出直接下載。目標是**端到端跑通一個 job**。

**Phase 2 — 記帳**
usages 計算、債務級距、帳本頁面、Observ 認證接上。

**Phase 3 — 多人**
派單佇列、worker 上限、Teams 通知、排行榜。

**Phase 4 — Claude Code 路徑**
`.jsonl` 上傳、真 resume、版本比對。

> ⚠️ **這個順序是在「TA 只有 BD/PM」的假設下排的，該假設已經不成立。**
> RD 也是 TA，而 RD 是 `.jsonl` 真續跑的唯一受眾。把它排在最後，等於讓半個 TA 一直用次等體驗。
>
> **建議**：Phase 0 的 spike #2（跨機器 resume 可行嗎）與 #3（版本漂移）結果出來後重排。
> 若兩者都通過，把 Phase 4 併進 Phase 1 —— 兩條路共用同一個 job 提交流程，
> 差別只在 worker 端多一個 `--resume` 參數，增量成本遠低於獨立做一個 Phase。
> 若 #2 不通過，Phase 4 直接砍掉，RD 也走貼上路徑。
