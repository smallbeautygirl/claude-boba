# 出借你的 Claude 額度

這份文件是寫給**出租者**的 —— 你想讓同事在額度爆掉時借用你的 Claude，
但不想把帳號給出去。

跑完這份文件大約十分鐘，其中九分鐘是在等 `docker build`。
想跳過機械步驟的話直接跑 [`install.sh`](install.sh)。

> 開發整套系統（hub + worker + web）請看 [根目錄的 README](../README.md)。
> 這份只講出租端。

---

## 你實際上答應了什麼

**不是把帳號借出去。** 你的 Claude Code 憑證從頭到尾留在你的電腦上 ——
它以**唯讀**掛進每個 job 的容器，不進 image、不進環境變數、不上傳到任何地方。
借用者拿不到它，hub 也拿不到。

**是把算力借出去。** 別人的 prompt 會在你的機器上執行，花的是你的額度。
跑完你會在帳本上多一筆「他欠你一杯手搖」。

**幾件你該知道的事**，先講清楚比事後發現好：

- **每個 job 跑在全新的容器裡**，HOME 是全新目錄，跑完整個刪掉。你的
  `settings.json`、plugins、個人 skills 都不在裡面 —— 借用者的 job 碰不到。
- **預設沒有外網**。job 容器只連得到 `api.anthropic.com`，所以 `pip install`
  與 WebFetch 會失敗。這是刻意的：借用者的內容沒有路徑可以送出去。
- **每個 job 有花費上限**（預設 US$5）與時間上限（預設 10 分鐘），超過就中止。
- **你隨時可以按「暫停接單」**，不需要關掉程式。

---

## 你需要什麼

| | |
|---|---|
| 一台開著的電腦 | worker 關掉就不會接到單。筆電闔上也一樣 |
| Docker | job 跑在容器裡 |
| Python 3.12+ | worker 本身 |
| 已登入的 Claude Code | 就是你平常用的那個。下一節講怎麼找到憑證 |

---

## 六個步驟

### 1. 確認你的 Claude Code 是登入狀態

```bash
ls -l ~/.claude/.credentials.json
```

沒有這個檔案就先在終端機跑一次 `claude` 登入。**這個檔案就是你要出借的東西**，
待會要把它的路徑填進設定。

### 2. 在網頁產生 token

打開 claude-boba → **我的 worker** → 填一個名字（例如「Vivian 的 MBP」）→
**產生 worker token**。

那串 token **只會顯示一次**，馬上複製。它是這台 worker 的身分 ——
`worker` 因此完全不需要你的 Observ 帳密，**不要把公司密碼寫進任何設定檔**。

### 3. 起 egress proxy

```bash
cd worker
docker compose up -d
```

這會起一個白名單 proxy 與兩張網路。job 容器只接得到內部那張、沒有對外路由，
只有 proxy 出得去 —— 白名單就是在這裡生效的。

### 4. 建 job 容器的 image

```bash
docker build -t claude-boba-worker:2.1.278 .
```

這一步最久。tag 的版本號要跟 `.env` 裡的 `WORKER_IMAGE` 一致（預設值已經對好）。

### 5. 填設定

```bash
cp .env.example .env
```

一定要改的兩個：

```ini
WORKER_TOKEN=<步驟 2 那串>
CLAUDE_CREDENTIALS=/home/你/.claude/.credentials.json   # 要絕對路徑
```

`HUB_URL` 如果 hub 不在本機也要改。其餘的（花費上限、逾時、可用 model）
每一項在 `.env.example` 裡都寫了為什麼是那個值，改之前先讀那幾行。

### 6. 跑起來

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python worker.py
```

網頁的「我的 worker」應該在幾秒內變成**線上**。沒有的話看下一節。

---

## 出事的時候

| 現象 | 原因 |
|---|---|
| 網頁一直顯示離線 | `HUB_URL` 不對，或 hub 沒起來。worker 的終端機會印出連線錯誤 |
| job 一跑就回 `Not logged in` | 憑證路徑不對，或那個檔案不是登入狀態。**不要加 `--bare`**，它不讀 OAuth，錯誤訊息會把你導向「去登入」這條錯的路 |
| job 卡在「準備中」很久 | 第一次跑要複製 org skill 模板。之後會快很多 |
| 借用者說 `pip install` 失敗 | 正常。預設沒有外網，見上面「你實際上答應了什麼」 |
| 想看 job 的檔案 | 「我的 worker」頁有 MinIO console 的連結。路徑是 `jobs/<job id>/`，job id 在該 job 詳情頁的網址列上 |

---

## 不想接單的時候

- **暫時**：網頁按「暫停接單」。程式繼續跑，只是不再拿新的 job。
- **永久**：關掉 `worker.py` 就好。已經在跑的 job 會跑完。
- **建錯的 worker**：「我的 worker」可以刪除，但**只有從來沒跑過 job 的才能刪** ——
  跑過的刪掉會讓那些 job 失去出租者，而那是人情債的依據。
