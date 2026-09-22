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
| `web/src/pages/Submit.tsx`、`CommandPicker.tsx`、`App.tsx`、`index.css` 的色票與元件 | 拿著 Submit.tsx 的 session | 🟡 告一段落（`0e0efc9`）|
| `web/src/pages/JobDetail.tsx`、`hub/app/schemas.py` 的 `started_at` | 同上 | 🟡 告一段落（`ca03148`）|
| `web/src/pages/Login.tsx`、`Notifications.tsx`、`api.ts` | session B | 2026-09-22 告一段落 |
| `web/src/index.css` 的 `.hint` | session B | 同上（見下方契約）|
| `hub/app/config.py`、`hub/app/routers/auth.py` | session B | 同上 |
| `CONTEXT.md` | session B 起的頭 | 之後共用 |
| **⚠️ 身分** | **有兩個 session 自稱 B** | 見下 |
| `docs/web-spec.md` | 共用 | 小心：兩邊都會改，改前先看一次現況 |
| `SPEC.md`、`CLAUDE.md` | 共用 | 同上 |

⚠️ **「session B」目前指涉兩個不同的 session**（2026-09-22）。A 把附件上傳派給 B，
接下派工的那個在讀規格，同一時間另一個也自認是 B 的把整套實作寫完並 commit 成
`00a8d1d`。沒有東西被覆蓋，但兩邊各做了一次同樣的準備工作。**署名「session B」
已經不足以辨識是誰**；派工與認領請改用「正在動哪些檔案」來指認，並在動手前先跑
`git status` 看那些檔案是不是已經有人在寫。

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

## 📋 待派工

### ~~→ session B：附件上傳的 hub + worker~~ ✅ 已完成

後端 `00a8d1d`、契約在〈待接的契約〉§3、UI `087c15a`。**這一格可以刪了。**

留一句教訓：這次派工同時被兩個自稱 session B 的人收到，一個在讀規格、另一個
把整套寫完。沒有東西被覆蓋，但同樣的準備工作做了兩次。**派工前先跑
`git status` 看那些檔案是不是已經有人在寫** —— 署名不足以辨識是誰。

---

### ~~→ session 檔改用資料夾選取器~~ ✅ 已完成（`0e0efc9`）

使用者提了三件事，三件都跟使用者一起 grill 過並定案了。**這裡只寫定案與理由，
不寫怎麼刻**。動手前先 `git status` 確認 `Submit.tsx` / `index.css` 沒人在寫。

#### 1. 指令框破版（bug，先修這個）

`.findfile` 有 `flex-basis: 100%`，它是 `.session` 這個 flex 容器的項目。Flex 項目
預設 `min-width: auto`，**不會縮到比內容窄** —— 所以那行很長的 `grep` 把項目撐爆、
連帶衝出卡片。裡面的 `overflow-x: auto` 管的是 `<pre>` 自己，攔不住外層被撐開。
上面那行 `ls -t` 沒事，因為它在 `.sources`（普通 div，不是 flex）。

**定案：`min-width: 0` 與換行兩個都要。** 只加 `min-width: 0` 的話那行指令會變成
一個要橫向捲的框，內容還是看不全；而這是一行**要被複製貼上**的指令，看不全的
東西沒人敢貼。換行照 `.consent pre` 的先例（`white-space: pre-wrap; word-break: break-all`），
那裡已經為「長字串要全部看到」立過同樣的規矩。

#### 2. 終端機那段改成資料夾選取器

BD 不知道終端機是什麼。**定案：用 `<input webkitdirectory>` 當主線** —— 使用者挑
`~/.claude/projects`，瀏覽器自己把 `.jsonl` 列出來給他選。

> 我原本建議的是相反的做法（承認這段是 RD 專屬、收起來讓 BD 快速跳過），
> 理由是 BD 本來就沒有 `.jsonl`。使用者選了選取器這條路。兩條路都合理，
> 差別在要不要為「有 session 檔但不會用終端機」的人鋪路。

**選完之後要解掉「認不出哪個是哪個」** —— 那是原本文案自己承認解不掉的問題
（檔名是 session id）。選了資料夾之後瀏覽器手上有全部檔案，所以現在有解：

- 按 `lastModified` 排序，顯示日期與大小
- **讀開頭抽出第一則使用者訊息當預覽**，列成
  「9/21 14:30 · 『幫我把這份 Q3 報表整理成簡報』· 3.2 MB」
- 成本比看起來低：`Blob.slice` 只讀開頭幾十 KB，而且只預覽時間最近的十個就夠

**隱私那句要寫在選取器旁邊，而且要正經**（web-spec §9）：瀏覽器確實讀了他其他
對話的內容，即使一個 byte 都沒上傳。建議措辭：「這些檔案只在你的瀏覽器裡開啟，
只有你選中的那一個會被上傳。」

**退路兩層都留**：`~/.claude` 在 Finder 裡是隱藏的，選取器對話框要按
`Cmd+Shift+.` 才看得到 —— 這對 BD 不見得比終端機好懂。所以：選取器最上面，
底下一行小字給鍵盤提示，再底下收一個「或者用終端機」。**指令不要全砍** ——
選取器在某個瀏覽器或 OS 上失敗時，使用者不能完全沒有路。

#### 3. 附件與 session 檔不該長得像同類

它們現在是兩顆並排的膠囊按鈕，看起來像同一類的兩個選項。但
[`../CONTEXT.md`](../CONTEXT.md) 現在把它們定成不同的東西：**附件是 job 的標的**，
**session 檔是對話的延續**。傳錯不會報錯，只會得到一個怪結果。

**定案：版面要照這個區分排，不要並排成兩顆一樣的按鈕。** 怎麼排是你的判斷 ——
你上輪已經把附件排到 `.jsonl` 前面（對 Cowork/BD 來說附件才是主要動作），
這一步是把「它們不是同類」也做進版面。

#### ❌ 不做：借用者可選的權限模式

使用者原本問能不能照 Claude Code 的 manual / edit / auto 做一個選單。
**已否決，理由寫進 web-spec §10。** 一句話版本：那個控制項在 Claude Code 裡成立
是靠「動的是你自己的檔案、你人在現場、它會問你」三個前提，這裡三個都不成立。

順帶一個你寫文案會用到的事實：現在固定 `--allowedTools "Read,Edit,Bash"` +
`acceptEdits` + `--permission-prompts none`，它比 Claude Code 的 Auto **更緊** ——
工具只有三個，不在清單上的（`Write`、`WebFetch`）直接沒有，不是跳出來問。

---

### ~~→ 計時器從 `started_at` 起算~~ ✅ 已完成（`ca03148`，拿著 Submit.tsx 的那個 session）

**這是 bug，而且是會對使用者說謊的那種。** `JobDetail` 的計時器從 `created_at`
起算（`web/src/pages/JobDetail.tsx`，`startRef.current = new Date(j.created_at).getTime()`），
但標籤寫的是「⏱ 已執行」。排隊 10 分鐘、實跑 30 秒的 job，畫面會說
**「已執行 10 分 30 秒」**。而債務是照實際花費算的，所以使用者看到的時間與他
欠的錢對不起來。

**跨 hub 與 web 兩邊**，動手前先 `git status`：`hub/app/schemas.py` 與
`web/src/pages/JobDetail.tsx` 目前都沒人認領，但 web/ 那邊有人在動別的檔案。

#### hub：`started_at` 沒有出去

`Job.started_at` 早就存在（`models.py`），在**第一個事件抵達時**設定
（`routers/worker.py`，`CLAIMED` → `RUNNING`）。但 `JobSummary` 只放了
`created_at` 與 `finished_at`，`started_at` 從來沒進過 API。加進去即可，
不需要 migration。

#### web：一個標籤講三件事

現在不論哪個階段都只有「已執行」。實際上有三段，而且使用者關心的不一樣：

| 狀態 | 現在顯示 | 應該顯示 |
|---|---|---|
| `queued` | 已執行 N（其實還沒開始） | **排隊中 N** —— 還沒有 worker 接手 |
| `claimed` | 已執行 N（其實在開容器） | **準備中** —— 有人接了，容器正在啟動 |
| `running` | 已執行 N（含排隊時間） | **已執行 N**，從 `started_at` 算 |
| 終態 | 不顯示 | **總共執行 N**（`finished_at − started_at`）|

排隊那段不要藏起來 —— 等了十分鐘還沒人接，使用者有權知道是「沒人接」而不是
「跑很久」，那是兩種完全不同的處境（前者該去敲人，後者該去泡茶）。

#### ⚠️ 一段目前沒人算的空窗

`claimed` → 第一個事件之間，容器在 `docker run`、在複製 org skill 模板。
那段時間出租者的機器確實在為這個 job 工作，但 `started_at` 還沒設。
**這就是為什麼 `claimed` 那格建議只寫「準備中」不帶秒數** —— 與其算一個
語意不明的數字，不如說清楚它在做什麼。要改成把那段也算進去的話，那是
另一個決定（得在 claim 時多存一個欄位），不在這次範圍。

#### 留給實作者的一個選擇

現在的算法是 `Date.now() − 伺服器給的時間戳`，**使用者的電腦時鐘偏掉就會算錯**
（這個問題現在就存在，不是這次引入的）。兩條路：

- **照舊**：換掉起點就好，時鐘偏移當已知限制。改動最小。
- **一起修掉**：API 回應多帶一個伺服器當下時間，瀏覽器載入時算一次偏移量。
  多一個欄位，但這個問題從此消失。

我的建議是**照舊**：這次的 bug 是「起點錯了」，時鐘偏移是另一個 bug，
綁在一起改會讓 review 看不出哪個修法對應哪個問題。要修就另外開一筆。

---

### → 誰有空都可以：計時器的時鐘偏移（從 `ca03148` 拆出來）

計時器現在算的是 `Date.now() − 伺服器給的時間戳`，**使用者的電腦時鐘偏掉就會
算錯**。這個問題一直都在，不是 `ca03148` 引入的 —— 那筆刻意只修「起點錯了」，
沒有順手一起改，因為綁在一起 review 看不出哪個修法對應哪個問題。

修法：API 回應多帶一個伺服器當下時間，瀏覽器載入時算一次偏移量，之後都用
`Date.now() − offset`。一個欄位的事。

**優先度低。** 同事的電腦時鐘偏到會讓人看錯分鐘數的機率不高，而看錯的後果只是
時間顯示不準，不影響計費（債務照 CLI 回的 `total_cost_usd` 算，跟這個計時器
無關）。

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

### ~~3. 附件上傳~~ — 2026-09-22 由 session B 交付，**UI 已接**（`087c15a`）

**取票 →** `POST /api/uploads/attachment`，body `{ "filename": "報表.xlsx" }`
回 `{ key, put_url, max_bytes }`。一次一個檔，要傳多個就呼叫多次。
檔名由 Hub 淨化（去路徑、去 `..`）後放進 key —— 那個 key 最後會變成容器裡的
檔案路徑，讓使用者控制那段等於讓他寫到工作目錄外。

**送件 →** 瀏覽器直接 PUT 進 `put_url`（不經過 Hub，跟 `.jsonl` 同一條路）。

**建立 job →** `POST /api/jobs` 的 `attachment_keys: string[]`（最多 20 個）。

| 限制 | 值 | 錯誤碼 |
|---|---|---|
| 單檔 | 50 MB | `413` |
| 附件合計 | 50 MB | `413` |
| 單一 job 輸入合計（含 transcript） | 100 MB | `413` |
| 空檔案 | — | `400` |
| 同一個 key 帶兩次 | — | `400` |
| 不是你的 / 不存在 | — | `404`（**訊息相同**） |

最後一列是刻意的：分開講等於給人探測別人 job id 的工具。

**worker**：把附件放進工作目錄**根層**（不是子目錄）—— Claude 一進去就該看到
它們，這些是 job 的標的。同名檔會加序號（`a.pdf`、`a-2.pdf`），不互相覆蓋。

#### 🔑 誰負責去重：**UI 決定，worker 只是防呆**

worker 的序號迴圈要留著（兩個分頁、重送、race 都還是會撞，後端不能沒有防線），
但**正常路徑下它應該永遠不會真的改到名字**。

理由是 **worker 的改名是隱形的**。使用者從兩個資料夾各挑一個 `report.pdf`，
job 裡變成 `report.pdf` 與 `report-2.pdf`；Claude 在回答裡會用後面那個名字稱呼它，
改過的話也以 `report-2.pdf` 回到產出清單 —— 而使用者從頭到尾沒看過這個名字，
也沒有任何地方告訴他發生了改名。

UI 可以直接避免：`filename` 是客戶端送的（`POST /api/uploads/attachment` 的 body），
所以瀏覽器在使用者挑檔的當下就能發現撞名，當場講「你已經有一個 report.pdf，
這個會存成 report-2.pdf」或讓他自己改名。挑檔那一秒就知道，勝過事後從產出清單反推。

**契約**：`filename` 由客戶端保證不重複；worker 的序號是防呆，不是功能。
沒寫這一句的話兩邊都以為對方會處理，結果是使用者收到一個他沒命名過的檔案。

#### 🔑 那題的結論：改過的輸入檔會回傳，沒改的不會

用**內容雜湊**比對（放進工作目錄時記 sha256，收成果時再比一次）。

- 照檔名排除 → 「幫我改這份簡報」會**什麼都拿不到**，而那是主要情境之一
- 全部回傳 → 五份沒動過的 PDF 會出現在「產出的檔案」裡，變成噪音
- 用 mtime → 有些工具會原樣重寫檔案，會誤判成「改過」

**UI 文案可以這樣寫**：產出清單只會出現新檔案與被改過的檔案；
使用者原樣傳進去的東西不會再回來一次（他本來就有）。

---

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

- **〔改送給開放網路的 worker〕按鈕**：後端的 `blocked_by_network` 已備好，
  但只有一台 worker 時按了沒地方送。等有第二台。
- **排行榜**：整個沒有。等有第二個使用者才有意義 —— 現在只有一個人，
  而人不能欠自己。
