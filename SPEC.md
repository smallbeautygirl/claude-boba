# Claude 代跑互助平台 — 設計規格 v0.1

> **狀態：草案，待確認。** 本文件由一次完整的設計訪談產出，記錄所有已拍板的決策及其理由。
> 尚未寫任何程式碼。確認後才進入實作。

---

## 1. 這是什麼

同事之間的 **Claude 代跑互助平台**。當某人的 Claude 額度用完，他可以把手上的對話丟進來，由另一位還有額度的同事，用**他自己的帳號**代為跑完（執行地點見下方的合規論述 —— 2026-09-22 從「他自己的機器」改成共用主機），把結果交還。系統計算這次代跑在外面要花多少錢，換算成「請喝飲料 / 請吃飯」的人情債。

**TA**：BD / PM **與 RD 混合**。這兩群人的使用型態差很多，是本專案最重要的設計張力——見 §4.2 與 §12。

**方案現況**（2026-09-21 實測）：大家用的是公司的 **Team plan**，不是個人自費的 Max。
成員之間的 rate limit tier 不一致——部分成員是 20x，其餘較低。**出租者池就是 tier 較高的那群人**，
這個不對稱正是本專案存在的理由。

> 兩件因此要注意的事：
> 1. 借出的是**公司資源**，不是個人自費的額度。人情債的社交意義因此不同——這是刻意保留的設計，
>    但值得先跟帳號管理員確認公司立場（見 §11）。
> 2. 本機憑證檔回報的 tier 是 `default_claude_max_5x`，與「我們有人是 20x」的認知不符。
>    **未解**，需要用 `/usage` 對帳。經濟模型建立在出租者有餘裕上，這個數字要準。

**不是什麼：**

- 不是付費市集。不收錢、不轉帳，只記人情。
- 不是公司正式系統。side project，放個人 GitHub repo。

### ⚠️ 合規論述改過（2026-09-22）

**原本這裡寫「不是帳號共享平台。憑證從頭到尾不離開出租者的機器。」那句話現在
不成立了，而且那不是筆誤，是一個知情的取捨。**

原本的模型：每位代跑者在自己機器上跑 worker，job 在他的機器上執行，憑證從不
外流。那讓「這是代跑，不是借帳號」站得住 —— 你人在現場、用你自己的機器、跑完
就結束。

現在的模型：代跑者跑 `claude setup-token` 拿一組**一年期** token，貼進網頁，
存在共用主機上（加密，金鑰在 DB 外），所有 job 都在那台主機跑。

換來的是：沒有人需要安裝任何東西。原本擋路的三件事一起消失了 —— session token
8 小時過期、唯讀掛載寫不回刷新後的值、共用主機上沒有人會去刷新它。

**放棄的是這條論述最硬的那一半。** 誠實地說：

| | 舊模型 | 新模型 |
|---|---|---|
| 憑證離開代跑者的機器嗎 | 不會 | **會** |
| job 在誰的機器上跑 | 代跑者自己的 | 共用主機 |
| 代跑者能不能當場看到／中止 | 能（他的機器） | 只能透過 UI |
| 外洩的損失上限 | 8 小時 | **一年** |

**還成立的：** 每個人交的是自己的 token、用的是自己的額度，不是一個帳號大家共用；
交出去是自己按的，不是被代管；不收錢。

**不再成立的：** 「憑證不離開」。這條路在合規上**比舊的弱**，不是一樣強。
§11 spike #8 那句 Anthropic 官方的話（*"grants no one else access"*）沒有變 ——
變的是我們離它更遠了。

**風險仍然非對稱，而且更不對稱：** 被停權的還是代跑者，但他現在少了兩樣東西 ——
他的機器不再是執行現場，而他交出去的鑰匙一年有效。

**所以這件事的處理方式是揭露，不是主張。** UI 上代跑者交出 token 的那一步，
要讓他知道他交出的是什麼、有效多久、誰拿得到。不要寫成「連結你的帳號」那種
把風險藏起來的說法。

#### 🚨 順帶被改掉的一件事：看得到內容的人換了

這一條沒有人提，但它動到 §9 的隱私設計。

舊模型下 job 在代跑者的機器上跑，所以「代跑者技術上看得到你的內容」是真的，
提交頁的勾選因此**點名**他（而且換出租者時會重置）。

新模型下 job 在共用主機上跑。**代跑者反而看不到了** —— 看得到的是主機管理者。

所以那個勾選現在點名了一個看不到的人，卻沒有提真正看得到的人。**那比不揭露
更糟**：它讓使用者以為自己知道風險在哪。§9 的文案要一起改，對象從代跑者換成
主機管理者。

> **值得先跟帳號管理員確認公司立場**（原本就該做，現在更該做）。這一條在舊模型
> 下是「借出公司資源」的問題，新模型多了一個「長期憑證集中存放」的問題。

**為什麼仍然不是借帳號**：Anthropic 不存在任何支援「A 把訂閱額度提供給 B」的機制
（Remote Control 的 QR code 綁定啟動者自己的帳號，官方原文 *"grants no one else
access"*；session 共享只能唯讀觀看，token 永遠算在擁有者頭上）。所以這個系統做的
仍然是「用你的額度、替你跑、把結果還你」，只是執行的地點從你的機器換成了共用主機。
**這個區分變薄了，但沒有消失。**

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

**為什麼記帳不用「吃掉多少額度」**：作為**債務單位**，百分比難以換算成飲料。
原始資料全存，未來想改隨時能重算。

> 📌 **但額度佔比本身是拿得到的**（2026-09-21 實測推翻了原本「滾動視窗難算」的判斷）。
> `--output-format stream-json` 會吐 `rate_limit_event`：
>
> ```json
> "unifiedWindows": {
>   "five_hour": { "utilization": 0.02, "resetsAt": ... },
>   "seven_day": { "utilization": 0.12, "resetsAt": ... }
> }
> ```
>
> 它**不用於記帳**，但用於三件事：
> 1. §4.5 的出租者上限改用真實額度（「最多借到五小時窗口的 60%」比「最多借 $5」精準）
> 2. 自動派單挑最有餘裕的人，而不是輪流
> 3. Web 上顯示紅綠燈（`docs/web-spec.md` §3）
>
> 這個數字是相對各自 tier 計算的，所以不論出租者是 5x 或 20x 都適用。

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
                                ├─► over_budget(超過 --max-budget-usd，不計債)
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

## 7. 計價

**不自己算費率。** CLI 已經算好，且明確標示它是按 API 標價算的。

```
job_usd = total_cost_usd        # 等於 Σ modelUsage[*].costUSD
```

原本規劃的自維護價格表（每個 model 的 input / output / cache 費率）**已刪除** ——
不需要追著 model 改價維護它。

**資料來源**：worker 實際使用 `--output-format stream-json`（見下），其最後一個
`result` 事件帶有與 `--output-format json` 相同的 `total_cost_usd` 與 `modelUsage`。
完整事件流存進 MinIO 當明細與稽核。

> 🚨 **worker 絕不可設定 `modelPricing`。** 這個設定鍵會把成本改用「組織合約價」計算，
> 官方說明明寫它 *"Affects every spend figure Claude Code reports — … the SDK
> `total_cost_usd`"*。目前 worker 用乾淨 HOME、沒有 settings.json，所以是 list price
> （實測 `costBasis: "list"` 印證）。若有人「順手」加上合約價，整本帳會錯而且不會報錯。

**實測確認的欄位**（2026-09-21，CLI 2.1.278）：

```
total_cost_usd          ← CLI 算好的總成本
modelUsage  {…}         ← 以帶日期的 model id 為 key，每個 model 一組分項（結構見下）
usage {
  input_tokens, output_tokens,
  cache_creation_input_tokens, cache_read_input_tokens,   ← 分開的，這是關鍵
  output_tokens_details.thinking_tokens,
  cache_creation.{ephemeral_1h,ephemeral_5m}_input_tokens,
  service_tier, speed, server_tool_use
}
session_id, num_turns, duration_ms, is_error, result, terminal_reason
```

cache read 與 cache creation 是分開的欄位，上面那個「算錯會灌水數倍」的風險解除。

### 執行中是有進度的（2026-09-21 實測，推翻先前判斷）

先前記錄為「`--output-format json` 是結束才一次回傳，執行中拿不到進度」。
那句話只對 `json` 成立。改用 **`--output-format stream-json --verbose`** 會邊跑邊吐事件：

```
system/init
rate_limit_event              ← 出租者的額度使用率，見 §4.6
assistant/ thinking
assistant/ tool_use:Read      ← 看得到 Claude 在讀哪個檔
user/ tool_result
assistant/ text:"…"
result/success  cost=0.0256
```

**因此「借用者在畫面上看著 Claude 一步步做」是可行的**，這是 web 端 SSE 的
真正理由（`docs/web-spec.md` §4）。worker 因此一律用 `stream-json`，
把事件轉發給 Hub，最後一個 `result` 事件同時作為計費依據。

**`modelUsage` 的實際結構**（同一趟實測）：

```json
"modelUsage": {
  "claude-haiku-4-5-20251001": {
    "inputTokens": 908, "outputTokens": 54,
    "cacheReadInputTokens": 22333, "cacheCreationInputTokens": 7991,
    "thinkingTokens": 34, "webSearchRequests": 0,
    "costUSD": 0.0193933,
    "contextWindow": 200000, "maxOutputTokens": 32000,
    "canonicalModel": "claude-haiku-4-5",
    "provider": "firstParty",
    "costBasis": "list"
  }
}
```

**`costBasis: "list"` 是關鍵欄位** —— 它明確標示成本是按 **API 標價**計算，不是訂閱實付。
這正是 §4.6 選定的「API 等價金額」，不用猜也不用自己換算。
若未來出現其他 `costBasis`，據此分支即可。

key 是帶日期的 model id（記帳用），`canonicalModel` 是不帶日期的（顯示用）。

**`total_cost_usd` 在 Team 訂閱下確實回 API 等價金額（2026-09-21 實測，spike #1b 已答）：**

一次 `claude -p "Reply with exactly: pong" --model haiku` 的完整回傳：

```
total_cost_usd  0.0166103
input_tokens                 10
output_tokens                53   (thinking_tokens 45)
cache_creation_input_tokens  7465 (全數 ephemeral_1h)
cache_read_input_tokens      14053
```

不是 0。**§7 的自維護價格表可以砍掉**，直接採信 `total_cost_usd`，分項 token 只留作明細
與稽核。這也連帶讓風險 #6（cache 費率）從「必查，不做會算錯錢」降級為「不影響計費」。

> ⚠️ 保留一個退路：`total_cost_usd` 是 CLI 算的，若哪天某個 model 回 0 或缺欄位，
> 要能 fallback 回分項折算。所以價格表的 schema 先留著，只是不再是計費主路徑。

**這個數字對 §4.7 級距表的意義比對 §7 大：**

上面那趟 job 的 prompt 是六個字，產出是四個字母，成本 US$0.0166 —— 其中
21518 tokens（cache read + cache write）是**與任務內容無關的固定開銷**，真正的
input 只有 10 tokens。也就是說：

- 一個「小任務」的底價不是趨近於零，而是**大約一杯飲料的 1/100**
- 級距表若以「跑幾次才夠一杯飲料」來想，答案是**上百次**，不是十幾次
- 換句話說，真正會撞到級距的是長時間的 RD 任務，對話型任務幾乎永遠落在「不計債」

這不推翻 §4.7 的粗級距設計（那是社交決策，不是成本決策），但**級距的門檻數字要在
Phase 0 結束時用真實 job 重新校準** —— 現在的門檻若照「感覺」訂，會讓 99% 的 job
都掛在最低級距，社交層就失去意義了。

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
JOB_BUDGET_USD=5
AVAILABLE_MODELS=sonnet,haiku
```

Claude Code 憑證以 **唯讀 volume** 掛入 worker 容器。

worker 執行 job 的指令形狀：

```bash
HOME="$clean_home" claude -p "$task" [--resume "$transcript"] \
  --model "$model" \
  --output-format stream-json --verbose \
  --max-budget-usd "$JOB_BUDGET_USD" \
  --settings '{"availableModels": ["sonnet","haiku"]}' \
  --allowedTools "Read,Edit,Bash" \
  --permission-mode acceptEdits --permission-prompts none \
  --no-session-persistence \
  --add-dir "$workdir" < /dev/null
```

**`--max-budget-usd` 是逾時之外的第二道防線。** 逾時只擋「跑太久」，擋不住「跑很貴」——
一個 Opus job 在 8 分鐘內燒掉 US$25 是可能的，那會讓債務從一杯飲料跳到「你要挑餐廳」。
預設 **US$5**（對應級距表的「一個便當」），出租者可在 `.env` 調整。
超出即中止，依 §5 不計債 —— **這對借用者也是保護**。

**`availableModels` 是 model 白名單**，吃家族別名（`"opus"` 涵蓋所有 opus 版本）。
實際值是「站台白名單 ∩ 該出租者白名單」，由 Hub 算好後傳給 worker。
**站台預設不含 Fable**：它的 output 單價是 Haiku 的 10 倍、Sonnet 的 5 倍，
同一個 job 用 Haiku 是一杯手搖、用 Fable 就是一頓好料。想開放的出租者自己在 `.env` 加。

**隔離手段是乾淨的 HOME，不是 `--bare`。** 容器的 `$HOME/.claude/` 裡**只有**唯讀掛入的
`.credentials.json`，沒有 `settings.json`、`plugins/`、`skills/` 或 MCP 設定，所以出租者的
個人設定根本不存在、無從載入。實測驗證見 §11。

> 🚨 **不要加 `--bare`。** 它的官方說明明寫「Anthropic auth is strictly
> `ANTHROPIC_API_KEY` or `apiKeyHelper` (OAuth and keychain are never read)」，
> 會直接無視掛進去的 OAuth 憑證，回 `Not logged in · Please run /login`。
> 這個錯誤特別危險，因為訊息把人導向「去登入」，真正的原因是旗標。

**文件類 skill 的相依套件預裝在 image 裡。** `pptx`、`xlsx`、`docx`、`pdf`
這些 org skill 在 Anthropic 自家沙箱是預裝的（skill 文件寫著 "preinstalled"），
我們的容器沒有，而 egress 白名單只放行 `api.anthropic.com` —— 實測現象是
`npm install pptxgenjs` 回 403，然後 Claude 退而求其次產 HTML 給使用者。

預裝比放寬白名單安全：不用為了讓套件裝得起來，而讓借用者的 job 能連 npm。
代價是 image 從 805MB 漲到 1.47GB。

**沒有裝 LibreOffice。** 它是 skill 的 `thumbnail.py` 與 PDF 轉檔所需，
但不是 `validate.py` 所需 —— 而 validate 才是 skill 明列為 required 的 QA。
實測：deck 建得出來（3 頁、216KB、通過 validate），只是沒有截圖預覽。
再加 500MB+ 換一個視覺 QA 不划算，想要的出租者可以自行加進 Dockerfile。

**兩個容易漏的操作細節：**

- **`< /dev/null` 是必要的。** 沒有它，CLI 會等 stdin 三秒才繼續並印警告。
- **認證失敗會重試 11 次、指數退避，掛住約 3 分鐘才放棄。** worker 不能假設認證問題
  會快速失敗，§5 的逾時是唯一防線。

### Hub ↔ Worker 協定

worker 跑在出租者的機器上（可能在 NAT 後、可能隨時關機），所以**一律由 worker 主動連出**，
Hub 從不主動連 worker。

```
worker                                   Hub
  │  GET /api/worker/poll  (long-poll 30s)  │   領單。沒單就 hold 住，逾時回 204
  │ ◄───────────────────────────────────── │   有單回 job payload + 預簽 URL
  │                                         │
  │  POST /api/worker/jobs/{id}/events      │   串流事件，每 ~500ms 批次送一次
  │  { from_seq, events: [...] }            │   帶序號，Hub 據此去重與續傳
  │ ─────────────────────────────────────► │
  │                                         │
  │  POST /api/worker/jobs/{id}/result      │   最後的 result 事件 + 用量 + 輸出位置
  │ ─────────────────────────────────────► │
  │                                         │
  │  GET /api/worker/jobs/{id}/control      │   夾在 events 的回應裡：出租者按了停止
  │ ◄───────────────────────────────────── │
```

**設計理由：**

- **long-poll 而非 WebSocket**：worker 要傳的東西是單向批次（事件流），
  Hub 要回的只有「停止」這一個指令 —— 夾在 events 的 HTTP 回應裡就夠。
  為此維持一條雙向長連線不划算，而且 long-poll 天然容忍 worker 重啟。
- **事件帶序號**：Hub 存整份事件流（`docs/web-spec.md` §4 的「離開後回來」要用），
  序號同時解決 worker 重送的去重、與瀏覽器 SSE 斷線續傳。
- **檔案不走 Hub**：job 的輸入輸出用 MinIO 預簽 URL，worker 直接對 MinIO 讀寫。
  transcript 可能 50 MB，沒理由讓它穿過 Hub 兩次。

**瀏覽器端**：`GET /api/jobs/{id}/stream?from=<seq>`（SSE）。Hub 先重播已存的事件，
再接上即時流。斷線重連帶上最後看到的 seq。poll fallback 打同一組資料的非 SSE 版本。

**認證**：worker 帶自己的 token（安裝時產生，存在 `.env`），與借用者的 Observ token 分開。

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
| 0 | ✅ **已答**：headless CLI 認證可以動。`Not logged in` 的成因是 `--bare`，不是組織政策，**也不是全案停擺** —— 但換來一個新阻斷項，見下方 | — |
| ~~0b~~ | ✅ **已解**：`--bare` 確實不讀 OAuth，但它本來就不該用。隔離改由乾淨 HOME 達成，實測通過（見下方） | — |
| 1a | ✅ **已答**：CLI JSON 有分項 token 欄位，cache read / creation 分開（見 §7） | — |
| 1b | ✅ **已答**：`total_cost_usd` 在 Team 訂閱下回真實金額（US$0.0166 / 一趟 haiku），不是 0。§7 價格表可砍 | — |
| ~~2~~ | ✅ **已解**：`--resume <任意路徑的 .jsonl>` 可行，換路徑、換檔名、乾淨 HOME 都正常完成（見下方） | — |
| ~~3~~ | ✅ **已解**：2.1.197 ↔ 2.1.278 雙向 resume 都成功且上下文連續。閘門降級為「記錄 + 警告」，不擋下（見下方） | — |
| ~~4~~ | ~~Observ service id~~ | ✅ 已取得：`e39940ea-1fdf-4527-a3b7-c8d6334e5d2e` |
| ~~5~~ | ✅ **已解**：內部網路 + tinyproxy 白名單實測通過，Claude Code 吃 `HTTPS_PROXY`（見下方） | — |
| ~~5b~~ | ✅ **已決**：worker 跑在 host 上，自己 `docker run` job。不掛 docker.sock | — |
| 6 | ~~cache read / cache write 的官方費率~~ | 降級：#1b 已答，計費改採信 `total_cost_usd`，不再需要自算費率 |
| ~~7~~ | ✅ **已解**：Cowork 的 local session `--resume` 得起來，而且 transcript 就在 `~/.claude/projects/`，跟 CLI 同一棵樹。原本記的路徑結構是錯的（見下方） | — |
| ~~8~~ | ✅ **已解**：長期 OAuth token 當環境變數可行，`total_cost_usd` 還在。但撞出一個既有漏洞：`availableModels` 根本不擋 model（見下方）| — |

### ~~#8 長期 OAuth token 能不能取代掛載憑證~~ → 已解（2026-09-22 實測）

**為什麼要問：** 原本的模型是每位代跑者在自己機器上跑 worker。要改成「沒有人安裝
任何東西、job 都跑在同一台主機」，就需要一種不會 8 小時過期、也不需要有人在旁邊
刷新的憑證。`claude setup-token` 產生的一年期 token（`sk-ant-oat01-…`）走環境變數
`CLAUDE_CODE_OAUTH_TOKEN`，是 Anthropic 官方為非互動場景設計的通道。

**結論：機制可行。** 在乾淨 HOME、沒有掛 `.credentials.json` 的容器裡：

| 驗什麼 | 結果 |
|---|---|
| 認證過 | ✅ 正常回答 |
| **`total_cost_usd`** | ✅ 0.0162303 —— **人情債整本帳靠它，這是這次最關鍵的一項** |
| `usage` 分項 | ✅ cache creation / read 都在 |
| `--max-budget-usd` | ✅ 觸發 `budget_exhausted` |
| `--settings availableModels` | ❌ **不擋**（見下） |

**驗證腳本留在 `playground/spike8-oauth-token.sh`。**

#### 🚨 撞出來的既有漏洞：`availableModels` 不擋 model

`--settings '{"availableModels": ["haiku"]}'` 之下跑 `--model sonnet`，**sonnet
照跑並計費**。CLI 只拿那份清單擋了 fast mode（輸出裡的
`fast_mode_disabled_reason: "model_not_allowed"`）。

**這不是長期 token 造成的。** 跑對照組 —— 同一個測試改回掛 `.credentials.json`
的舊路徑 —— 結果更誇張：它跑了 `claude-opus-5[1m]`。**兩條憑證路徑都一樣**，
所以這是一直都在的漏洞，只是沒有人驗過。

沒有對照組的話，這裡會得到一個相反的結論（「長期 token 讓白名單失效，這條路
不能走」），而真正的洞會繼續留著。**這一格的教訓比 token 本身值錢：驗一個變更
時，失敗的那一項要先確認舊的做法會不會也失敗。**

已修（`c9f86dc`）：白名單改在 Hub 強制（`_check_model()`），worker 再擋一次。
細節見 §9。

#### 兩個沒有被這次驗證解決的問題

1. **一年期 token 外洩沒有損失上限。** 8 小時的 session token 洩漏還有天然的
   止血點，這個沒有。約束寫在 `.claude/rules/security.md` 紅線 2。
2. **合規論述變了**，見 §1 —— 這條路讓「憑證不離開出租者的機器」不再成立。

#### 一個踩過的坑，寫下來免得下一個人重踩

第一次跑 spike 得到 `API Error: Can't reach the API server (EAI_AGAIN)`，
看起來完全像認證失敗（容器結束碼 1、stderr 空的）。實際原因是漏了
`HTTPS_PROXY` —— job network 是內部網路，出口走白名單 proxy（紅線 3）。
**症狀長得像認證問題，成因是 DNS 出不去。**

另外 `--output-format json` 的錯誤寫在 **stdout**，不是 stderr。只印 stderr
會得到一片空白。

### ~~#7 Cowork 的 local session 能不能續跑~~ → 已解（2026-09-22 實測）

**為什麼重要：** BD/PM 主要用 Cowork（非寫程式的 agentic 工作），而那正是他們會把
額度燒爆的地方 —— 也就是這個系統存在的情境。

**結論：通了，而且比預期簡單。** `claude --resume <transcript>` 完整接上一個真實的
Cowork local session，不是退化成「把文字當上下文重貼」：

- 回傳的 `session_id` 與原 session 一致 —— 真的接上同一個，不是開了新的
- `cache_creation_input_tokens: 187031` —— 整份 3.3 MB 的 transcript 都讀進去了
- 問了一個只有讀過前文才答得出來、又不是常識的細節，答案完全正確且具體
- `num_turns: 1`、無 tool call、`permission_denials: []` —— 沒有因為找不到原始附件而卡住

transcript 刻意先複製到一個與原始 `cwd` 無關的目錄再跑，排除「剛好在同一個工作目錄
才續得上」。這與 worker 的實際用法一致 —— 容器裡本來就是全新工作目錄。

**原本記在這裡的路徑結構是錯的。** 社群逆向猜的是
`local-agent-mode-sessions/<org>/<user>/<sessionId>/.claude/projects/…jsonl`。
實測是兩層，而且 `local-agent-mode-sessions/` 裡裝的其實是 plugin/skill 的 manifest，
**不是** transcript：

| | 路徑（macOS） | 內容 |
|---|---|---|
| metadata | `~/Library/Application Support/Claude/claude-code-sessions/<org>/<user>/local_<uuid>.json` | 標題、`cwd`、model，**以及關鍵的 `cliSessionId`** |
| **transcript** | `~/.claude/projects/<encoded-cwd>/<cliSessionId>.jsonl` | 跟一般 CLI session **同一個位置、同一種格式** |

也解釋了為什麼 `--resume` 通得了：Cowork 的 local 模式底層跑的就是 Claude Code CLI
引擎，寫出來的自然是同一種檔案。

**這一點決定了提交頁怎麼寫。** 既然兩種來源的 transcript 在同一棵樹，
`ls -t ~/.claude/projects/*/*.jsonl` 一行就同時涵蓋 RD 與 Cowork —— 使用者不需要
知道自己用的是哪個分頁。提交頁因此改成「跑這行、看有沒有檔案」，不再要使用者推理
自己屬於哪一類（那段文案照職稱、照 app 名稱、照「狀態存在哪」各寫錯過一次）。

**四個保留仍然成立，這次驗證沒有解除任何一條：**

1. **沒有官方 schema。** 社群逆向，Anthropic 沒有文件化，隨時可能改。
2. **會被更新洗掉。** Desktop 小版本更新會 provision 新的 VM instance，舊 instance
   的 session 檔不會搬過去。
3. **Linux 上的 Desktop 還在 beta。**
4. **只在 macOS 上驗過** —— Linux 是否走同一套標準路徑未驗。

所以調性維持：**可用但不保證**，不能變成 BD/PM 的**唯一**指引，貼上仍然是那條一定
會動的路。提交頁那行指令是經驗性的（跑了看有沒有），所以 Linux 的未知不會害到
使用者 —— 沒有就是沒有，自然落到貼上那一邊。

### ~~`--bare` 與 OAuth 憑證互斥~~ → 已解（2026-09-21 實測）

風險 #0 原本的敘述是「`claude -p` 在已登入的機器上仍回 `Not logged in`，可能是組織政策
`require_trusted_devices` 擋的，若如此全案停擺」。實測證明**不是政策問題，headless 本身
完全能動** —— 但成因換來一個新的阻斷項。

```bash
# 失敗
claude -p "Reply with exactly: pong" --output-format json --model haiku --bare
#   is_error: true, terminal_reason: "api_error", total_cost_usd: 0
#   result: "Not logged in · Please run /login"

# 成功（只差一個 --bare）
claude -p "Reply with exactly: pong" --output-format json --model haiku
#   is_error: false, result: "pong", total_cost_usd: 0.0166103
```

`claude --help`（CLI 2.1.278）對 `--bare` 的說明就寫在那裡：

> skip hooks, LSP, plugin sync, attribution, auto-memory, background prefetches,
> **keychain reads**, and CLAUDE.md auto-discovery. Sets `CLAUDE_CODE_SIMPLE=1`.
> **Anthropic auth is strictly `ANTHROPIC_API_KEY` or `apiKeyHelper` via `--settings`
> (OAuth and keychain are never read).**

這台機器的憑證是 `~/.claude/.credentials.json`（`claude /login` 產生的 OAuth token），
環境裡沒有 `ANTHROPIC_API_KEY`。`--bare` 不讀它，所以直接 `Not logged in`。

**`apiKeyHelper` 已驗，不通。** 把 OAuth access token 從 `.credentials.json` 取出、
透過 `apiKeyHelper` 餵進去，CLI 確實會讀取並使用它，但伺服器回：

```
401 {"type":"authentication_error","message":"API key is invalid."}
```

`apiKeyHelper` 的輸出被當成 `x-api-key`，而 OAuth token 走的是 `Authorization: Bearer`
配合 oauth beta header —— 兩條不同的認證通道，不能互換。
**`apiKeyHelper` 只服務 API key 計費路線**，與訂閱額度無關。

**解法是放棄 `--bare`，隔離改由乾淨 HOME 達成。** 實測：`HOME` 指向一個只含
`.credentials.json` 的目錄執行 `claude -p`，認證通過、job 正常完成
（`result: "pong"`, `total_cost_usd: 0.0172`）。

逐項核對 `--bare` 原本關掉的行為，確認乾淨 HOME 涵蓋了所有**安全相關**的部分：

| `--bare` 關掉的東西 | 來源 | 乾淨 HOME 下 | 安全相關 |
|---|---|---|---|
| hooks | `~/.claude/settings.json` | 不存在 ✅ | 是 |
| plugin sync | `~/.claude/plugins/` | 不存在 ✅ | 是 |
| skills | `~/.claude/skills/` | 不存在 ✅ | 是 |
| MCP | settings / mcp cache | 不存在 ✅ | 是 |
| auto-memory | `~/.claude/` | 不存在 ✅ | 是 |
| keychain / OAuth reads | — | **保留**（正是我們要的） | 是 |
| CLAUDE.md auto-discovery | **工作目錄**，不是 HOME | ⚠️ 仍啟用 | 部分 |
| LSP / background prefetches / attribution | — | 仍啟用 | 否 |

**唯一殘留：CLAUDE.md 從工作目錄讀，不從 HOME。** job 的工作目錄每次新建，裡面只有
借用者自己上傳的檔案，所以最壞情況是借用者的 CLAUDE.md 影響借用者自己的 job，
碰不到出租者的任何東西。判定為可接受。

> 這張表是為了回應一個正確的質疑：`--bare` 是一個旗標包掉一整組行為，
> 換掉它就必須逐項確認替代方案沒有漏。上表即為該核對。

### 跨機器 `--resume` 可行（2026-09-21 實測）

模擬「從別台機器上傳過來」：把一份 transcript 複製到新路徑、改檔名、在一個只含
`.credentials.json` 的乾淨 HOME 下 resume。

```bash
HOME=<乾淨目錄> claude -p "Now reply with exactly: pong2" \
  --resume /任意路徑/uploaded-transcript.jsonl \
  --output-format json --model haiku < /dev/null
#   is_error: false, terminal_reason: "completed", result: "pong2"
```

**§4.2 的「Claude Code 走真 resume」成立**，不需要退回重建上下文。

兩個連帶的設計約束：

**1. `session_id` 會沿用原 transcript 的值。** 上例回傳的 `session_id` 與原始 session
完全相同。**不能用 `session_id` 當 job 主鍵** —— 兩個出租者 resume 同一份上傳檔會撞號，
同一個借用者重複提交同一份也會。job id 必須是 Hub 自己產生的 UUID
（`.claude/rules/security.md` 已有此要求，這是它的具體理由）。

**2. transcript 很大。** 一句 `"Reply with exactly: pong"` 的 session 存下來就 **224 KB**
（系統提示與工具定義佔絕大部分）。真實的 RD session 上看數十 MB。

- §6 的上傳大小限制要按這個量級訂，不能按「一段對話」的直覺
- §8 的 MinIO 容量與 30 天保留期要重算
- 上傳與下載都要考慮這個尺寸下的逾時

### 版本漂移的產生機制（2026-09-21 實測）

風險 #3 原本寫的是「版本不同時 resume 可能失敗」。實測發現漂移**是無聲產生的**：

```
@anthropic-ai/claude-code@2.1.278   engines: { node: '>=22.0.0' }
@anthropic-ai/claude-code@2.1.197   engines: { node: '>=18.0.0' }
```

在 node 20 的機器上跑 `npm install -g @anthropic-ai/claude-code`，npm **不會報錯**，
它會往回裝到最新相容版 2.1.197 —— 比 latest 舊 81 個版本。開發機上因此同時存在
兩個版本（VSCode extension 的 2.1.278 與 npm 的 2.1.197）。

**對 §9 worker 的直接要求：**

1. Dockerfile 必須**同時釘 node 版本與 Claude Code 版本**，不可用 `latest`
2. Worker 啟動時回報自己的 CLI 版本給 Hub，寫進 `jobs.claude_code_version_lender`
3. 提交 `.jsonl` 時記錄借用者版本，兩者不符時在 UI 明確警告
   —— **警告，不擋下**（理由見下）

沒有第 1 點的話，每個出租者的 worker 會依自己 base image 的 node 版本裝到不同的
Claude Code，而且沒有任何人會發現。

**實測：跨版本 resume 雙向都可行（2026-09-21）**

用相隔 81 個版本的兩份 CLI 互相 resume：

| 方向 | 結果 |
|---|---|
| 2.1.197 resume 2.1.278 產生的 transcript | ✅ `terminal_reason: completed` |
| 2.1.278 resume 2.1.197 產生的 transcript | ✅ completed，**且上下文語意連續** |

第二項是強驗證：在舊版 session 裡埋入 codeword `banana`，用新版 resume 後詢問，
正確答出 `banana`。不只是「沒有崩潰」，是上下文真的被接上了。

**因此版本閘門定為「記錄 + 警告」，不擋下。** 擋下會把一條實測可用的路徑無謂關閉。

> ⚠️ 限制：只測過這一組版本，且官方仍聲明 transcript 格式是內部的、會隨版本改變。
> 記錄版本的用途因此是**出事時能診斷**，不是預防性封鎖。若日後真的出現版本相關的
> 失敗，屆時再依實際失敗的版本區間收緊閘門。

### Egress 白名單與 per-job 隔離（2026-09-21 實測）

實作在 `worker/`：`Dockerfile`（job 執行環境）、`egress/`（tinyproxy 白名單）、
`docker-compose.yml`、`run-job.sh`。

**網路：** job 容器只接 `internal: true` 的網路（實測確認完全無對外路由：DNS 解析失敗、
直連回 `EAI_AGAIN`），唯一出口是 tinyproxy，設 `FilterDefaultDeny Yes` 與 `ConnectPort 443`。

| 測試 | 結果 |
|---|---|
| `CONNECT example.com:443` | **403** 擋下 |
| `CONNECT api.anthropic.com:443` | **200** 放行 |
| Claude Code 完整跑一個 job | ✅ `completed`，確認吃 `HTTPS_PROXY` |

**per-job HOME 是必要的，不只是好習慣。** 用一個持久掛載的 HOME 跑完一次 job 後，
容器在裡面留下了 `plugins/`、`skills/`、`projects/`、`sessions/`、`settings` 類檔案。
若 HOME 在 job 之間共用，**借用者 A 可以寫入 `~/.claude/settings.json`（含 hooks）
或塞一個 skill，借用者 B 的 job 執行時就會載入它** —— 這是跨 job 的任意程式碼執行。

實測的正確形狀（`run-job.sh`）：

```
--tmpfs /home/runner            ← 每個 job 全新的 HOME，容器結束即消失
-v <creds>:/home/runner/.claude/.credentials.json:ro
```

驗證：job 內嘗試覆寫憑證 → `Read-only file system`；host 上的憑證檔未變更。

### 誰啟動 job 容器 → 已決：worker 跑在 host 上

`run-job.sh` 需要能執行 `docker run`。目前 `docker-compose.yml` 是把 `/var/run/docker.sock`
掛進 worker，**而掛 docker.sock 等同給該容器 root 權限** —— 一個能操作 docker 的程序
可以掛載 host 的任何路徑。這與「借用者的內容不該碰到出租者機器」的精神相衝。

三條路，**Phase 1 前要選定**：

| 走法 | 代價 |
|---|---|
| worker 直接跑在 host 上（不進容器），自己 `docker run` job | 最簡單，攻擊面也最小（worker 是我們的程式，不是借用者的）。代價是出租者要裝 Python 環境，不能純 compose |
| worker 在容器內 + 掛 docker.sock | 部署最乾淨，但等於把 root 給了 worker 容器 |
| rootless Docker 或 socket proxy（只放行 `container create/start`） | 最安全，設定最麻煩 |

**決定：第一條。** worker 是自己寫的程式、不執行借用者的內容，跑在 host 上的風險遠低於
把 docker.sock 交出去。「出租者要裝 Python」這個代價由安裝腳本吸收。

**連帶影響 §9**：`docker-compose.yml` 只負責 egress proxy，不再包含 worker service，
也不掛 `docker.sock`。worker 改由安裝腳本裝在 host 上。

> 📌 要裝 Python 的是**出租者**（跑 worker 的人），不是借用者。
> 借用者只用網頁，不安裝任何東西。

### 非技術阻斷項

**決定（2026-09-21）：先不正式報備，限少數同事試用。**

原本的建議是先問帳號管理員。經評估後決定先以小規模試用起步，等實際有人在用、
有真實數據之後再視情況處理。以下記錄為什麼這個決定在小規模下是合理的，
以及**什麼情況下必須回頭補問**。

原本要問的問題是：

這不是合規性問題（沒有分享憑證，§1 已說明），是**資源分配政策**問題：這個工具在做的事，
是把公司分配給 A 的額度挪給 B 用。組織政策確實存在（`~/.claude/policy-limits.json` 有
`require_trusted_devices`，代表有 admin 在管），所以這題的答案不是技術決定的。

**觸發回頭補問的條件**（任一成立就該主動去問，不要等被發現）：

- 使用者超出「少數同事」的範圍（例如跨部門、或超過 10 人）
- 有人開始把它當成日常工作流程的固定環節，而非偶發救急
- 單月累積的 API 等價金額達到會被注意的量級
- 組織政策（`policy-limits.json`）出現新的限制項

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
> **#2 已通過（2026-09-21）**：跨機器 resume 可行。依原訂判準，**Phase 4 應併入 Phase 1** ——
> 兩條路共用同一個 job 提交流程，差別只在 worker 端多一個 `--resume` 參數，
> 增量成本遠低於獨立做一個 Phase。
>
> 仍待 #3（版本漂移的實際行為）確認是否需要在提交時加版本閘門。
