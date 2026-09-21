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

**一律明確列出自己的路徑：**

```bash
git add hub/ worker/ docs/web-spec.md     # ✅ 只加自己動過的
git add -A                                 # ❌ 永遠不要
```

提交前先看一眼 `git status --short`，出現不是你改的檔案就停下來。

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

- **檔案上傳**：`web-spec` §3 的線框畫了「上傳 .jsonl」，但**後端還沒有**。
  在做出來之前畫面上不可以出現那個控制項或那句說明 —— 指向不存在的功能
  比少講一件事糟得多。web-spec 該段已標 🚧。
- **〔改送給開放網路的 worker〕按鈕**：後端的 `blocked_by_network` 已備好，
  但只有一台 worker 時按了沒地方送。等有第二台。
- **排行榜**：整個沒有。等有第二個使用者才有意義 —— 現在只有一個人，
  而人不能欠自己。
