# 誰在動什麼

這個 repo 同時有多個 Claude session 在寫。這份檔案的用途有三個：

1. **認領**：現在誰在動哪些檔案
2. **交接**：一邊做好、另一邊要接的契約
3. **已定案**：哪些事吵完了、哪些前提變了 —— 不要重新發明，也不要照舊前提寫

**還是不要在這裡抄逐筆的「我改了什麼」** —— `git log` 已經完整記錄了，手抄一份
只會過期，而過期的紀錄比沒有紀錄危險：

```bash
git log --format='%h %s' --name-only -15
```

第 3 項不重複 git log：它說得出「改了什麼」，說不出「**這件事已經吵完，別再動它**」。
所以那兩節只記結論與理由，每條掛 hash，對不上時**以 commit 為準**。

---

## 🚨 最重要的一條規則：不要用 `git add -A`

從 repo 根目錄 `git add -A` 會把**另一個 session 還沒提交的修改一起掃進你的
commit**。這不是假設 —— 2026-09-21 實際發生過：`6d978c6` 表面上是「出租者可以
中止 job」，實際卻夾帶了 UI 改版的 `App.tsx`、`index.css`、`MyWorker.tsx`、
`Notifications.tsx`。東西沒丟，但 commit 訊息與內容不符，歷史被弄糊。

**`git add <路徑>` 也不夠。** index 是共用的：另一個 session 只要先 `git add`
過，他的檔案就已經躺在 index 裡，你再 `git add` 自己的兩個檔案，commit 時會把
他的一起帶走。2026-09-22 實際發生：一個只該有 `SPEC.md` 與 `docs/web-spec.md`
的 commit，夾帶了另一個 session 的 `CONTEXT.md`、`hub/app/config.py`、
`hub/app/routers/auth.py`、`web/src/api.ts`、`Notifications.tsx`。

**用 commit 的 pathspec，它完全不看 index：**

```bash
git commit -F msg.txt -- web/src/pages/Submit.tsx web/src/index.css   # ✅
git add web/ && git commit                                            # ⚠️ 會帶走別人已 stage 的
git add -A                                                            # ❌ 永遠不要
```

`git commit -- <路徑>` 只從工作區取那幾個路徑，別人 stage 了什麼都不影響。
真的要先 `git add` 的話，**`git commit` 前一定要看 `git diff --cached --name-only`**，
出現不是你的檔案就停下來。

**`0e2f900` 現在就卡在這個狀態**：它的訊息只講 spike #7，實際還夾帶了另一個
session 的通知頁與 hub 設定。東西沒丟、能跑，但 `git log` 讀不出通知頁那些改動
是為什麼做的。要不要拆成兩筆**還沒決定** —— 拆要改寫歷史，所以得等兩邊都停手、
由使用者決定。在那之前不要有人去動它。

---

## 目前認領

| 區域 | 誰 | 狀態 |
|---|---|---|
| `web/src/pages/Submit.tsx`、`CommandPicker.tsx`、`App.tsx`、`index.css` 的色票與元件 | session A | 2026-09-22 告一段落（見下方兩節）|
| `web/src/pages/Login.tsx`、`Notifications.tsx`、`api.ts` | session B | 2026-09-22 告一段落 |
| `web/src/index.css` 的 `.hint` | session B | 同上（見下方契約）|
| `hub/app/config.py`、`hub/app/routers/auth.py` | session B | 同上 |
| `CONTEXT.md` | session B 起的頭 | 之後共用 |
| `docs/web-spec.md` | 共用 | 小心：兩邊都會改，改前先看一次現況 |
| `SPEC.md`、`CLAUDE.md` | 共用 | 同上 |

⚠️ **原本的切法（`web/src/**` 全歸 A、`hub/**` 全歸 B）已經不成立**：B 這輪
從通知頁一路改到 `/me`，兩邊都踩進 `web/src/`。按目錄認領擋不住，所以改成按檔案。
`index.css` 是現在唯一兩邊都碰的檔案，動它之前先看這張表。

改完一個區域就把狀態改掉。

---

## 已經改掉的共用前提（動之前先看這裡）

不是改動紀錄 —— 是**會讓你寫錯的前提變更**。

- **`.hint` 不再有負 margin**（2026-09-22）。它以前預設 `margin-top: -8px`，
  前提是前面接的是 `<label>`；十處用例只有兩處成立，接 `div.actions` 那處直接
  疊在按鈕上。現在預設安全，**要緊貼欄位得自己掛 `class="hint under-field"`**。
- **`/me` 多了兩個欄位**：`channel_notifications`（共用頻道的 webhook 在 hub 上
  有沒有設）與 `channel_name`。`web/src/api.ts` 的 `Me` 已同步。hub 對應新的
  `TEAMS_CHANNEL_NAME`，`.env.example` 已更新 —— **`.env` 要自己補，不補的話
  通知頁會顯示「共用頻道」四個字而不是真正的頻道名**。
- **通知的詞彙定在 [`../CONTEXT.md`](../CONTEXT.md)**：目的地有三種 ——
  私訊、頻道、**沒有**。**不要再寫「開啟通知」「關閉通知」**，那是假的：
  沒設個人 webhook 的人一樣會被通知，只是通知在頻道裡。
- **`index.css` 的色票整組換過，而且多了幾個 token**（`dd580b8`）。
  新增 `--accent-hover`、`--accent-soft`、`--on-accent`、`--ok/bad-soft`、
  `--ok/bad-ink`。**不要再寫死 `#fff` 當按鈕文字**（深色模式的 accent 是淺橘，
  白字只有 2.7:1）。按鈕已有三階：預設實心、`.secondary` 描邊、`.small` 小藥丸
  —— **要次要按鈕請用這些，不要自己刻**。`.chip` 也已按狀態分三組底色。
- **`CommandPicker` 的介面換了**（`bda7b8d`）：從 `onPick(name)` 改成
  `value` / `onChange`。它現在自己擁有「選一個指令」的語意（讀得出目前選哪個、
  換掉、再點一次取消）。**不要再從外面自己把指令併進文字**。

---

## 已定案的取捨（不要重新發明）

只記結論與理由，細節看 hash，對不上時以 commit 為準。列在這裡的標準是
「重新想一次會浪費半小時以上」。

### session A

| 決定 | 為什麼 | commit |
|---|---|---|
| **奶茶色是底，不是強調色** | 中性色全部帶暖，飽和度只留給 `--ok`/`--warn`/`--bad`。整個介面都染色的話，web-spec §9 那三個「必須正經的地方」就沒地方跳出來 | `dd580b8` |
| **指令是「選一個」，不是一直插入** | worker 把整個文字框當一個 prompt 交給 `claude -p`，而 Claude Code 只認開頭那一個 slash command，後面的會變成它的參數。疊四個 ≠ 做四件事 | `bda7b8d` |
| **上傳 `.jsonl` 不用動 worker** | resume 管線本來就在（`resume_from_url` → `.home/resume.jsonl` → `--resume`），「接著問」用的就是它。上傳 = 把新 job 的 `transcript_key` 指到使用者上傳的物件 | `bca3312` |
| **`transcript_key` 一定要驗 prefix 屬於呼叫者** | 不驗的話任何人都能指到 `jobs/<別人的 job>/transcript.jsonl`，讓 worker 把別人的對話 resume 出來。「不是你的」與「不存在」回同一個 404，分開講等於給人探測 job id 的工具 | `bca3312` |
| **不做 BD/PM 專屬的上傳入口** | 分界是工具不是職稱；沒有 transcript 的對話就算合成一份，裝的資訊也跟貼上一樣 | `8743097` |

### ⚠️ 「該上傳還是該貼上」這段文案，動之前先讀

**同一段話錯了三次**，每次都是拿表象當分界：

1. 照**職稱**（RD 上傳 / BD/PM 貼上）→ 用過 Claude Code 的 PM 也有檔案（`b670b53`）
2. 照 **app 名稱**（Claude app / Claude Code app）→ Claude Desktop 一個 app 裡就有
   Chat / Cowork / Code 三個分頁（`e33b094`）
3. 照**「對話有沒有碰你的檔案」**→ Cowork 會讀寫本機檔案，但跑在雲端（`cf4e737`）

共同原因：**都要求使用者（和寫文案的人）懂產品架構，而產品架構會變。**

現在的作法是讓指令自己當測試 —— `ls -t ~/.claude/projects/*/*.jsonl | head -5`，
有東西就上傳、沒有就貼上（`8b1582e`）。成立的前提是 SPEC §11 spike #7 實測出
**Cowork 的 local session 也寫在同一棵樹**。

**要改這段之前，先確認新寫法不需要使用者判斷自己屬於哪一類。**

附帶：不要教人「全選複製」整段對話 —— 貼最後幾輪就夠。貼越多讀越久越貴，
而那筆錢要算進人情債（`483e73c`）。

### session B

（B 自己填）

---

## 待接的契約（後端做好了，UI 還沒接）

這些後端都可用、有測試，UI 改版時照著接即可。詳細欄位見
[`../docs/web-spec.md`](../docs/web-spec.md)。

> ✅ 1 與 2 的前端已接（`web/src/JobOutcome.tsx`，2026-09-21）。
> 那個元件自帶 CSS，沒有動 `index.css` —— UI 改版落地後可以把樣式併進設計系統。

### 1. 失敗分類 — `docs/web-spec.md` §5

`GET /api/jobs/{id}` 回 `failure`：

```json
{ "kind": "fixable|system|claude", "title": "...", "hint": "...",
  "show_detail": true, "blocked_by_network": false }
```

UI 只要三件事：顯示 `title`、顯示 `hint`（若有）、**只在 `show_detail` 為真時**
才顯示 `error_detail`。系統問題的 stderr 對使用者沒有意義。

### 2. 中止 job — `docs/web-spec.md` §8

`POST /api/jobs/{id}/stop`，body `{ "note": "..." }`（選填）。
詳情的 `can_stop` 決定按鈕要不要出現。

**那個 note 欄位是重點，不是附加功能。** 沒有它，停止會被讀成拒絕：

> ❌ 你的 job 已被出租者終止。
> ✅ Vivian 終止了你的 job：「這個看起來會跑很久，我等下要開會，晚點再幫你跑」

### 3. Job 列表的欄位

`GET /api/jobs` 每筆帶 `preview`（prompt 第一行）與 `is_follow_up`。
沒有 preview 的話列表就是一排 UUID，認不出哪個是哪個。

---

## 已知還沒做（不要照 spec 的線框直接做出來）

- **附件上傳**（`web-spec` §3 線框裡的「附件（選填）」）：還沒有。
  `.jsonl` 的 session 檔已經可以上傳了（session A，2026-09-21），但那是
  transcript，走 `claude --resume`；一般附件要進 job 的工作目錄，是另一條路。
- **〔改送給開放網路的 worker〕按鈕**：後端的 `blocked_by_network` 已備好，
  但只有一台 worker 時按了沒地方送。等有第二台。
- **排行榜**：整個沒有。等有第二個使用者才有意義 —— 現在只有一個人，
  而人不能欠自己。
