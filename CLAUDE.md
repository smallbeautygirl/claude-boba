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

**尚未實作。** 目前只有規格與工具設定。

下一步是 SPEC.md §11 的 Phase 0 spike。**在 spike 完成前不要開始寫功能程式碼。**

§7（計價）的兩項假設已在 2026-09-21 驗完並收斂 —— CLI 的 JSON 有分項 token，
`total_cost_usd` 在 Team 訂閱下回真實金額，自維護價格表可以砍掉。

還沒答的是 §4.2（上下文交付）與新冒出來的 **spike #0b：`--bare` 與 OAuth 憑證互斥**。
#0b 決定 worker 怎麼注入憑證，三條候選路的 Dockerfile 與 volume 配置都不同，
**在它拍板前不要寫 worker 的執行程式碼**。

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
