# Security Rules

These rules are always active. Violations must be fixed before merging.

**這份規則的優先級高於其他所有規則。** 原因：這個專案同時處理**別人的對話內容**和
**三組不同的憑證**，而且有一部分程式碼跑在別人的個人電腦上。出事的不是服務中斷，
是同事之間的信任。

## 這個專案特有的三條紅線

### 1. 絕不記錄 job 內容

借用者的 prompt、對話、附件、產出，**一律不進 log**。
只記 metadata：job id、狀態、token 數、耗時、model。

違反這條的後果不是資料外洩，是這個工具會被同事集體棄用。

### 2. Claude Code 憑證永遠不進 image，而且只走它那一種通道

出租者的 Anthropic 憑證是這整個系統裡最敏感的東西 —— 它被盜等於帳號被停權。

**憑證有兩種，通道不同，不可互換：**

| 憑證 | 怎麼進 job 容器 | 為什麼 |
|---|---|---|
| `.credentials.json`（`claude /login` 的 OAuth session） | **只以唯讀 volume 掛入** | 它是檔案，Claude Code 會就地讀它 |
| 長期 OAuth token（`claude setup-token`，`sk-ant-oat01-…`，一年期） | **只以環境變數 `CLAUDE_CODE_OAUTH_TOKEN` 注入該 job 的容器** | 那是 Anthropic 官方為非互動場景設計的唯一通道 |

> **2026-09-22 修訂。** 這條原本寫「不寫進環境變數」，而長期 token 官方的用法
> 就是環境變數 —— 規則與唯一可行的機制打架。`apiKeyHelper` 那條路已經驗過不通
> （SPEC.md §11：OAuth token 走 `Authorization: Bearer` + oauth beta header，
> 而 `apiKeyHelper` 的輸出被當成 `x-api-key`，伺服器回 401）。
>
> 這條的**用意**一直是「憑證不要散落到會被意外讀到的地方」，不是「環境變數這個
> 機制有罪」。所以用意用下面的約束保住，機制放行。

**長期 token 的額外約束，每一條都不可省：**

- **絕不進 log。** 包含 worker 的 stdout、hub 的 access log、docker 的事件。
  紅線 1 說 job 內容不進 log，token 比那更嚴格
- **絕不寫進檔案系統**，包含 job 的工作目錄與 `.home/`
- **絕不回傳給前端。** 存進去之後就是單向的 —— UI 只能顯示「已設定 / 未設定」，
  不能顯示遮罩後的值，也不能提供「查看」
- **在資料庫裡加密存放，金鑰放在資料庫外**（`hub/.env`）。這擋不住拿到整台主機
  的人，但擋得住最可能發生的那種外洩：備份外流、SQL injection、有人拿到 psql。
  **金鑰沒設就拒絕啟動**，不要默默用明文存
- **一年期意味著外洩沒有損失上限。** 8 小時的 session token 洩漏還有天然的止血點，
  這個沒有 —— 所以凡是需要人類複製貼上的介面都要假設它終究會被貼到不該貼的地方
  （2026-09-22 實際發生過一次）

- 不 `COPY` 進 image
- 容器的 `HOME` 必須是**每個 job 全新的目錄**（目前是工作目錄底下的 `.home/`）
- HOME 裡**只允許**這三樣，其餘一概不得出現：

  | 允許 | 為什麼 |
  |---|---|
  | 唯讀掛入的 `.credentials.json` | 認證必需 |
  | `.claude/skills/synced/` | 組織層級、由 Anthropic 同步的 skill（pptx、xlsx…）。借用者與出租者同一個 org，不是出租者的個人內容 |
  | `.claude/plugins/synced/` | 同上 |

  **明確禁止**：出租者的 `settings.json`（可含 hooks）、`plugins/cache/`、
  個人 `skills/`、MCP 設定、`projects/`、`history.jsonl`。
  複製時用白名單列舉，不要用「排除法」—— 上游新增一個目錄，排除法就漏了。
- job 容器跑完即銷毀，worker 接著刪掉整個工作目錄（含 `.home/`）

> 🚨 **HOME 絕不可在 job 之間共用。** 2026-09-21 實測：一次 job 跑完後，容器會在
> `~/.claude/` 留下 `plugins/`、`skills/`、`settings` 類檔案。若 HOME 共用，借用者 A
> 能寫入帶 hooks 的 `settings.json` 或塞一個 skill，**借用者 B 的 job 執行時就會載入它**
> —— 跨 job 的任意程式碼執行。
>
> **這條的重點是「每個 job 全新」，不是「用 tmpfs」。** 最初的實作用 tmpfs，
> 後來改成工作目錄底下的 `.home/` —— 因為 tmpfs 隨容器消失，連 transcript 也一起沒了，
> 而「接著問」需要它（SPEC.md §4.2）。隔離性完全相同：仍是每個 job 一個全新目錄、
> 跑完刪除。**要改這裡之前先確認新方案仍然滿足「全新」這個條件。**

> ⚠️ **不要用 `--bare` 來做隔離。** 它不讀 OAuth 與 keychain（只吃 `ANTHROPIC_API_KEY`
> 或 `apiKeyHelper`），會無視掛進去的憑證並回 `Not logged in`。隔離由上面那條乾淨 HOME
> 達成，已逐項驗證 —— 見 SPEC.md §11。
>
> **也不要為了讓指令跑起來而改用 `ANTHROPIC_API_KEY` 塞環境變數。** 那是另一套計費
> （真錢，非訂閱額度），會推翻 §4.6 的整個記帳前提。

### 3. Egress 白名單是安全邊界，不是效能設定

借用者提交的內容會在出租者機器上被 Claude 執行。容器能出網，就等於借用者能把
出租者機器上的東西送出去、或把那台機器當跳板。

- 預設只放行 `api.anthropic.com` 與 MinIO endpoint
- `ALLOW_FULL_NETWORK` 只能由出租者自己開，且 UI 要標示該 worker 的網路模式
- 不要為了「讓 pip install 能跑」而在程式裡默默放寬

## Secrets & Configuration

- 憑證只存在 `.env` —— 絕不寫進程式碼
- `.env` 在 `.gitignore` 裡 —— 絕不 commit
- 透過 `settings.*`（pydantic-settings）或啟動時的 `os.getenv` 取用 ——
  不要在業務邏輯裡散落 `os.environ["KEY"]`
- 絕不 log、print，或把憑證放進錯誤訊息與 API 回應
- 本專案涉及的憑證：Observ 帳密、Anthropic / Claude Code 憑證、MinIO access key

## Input Validation

- 所有 request body 在進業務邏輯前先由 Pydantic 驗證
- 上傳的 `.jsonl` 與附件要有大小上限，在 schema 層擋掉
- 絕不把使用者輸入直接接進 shell 指令、SQL 或檔案路徑
  —— 特別注意 job id 會被用來組 MinIO prefix 與容器工作目錄，**必須是伺服器產生的
  UUID，不能接受使用者指定**

## Storage

- 下載一律走 **presigned URL** 並設短效期 —— 不要開放公開讀取的 bucket policy
- Presigned URL 本身就是憑證，不要寫進 log 或通知訊息的可長期保存處
- 30 天 lifecycle rule 是隱私承諾的一部分，不是省空間的手段

## Authentication

- 在 middleware 或 dependency 驗 token —— 不要在每個 route handler 裡各驗一次
- Job 的存取檢查：只有**借用者本人**與**執行該 job 的出租者**能讀該 job 的內容
- Token 短效期優先，過期就 refresh，不要用長 TTL

## Dependency Security

- 發布前跑 `pip-audit`
- 在 `requirements*.txt` 釘版本，transitive deps 用 lock file
