# claude-boba

同事之間的 Claude 代跑互助平台。完整設計與所有決策理由在 **[SPEC.md](SPEC.md)**。

## 先讀 SPEC.md

這個 repo 的設計是經過一次完整訪談定案的，SPEC.md 記錄的不只是結論，還有**為什麼**
以及**被否決的方案**。動任何設計之前先讀它，不要重新發明已經被討論掉的做法。

特別注意這幾條，它們看起來像可以「順手改進」但其實是刻意的：

| 看起來像 | 其實是 |
|---|---|
| 「租借帳號」 | **代跑**。但 2026-09-22 這條的內容變了 —— 見下方那格，不要照舊的理解動它 |
| 一個人一個帳號 | **一個人可以有多個出借帳號**（個人 + 公司），而帳本不分來源。見 [ADR-0001](docs/adr/0001-multiple-lending-accounts-one-ledger.md) |
| 級距表很粗糙 | 刻意的。精確數字會讓人計較，粗級距才會讓人笑著說「好啦我請」 |
| 失敗/取消不計債好像不公平 | 刻意的。一條簡單的規則比一條公平的規則值錢 |
| 隱私宣告寫得很直白 | 刻意的。宣稱「看不到」是虛假安全感。**但看得到的人換了** —— 見下方那格 |
| CI 比 `lighthouse-saas-api` 那套少一大截 | 刻意的，五處都不是漏抄。理由寫在 [.github/workflows/ci.yml](.github/workflows/ci.yml) 開頭 —— 補回來之前先讀那段 |
| 許願板少了「認領／處理中」 | 刻意的。它只記錄已實現的願望，不記錄承諾 —— 見 [web-spec §12](docs/web-spec.md) |
| 許願板很像 §7 否決掉的動態牆 | 不是同一件事。差別在**內容是誰決定要公開的**，見 web-spec §10 那條 |
| 有介紹頁，違反 web-spec §10「獨立的新手說明頁」 | 不是。§10 那條沒被推翻，它當初只看了需求側 —— 招募**代跑者**需要一個講得完整的地方，而那不適合塞進「我很急」的提交頁。介紹頁不擋路、不是登入後的落點，**提交頁的文案一句都沒搬過去**（搬了才是真的推翻）。見 [web-spec §13](docs/web-spec.md) 與 §10 的兩則註記 |
| 登入頁有「這是什麼？」連結，違反 §10「登入頁放裝飾性內容」 | 不是。那條反對的是跟登入無關、只為了有趣的東西（星座、吉祥物）；這個連結回答的是「我為什麼要登入」。位置是約束：貼著最上面的 lede，**不准**搬到底部那句「密碼只會轉交給 Observ」旁邊 |
| 白名單有 Fable，違反 §9「站台不含 Fable」 | 不是。§9 原本就允許出租者自己開，只是那扇門在 `.env`；共用主機之後 `.env` 沒了，門搬到 `SITE_MODELS`，**預設仍然沒人開**。見 §9 的 2026-09-23 註記 |

### ⚠️ 合規論述在 2026-09-22 改過

這一段原本寫「憑證不離開出租者機器，這是合規性的根本，**不能為了方便而改**」。
**它被改了**，而且是知情的取捨，不是有人沒讀到規則。

代跑者現在交出一組一年期的 `CLAUDE_CODE_OAUTH_TOKEN`，存在共用主機上，job 都在
那台跑。換來沒有人需要安裝任何東西。放棄的是「憑證不離開」—— 這條路在合規上
**比舊的弱**，而且風險更不對稱（被停權的還是代跑者，但他少了執行現場與短效期
這兩樣控制）。

- 完整取捨：[SPEC.md §1](SPEC.md)
- 機制與五條不可省的約束：[security.md 紅線 2](.claude/rules/security.md)
- 可行性怎麼驗的：SPEC.md §11 spike #8

**還沒做的**：

1. UI 上代跑者交出 token 那一步的揭露文案。他要知道自己交出的是什麼、有效多久、
   誰拿得到 —— 不要寫成「連結你的帳號」那種把風險藏起來的說法。
2. **隱私勾選的對象變了，文案還沒改。** 提交頁現在寫「我了解 &lt;代跑者&gt; 技術上
   可以看到我送出的內容」，而且會點名。新模型下 job 不在代跑者的機器上跑 ——
   **他反而看不到了**，看得到的是主機管理者。點名一個看不到的人、卻不提真正
   看得到的人，比不揭露更糟。web-spec §9 要一起改。

## 現況

**Phase 0 與 Phase 1 完成，Phase 2 進行中。**

- Phase 0：六項 spike 全數通過（SPEC.md §11），其中四項推翻了原本的設計
- Phase 1：提交 job → worker 真的執行 → 事件即時串回畫面，端到端跑通
- Phase 2：Observ 認證與人情債帳本已實作，尚未用真實帳號完整驗過

三個元件：`hub/`（FastAPI）、`worker/`（**站台的領單主機**，一台服務所有出借帳號）、
`web/`（React）。
怎麼跑見 [README.md](README.md)。

尚未實作：排行榜。

（**附件上傳已完成**，2026-09-22。`.jsonl` 的 session 檔走 `claude --resume`；
其他檔案平鋪在工作目錄根層。帶 codebase 進來的路徑是「壓成一個含 `.git` 的 zip」——
見 [web-spec §3](docs/web-spec.md) 與 `hub/app/data/commands.json` 工程組的 hint。）

**許願板已實作，但它是試玩期的鷹架，不是長期功能。** 它有寫死的下架條件（Phase 2 驗完或連續 30 天
無新內容，先到者為準），拆的時候要連 `emoji-picker-react` 這個相依一起檢查
（[ADR-0004](docs/adr/0004-emoji-picker-first-ui-dependency.md)）。它的貼圖功能
**知情地弱化了 SPEC §4.3**（[ADR-0005](docs/adr/0005-wish-board-public-image-upload.md)）。

### 動手前要知道的幾個地雷

這些都是實測踩出來的，寫在 SPEC.md §11 與 `hub/README.md`：

- **不要給 worker 的 `claude` 加 `--bare`** —— 它不讀 OAuth，會回 `Not logged in`，
  而錯誤訊息會把人導向「去登入」這條錯的路
- **job 的 HOME 必須是每個 job 全新的目錄** —— 共用會造成跨 job 的任意程式碼執行。
  重點是「全新」不是「tmpfs」。HOME 裡允許什麼是白名單，見 security.md 紅線 2
- **org skill（pptx/xlsx/docx/pdf）需要暖機** —— 它們是背景同步的，而每個 job 都是
  全新 HOME、永遠是「第一次執行」，所以來不及。worker 啟動時暖一個 template 再複製。
  同步只在「工作目錄是掛載進來的專案目錄」時才觸發（`-w /tmp` 不會）。
  **2026-09-23 起這條只影響舊憑證檔模型**：清單上的 pptx/xlsx/docx/pdf 已改成
  `worker/job-claude/skills/` 裡自己寫的版本，不靠暖機；託管模型的 token 本來就讀不到
  org 同步的 skill（`worker/job-claude/README.md`）
- **SQLAlchemy 的 enum 欄位要用 `_enum()`**，`String` 配 `Mapped[SomeEnum]` 不會轉型
- **`NULL IN (...)` 在 SQL 裡永遠不為真** —— 自動派單的 job 會一筆都領不到且不報錯
- **Observ 的身分端點用 `x-request-service-id`**，不是 `X-Service-Id`；送錯只會回 401
- **worker 的身分是「這台主機」，不是某位代跑者**（2026-09-22，SPEC §4.12）。
  token 來自 hub 的 `WORKER_SHARED_TOKEN`，不再由網頁產生。舊協定下 hub 沒有
  「挑帳號」這個動作 —— 帳號是自己跑來搶單的，而那讓多帳號做不出來
- **出借條件不要讓 worker/.env 回報**。上限／model／外網屬於代跑者，不屬於主機；
  主機只有一台、代跑者有很多位，讓 .env 決定等於某個人的設定被另一個人蓋掉
- **MinIO 的 30 天 lifecycle rule 只能掛在 `jobs/` prefix 上**，不能掛整個 bucket ——
  許願板的圖在 `wishes/`，活到牆被拆掉為止。掛整個 bucket 的話圖會在第 31 天消失
  而願望還在，牆上一排破圖且沒人知道為什麼（SPEC §8）
- **改 schema 要跑 Alembic**，不要再用 `create_all` 或手動 `ALTER TABLE`。
  Hub 啟動時會檢查版本，落後就拒絕啟動
- **新增背景迴圈時，`hub/tests/test_sweeper_is_wired.py` 要多守一條。** 這個 repo 已經
  三次寫好了清潔工卻沒人啟動它（authorize、expiry、orphans）。`sweep_once()` 有測試
  不代表 `main.py` 有 create_task
- **長連線的 endpoint（SSE、long-poll）不要讓 `Depends(get_session)` 的 session 活到
  回應結束。** 它會一直 idle in transaction、握著表的鎖；一支 `ALTER TABLE` 排到它
  後面，全站每一個查詢都會跟著卡住（2026-09-23 實際發生，13 分鐘）。資料讀完就
  `commit()` + `close()`，串流階段只靠記憶體
- **hub 對已派出的 job 有自己的判斷了**（2026-09-23，`hub/app/orphans.py`）：worker
  超過 `job_heartbeat_timeout_seconds` 沒回報就結案為 failed。不要再假設「worker 一定
  會回報 result」—— 它中途崩潰時就不會，而那個 job 之前會永遠「執行中」
- **不要加「這個帳號跑不跑得動某個 model」的預先探測。** 2026-09-23 逐一量過，
  API 問不出來：`GET /v1/models`、`count_tokens`、`POST /v1/messages` 對有 Fable
  與沒有 Fable 的帳號回的東西**逐字相同**，而且有 Fable 的帳號在額度滿時同樣回
  429 —— 把 429 讀成「沒權限」會誤傷最該用這個站台的人。唯一的訊號是 CLI 的
  `api_error_code: credits_required`，而那要真的跑一趟（跑得動的那趟 US$0.22）。
  做法是失敗一次就記在 `LendingAccount.credits_required_models`。見 SPEC §9 spike #11

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
