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

### 2. Claude Code 憑證只以唯讀 volume 掛入，永遠不進 image

出租者的 Anthropic 憑證是這整個系統裡最敏感的東西 —— 它被盜等於帳號被停權。

- 唯讀掛載，不 `COPY` 進 image，不寫進環境變數
- 容器的 `HOME` 必須是乾淨的：`$HOME/.claude/` 裡**只有**唯讀掛入的 `.credentials.json`，
  不得出現 `settings.json`、`plugins/`、`skills/` 或 MCP 設定
- job 容器跑完即銷毀

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
