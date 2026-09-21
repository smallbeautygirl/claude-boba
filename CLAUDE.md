# claude-boba

同事之間的 Claude 代跑互助平台。完整設計與所有決策理由在 **[SPEC.md](SPEC.md)**。

## 先讀 SPEC.md

這個 repo 的設計是經過一次完整訪談定案的，SPEC.md 記錄的不只是結論，還有**為什麼**
以及**被否決的方案**。動任何設計之前先讀它，不要重新發明已經被討論掉的做法。

特別注意這幾條，它們看起來像可以「順手改進」但其實是刻意的：

| 看起來像 | 其實是 |
|---|---|
| 「租借帳號」 | **代跑**。憑證不離開出租者機器，這是合規性的根本，不能為了方便而改 |
| 級距表很粗糙 | 刻意的。精確數字會讓人計較，粗級距才會讓人笑著說「好啦我請」 |
| 失敗/取消不計債好像不公平 | 刻意的。一條簡單的規則比一條公平的規則值錢 |
| 隱私宣告寫得很直白 | 刻意的。內容跑在對方機器上，宣稱「看不到」是虛假安全感 |

## 現況

**Phase 0 與 Phase 1 完成，Phase 2 進行中。**

- Phase 0：六項 spike 全數通過（SPEC.md §11），其中四項推翻了原本的設計
- Phase 1：提交 job → worker 真的執行 → 事件即時串回畫面，端到端跑通
- Phase 2：Observ 認證與人情債帳本已實作，尚未用真實帳號完整驗過

三個元件：`hub/`（FastAPI）、`worker/`（出租者端，跑在 host 上）、`web/`（React）。
怎麼跑見 [README.md](README.md)。

尚未實作：排行榜、Teams 通知、借用者上傳檔案與 `.jsonl`。

### 動手前要知道的幾個地雷

這些都是實測踩出來的，寫在 SPEC.md §11 與 `hub/README.md`：

- **不要給 worker 的 `claude` 加 `--bare`** —— 它不讀 OAuth，會回 `Not logged in`，
  而錯誤訊息會把人導向「去登入」這條錯的路
- **job 的 HOME 必須是每個 job 全新的目錄** —— 共用會造成跨 job 的任意程式碼執行。
  重點是「全新」不是「tmpfs」。HOME 裡允許什麼是白名單，見 security.md 紅線 2
- **org skill（pptx/xlsx/docx/pdf）需要暖機** —— 它們是背景同步的，而每個 job 都是
  全新 HOME、永遠是「第一次執行」，所以來不及。worker 啟動時暖一個 template 再複製。
  同步只在「工作目錄是掛載進來的專案目錄」時才觸發（`-w /tmp` 不會）
- **SQLAlchemy 的 enum 欄位要用 `_enum()`**，`String` 配 `Mapped[SomeEnum]` 不會轉型
- **`NULL IN (...)` 在 SQL 裡永遠不為真** —— 自動派單的 job 會一筆都領不到且不報錯
- **Observ 的身分端點用 `x-request-service-id`**，不是 `X-Service-Id`；送錯只會回 401

## 規則

程式碼規則在 `.claude/rules/`：

- [code-style.md](.claude/rules/code-style.md) — Python 風格，由 ruff 強制
- [git-commits.md](.claude/rules/git-commits.md) — Conventional Commits
- [security.md](.claude/rules/security.md) — 這個專案處理他人的對話內容與多組憑證，優先級最高

這份規則集是**刻意精簡**的。`lighthouse-saas-api` 有另外六份規則（logging、database、
testing、error-handling、clean-code、docs-lifecycle），這裡沒有複製過來 —— 因為對應的
程式碼還不存在，替不存在的 code 寫規則只會產生錯的規則。**當某個領域的第一份 code
出現時，再從那邊抄對應的規則過來。**

## 工具

```bash
.venv/bin/ruff check .
.venv/bin/ruff format .
.venv/bin/pre-commit run --all-files
```

`ruff` 釘 0.16.1。不要升版而不同時更新 `.pre-commit-config.yaml`。
