---
name: observ-event-lookup
description: 查 Observ 事件紀錄。使用者給事件紀錄 id、tracking_id、客戶端事件名稱＋時間，或 Observ 事件類型 id＋時間窗；拿回截圖檔、發生時間、審核狀態、VLM 回答、middleware 的二次驗證與轉發結果（有沒有轉給中華／資拓、為什麼沒轉）。只讀，不改任何東西。使用者提到 Observ、事件紀錄、event_record_id、tracking_id、截圖、客戶說收到／沒收到某個事件、坑洞辨識這類事件名稱時使用。
---

# 查 Observ 事件

PM 的日常：客戶（中華、資拓）說「我們收到／沒收到一個事件」，PM 要回頭找那筆事件的
截圖與細節，並解釋 middleware 為什麼轉了或沒轉。你用的工具只有一支腳本，
它會同時查 Observ（原始紀錄與截圖）和 middleware（二次驗證、轉發結果）。

## 🚨 唯一的硬規則：只透過腳本查，不要自己打 API

```bash
python3 .claude/skills/observ-event-lookup/scripts/observ_lookup.py <子指令> ...
```

- **不要**自己用 `urllib`／`node` 組任何 Observ 或 middleware 的網址，**不要** `echo $OBSERV_TOKEN`，
  **不要**把 token 寫進檔案。這個 job 的每一行指令都會被 hub 存進資料庫並重播給使用者看；
  token 一進指令列，就等於把委託者 72 小時的 Observ 身分寫進資料庫。腳本從環境變數讀 token，
  只放進 HTTP 請求裡，其他地方都不會出現。
- 腳本只做 GET。這個指令**不建立、不修改、不刪除**任何 Observ 上的東西。使用者要改審核狀態、
  加註記、建偵測 → 直說這個指令不做，請他到 Observ 網頁操作。

## 使用者會怎麼講、對應哪個子指令

| 使用者手上有的 | 子指令 | 例 |
|---|---|---|
| 一個七位數的編號（Observ 事件紀錄 id；他可能誤稱 event_type_id） | `record` | `record 1111954` |
| `6053-1111954` 這種帶連字號的（middleware 的 tracking_id） | `tracking` | `tracking 6053-1111954` |
| 客戶端看到的事件名稱＋大概時間（「昨天中華收到的坑洞事件」） | `name` | `name 坑洞 --start ... --end ... --vendor cht` |
| Observ 事件類型 id＋時間窗（「10650 昨天的全部」） | `type` | `type 10650 --start ... --end ...` |
| 想知道某類型 VLM 回答的中文對照 | `vlm-labels` | `vlm-labels 10650` |

**先判斷編號是哪一層。** Observ 的事件類型 id 多半是三到五位數（139、10650），
事件紀錄 id 是七位數以上（1111954、9482466）。使用者說「event_type_id=1111954」時，
那幾乎一定是事件紀錄 id，直接用 `record`。

**Observ 查不到不代表沒有這筆。** Observ 的查詢 API 有時不回某些紀錄（可能被刪除或篩掉），
但 middleware 還留著完整內容。腳本會自動改從 middleware 補，每筆的 `data_source` 是
`middleware` 時要跟使用者講明「Observ 查不到，以下取自 middleware」。
只有腳本的 notes 說「Observ 與 middleware 都查不到」時，才請使用者確認編號，不要自己猜是打錯字。

**客戶的「結束」訊息帶的紀錄編號是合成的**（epoch 毫秒，13 位數），Observ 查不到。
那種情況要用訊息裡的 `tracking_id`（`tracking` 子指令）。

## 時間

- `--start` / `--end` 用 ISO 8601，**沒帶時區當台北時間**：`2026-07-24T00:00:00+08:00`。
  使用者說「昨天」「上週三」，你換算成台北時間的整天，並在回覆裡寫出你用的區間。
- `type` / `name` 沒給時間窗時，腳本用**最近 24 小時**，你要在回覆裡講明。
- middleware 的轉發結果一次最多 7 天；超過就分段查。

## 量

- 預設一次 20 筆截圖，`--limit` 最多 40。腳本輸出的 `total` 大於 `shown` 時，
  **列出總數、請使用者縮小範圍**（更窄的時間、指定 `--vendor cht` 或 `--outcome delivered`），
  不要自己多跑幾次把 40 張以上的圖塞滿工作目錄。
- 中華排前面：兩家都列時先講中華（`--vendor cht` 只看中華，`iisi` 是資拓）。
- **不下載影片**，只在摘要裡說「有影片片段，Observ 歷史頁可看」。
- **不要 `Read` 截圖來描述內容**，除非使用者明確要求「幫我看圖裡有什麼」。他要的是拿到截圖，
  Observ 的 VLM 回答已經是一份機器描述；看圖花的是代跑者的額度。

## 回覆長什麼樣

腳本會在 `--out`（預設工作目錄）寫：

- `event_<紀錄id>_<YYYYMMDD-HHMMSS>.jpg`：截圖（VLM flow 有第二張時 `_2`）
- `summary.md`：每筆一節，內容跟 stdout 的 JSON 一致

你的回覆**把 `summary.md` 的內容貼出來**（使用者在 job 頁面下載檔案，但摘要要在對話裡就看得到），
語言跟使用者一樣（通常是中文）。每筆依這個順序講：

1. **Observ 事件頁連結**（`observ_url`）放在最前面，讓 PM 一鍵點過去。它是 `null` 時代表
   Observ 查不到這筆，**不要自己組一個連結**，改講「Observ 查不到，以下取自 middleware」。
2. 發生時間（台北時間）、事件類型與客戶端名稱、攝影機、審核狀態、VLM 回答（中文對照）。
3. **middleware 的資訊**，這是 PM 最需要的細節，不要省略：
   - 轉發結果與轉給誰（中華排前面）、在不在白名單上，以及腳本給的**原因**（`outcome_why`）
   - 二次驗證有沒有過、用哪個模型、模型的原始回答
   - middleware 收到的時間與「發生後幾秒」、收到管道、訊息狀態、走到哪一關
   - 一次發生的起訖時間與訊息數（開始、進行中、結束）
   - middleware 歷史事件頁連結（`middleware.ui_url`），並附註它是依事件類型篩選、
     頁面預設只顯示最近 24 小時，較舊的事件要在頁面上調時間
   `middleware` 是 `null` 時講「middleware 沒有這筆」。

## 結束碼與該說的話

| 結束碼 | 意思 | 你要做的 |
|---|---|---|
| 0 | 正常 | 貼摘要 |
| 2 | 參數錯 | 看 stdout 的 `message` 修參數再跑 |
| **3** | **Observ 登入過期** | **照抄 stdout 的 `message`**（「Observ 登入已過期，請重新登入 boba 後用「接著問」重試。」）。**不要**說指令壞了、不要重試、不要另想辦法拿 token |
| 4 | Observ 或 middleware 回錯 | 把 `where` / `status` 講給使用者，並說明是哪一邊的服務回的 |

stdout JSON 的 `notes[]` 是腳本要你轉達的事（middleware 連不上、某筆截圖抓不到、
超過 7 天、Observ 沒有這筆…）。**每一條都要講**，不要吞掉。

## 環境（供你判讀錯誤，不用自己設）

hub 在派單時注入 `OBSERV_TOKEN`、`OBSERV_BASE_URL`、`OBSERV_SERVICE_ID`、`MIDDLEWARE_BASE_URL`。
腳本說缺環境變數 → 這個 job 沒帶 Observ 身分（提交時 prompt 沒有以 `/observ-event-lookup` 開頭），
請使用者重新用「查 Observ 事件」送一次。middleware 用釘住的自簽憑證驗 TLS（`scripts/middleware-ca.pem`）；
憑證不符時腳本會退回只查 Observ 並在 `notes` 裡講。
