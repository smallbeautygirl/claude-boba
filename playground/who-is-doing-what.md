# 誰在動什麼

這個 repo 同時有多個 Claude session 在寫。這份檔案的用途**只有兩個**：

1. **認領**：現在誰在動哪些檔案
2. **交接**：一邊做好、另一邊要接的契約

**不要在這裡寫「我改了什麼」** —— `git log` 已經完整記錄了，手寫一份只會過期，
而且過期的紀錄比沒有紀錄危險。要看改動就跑：

```bash
git log --format='%h %s' --name-only -10
```

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

---

## 目前認領

| 區域 | 誰 | 狀態 |
|---|---|---|
| `web/src/**`（UI 改版） | session A | 進行中 —— 其他人不要碰 |
| `hub/**`、`worker/**` | session B | 進行中 |
| `docs/web-spec.md` | 共用 | 小心：兩邊都會改，改前先 `git pull` 心態看一次現況 |
| `SPEC.md`、`CLAUDE.md` | 共用 | 同上 |

改完一個區域就把狀態改掉。

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
