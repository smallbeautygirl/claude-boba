# Egress 白名單按指令開洞，不是全站一份

`worker/egress/allowlist` 目前只有一行 `^api\.anthropic\.com$`，而
`tinyproxy.conf` 的註解寫著「FilterDefaultDeny 是這整個設計的重點」。
要讓需要連外部服務的指令跑得起來，這條線一定得鬆 —— 決定是
**每個指令在自己的 `boba.json` 裡宣告要哪些網域，job 只開它選中那個指令要的洞**，
而不是把網域加進一張全站共用的清單。

## Considered Options

**全站一份 allowlist。** 簡單得多，worker 不用 per-job 生成設定。
否決的理由不是 Observ 本身，是第五個指令進來的時候 —— 那時 allowlist 已經是一張
**所有 job 都連得到的公司內網地圖**，而每個 job 都跑在同一台共用主機上。

**沿用既有的「開放網路／白名單」出借設定，讓代跑者自己決定。**
這是把一個安全決定丟給不知道指令需要什麼的人。代跑者能判斷的是
「我願不願意讓 job 連外網」，不是「Observ 的哪個網域是必要的」。

## Consequences

- **worker 必須能為每個 job 生成 allowlist**，而不是啟動時讀一份固定檔案。
  這是這個決定的主要工程成本。
- **未來每加一個網域，都會回頭引用這篇。** 它的價值與其說在 Observ，
  不如說在建立「開洞要具名、要跟著一個指令走」這個習慣。
- **證據很薄，而我們知道。** 見 ADR-0002 最後一條，同一個薄弱處。

## 2026-09-24 註記：前提變了一半，宣告照做、per-job proxy 先不做

這篇寫的時候 job 預設**關**外網。2026-09-23 紅線 3 改成預設**開**、代跑者可關
（SPEC §4.4），所以「要讓 Observ 指令連得到」這個直接動機消失了：預設就連得到。

第一個指令（`observ-event-lookup`）因此這樣做：

- **`boba.json` 的 `domains` 照宣告**。它的價值是這篇說的「開洞要具名、要跟著一個指令走」——
  而且它同時是「委託者的 Observ token 會被送到哪裡」的白紙黑字。
- **worker 不做 per-job allowlist**。它現在只服務一個指令、而且只服務關了外網的少數人。
  取而代之的是 SPEC §4.13 的「選了指令就收窄派單」：帶 Observ token 的 job 只派給開外網的
  代跑者，沒人可派就在提交時當場講（`routers/jobs.py::_check_network`）。
- 第二個需要內網的指令出現、或有代跑者關了外網卻想接這類 job 時，再回來做 per-job proxy。
  到那時 `domains` 已經是現成的輸入。

