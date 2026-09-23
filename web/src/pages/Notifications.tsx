// Teams 通知的**目的地**設定。
//
// 這頁不是開關。目的地有三種：私訊、共用頻道、以及「沒有」。
// 零設定的人通常已經會被通知 —— 走共用頻道並 @ 本人（notify.py）。
// 填了自己的 webhook 只是把目的地換成私訊，換到的是「同事看不到」與「顯示金額」。
//
// 「沒有」是真的會發生的狀態：hub 的 TEAMS_CHANNEL_WEBHOOK 沒設時，
// notify.py 直接 return，那時候沒設個人 webhook 的人一則都收不到。
// 這頁必須認得那個狀態，否則它會宣稱一個不存在的頻道通知。
//
// 這個框架是刻意的：寫成開關的話，四個 Teams 步驟讀起來像必做的設定，
// 而它其實是可選的升級 —— 除非目的地是「沒有」，那時它才真的必做。
//
// webhook 用 Workflows 的 chat 範本，目的地是建立時固定的，沒辦法靠 payload
// 指定對象。好處是不需要管理員權限，也不需要 email 對照表。

import { useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";

export function Notifications() {
  const { me } = useAuth();
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [dm, setDm] = useState(me?.has_teams_webhook ?? false);
  // 頻道那一側是 hub 的設定，不會因為這頁的操作而改變。
  const channel = me?.channel_notifications ?? false;
  const channelName = me?.channel_name || "共用頻道";
  const nowhere = !dm && !channel;

  function channelMessage() {
    return channel
      ? `已改回頻道通知。之後 job 跑完會在${channelName}裡 @ 你。`
      : "已移除私訊網址。共用頻道沒有設定，所以現在不會有任何通知送到你這裡。";
  }

  async function save(next: string) {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.setTeamsWebhook(next);
      setDm(r.has_teams_webhook);
      setMsg(
        r.has_teams_webhook
          ? "已改成私訊，剛剛送了一則測試訊息過去。沒收到的話代表網址不對。"
          : channelMessage(),
      );
      if (!r.has_teams_webhook) setUrl("");
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "儲存失敗");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <h1>Teams 通知</h1>
      {/* 這句要跟 notify.py 送得出的東西對得上。2026-09-23 加了結清與催促
          兩則之後，原本的「job 跑完與掛債」就少講了 —— 少講的方向雖然不像
          多講那麼糟，但這頁的職責就是讓人知道會收到什麼。 */}
      <p className="lede">
        job 跑完、掛債，以及帳本上的結清與催促，都會發 Teams 通知。這頁決定它送到哪裡。
      </p>

      {/* 頻道那一側的代價寫得直白：它是隱私成本，不是功能差異（web-spec §9）。 */}
      {dm && (
        <p>
          現在送到<strong>你的私訊</strong>。只有你看得到，訊息裡會顯示金額。
        </p>
      )}
      {!dm && channel && (
        <p>
          現在送到 <strong>{channelName}</strong>，並 @ 你，所以你會收到紅點通知，
          不只是頻道裡多一則訊息。不需要任何設定 —— 代價是{" "}
          <strong>同事看得到你在跑 job</strong>。頻道訊息不顯示金額。
        </p>
      )}
      {nowhere && (
        <p>
          <strong>目前沒有任何通知會送到你這裡。</strong>
          這套系統的共用頻道還沒設定，所以要收到通知，你必須自己建一個 webhook。
        </p>
      )}
      <p className="hint">
        {nowhere
          ? "在 Teams 建一個自己的 webhook 貼到下面。"
          : "想改成私訊的話，在 Teams 建一個自己的 webhook 貼到下面。"}
      </p>

      {/* 目的地是「沒有」的時候，這四步不是可選的升級，是唯一的路 —— 不該收起來。 */}
      <details className="cmds" open={nowhere}>
        <summary>怎麼拿到 webhook 網址？（四步，不需要管理員權限）</summary>
        <ol className="steps">
          <li>
            在 Teams 開啟 <strong>Workflows</strong> 應用程式
          </li>
          <li>
            搜尋範本 <code>Send webhook alerts to a chat</code>
            <span className="muted">（要 chat 版本，不是 channel 版本）</span>
          </li>
          <li>選擇要收通知的聊天，按 Save</li>
          <li>複製它給你的 webhook 網址，貼到下面</li>
        </ol>
        <p className="hint">
          也不需要付費授權。
          <br />
          ⚠️ 舊的「Incoming Webhook」連接器已於 2026-05-22 停用，網路上多數教學已經過時。
        </p>
      </details>

      <label>
        Webhook 網址
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://prod-xx.westus.logic.azure.com/workflows/..."
        />
      </label>

      {msg && <p className="hint under-field">{msg}</p>}

      <div className="actions">
        <button type="button" disabled={busy || !url.trim()} onClick={() => save(url)}>
          {busy ? "儲存中…" : "儲存並測試"}
        </button>
        {/* 按這顆不是「關閉通知」—— 有頻道的話通知還是會來，只是回到頻道。
            按鈕要講後果，所以沒有頻道可回的時候也不能寫「改回頻道通知」。 */}
        {dm && (
          <button type="button" className="small" disabled={busy} onClick={() => save("")}>
            {channel ? "改回頻道通知" : "移除私訊網址（之後不會有通知）"}
          </button>
        )}
      </div>

      <p className="hint">
        通知裡只會有 job 的狀態、花費與連結 —— <strong>不會包含你送出的內容</strong>，
        也不會放檔案的下載連結（那本身就是憑證）。
      </p>
    </div>
  );
}
