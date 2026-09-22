// Teams 通知的**目的地**設定。
//
// 這頁不是開關。零設定的人已經會被通知 —— 走共用頻道並 @ 本人（notify.py）。
// 填了自己的 webhook 只是把目的地換成私訊，換到的是「同事看不到」與「顯示金額」。
//
// 這個框架是刻意的：寫成開關的話，四個 Teams 步驟讀起來像必做的設定，
// 而它其實是可選的升級。寫成目的地，複雜度就變成使用者自己選擇要不要碰的。
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

  async function save(next: string) {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.setTeamsWebhook(next);
      setDm(r.has_teams_webhook);
      setMsg(
        r.has_teams_webhook
          ? "已改成私訊，剛剛送了一則測試訊息過去。沒收到的話代表網址不對。"
          : "已改回頻道通知。之後 job 跑完會在共用頻道裡 @ 你。",
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
      <p className="lede">job 跑完與掛債時會通知你。這頁決定通知送到哪裡。</p>

      {/* 頻道那一側的代價寫得直白：它是隱私成本，不是功能差異（web-spec §9）。 */}
      {dm ? (
        <p>
          現在送到<strong>你的私訊</strong>。只有你看得到，訊息裡會顯示金額。
        </p>
      ) : (
        <p>
          現在送到<strong>共用頻道</strong>，並 @ 你，所以你會收到紅點通知，
          不只是頻道裡多一則訊息。不需要任何設定 —— 代價是{" "}
          <strong>同事看得到你在跑 job</strong>。頻道訊息不顯示金額。
        </p>
      )}
      <p className="hint">想改成私訊的話，在 Teams 建一個自己的 webhook 貼到下面。</p>

      <details className="cmds">
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
        {/* 按這顆不是「關閉通知」—— 通知還是會來，只是回到頻道。
            按鈕要講後果，不然它就是這頁的第二句假話。 */}
        {dm && (
          <button type="button" className="small" disabled={busy} onClick={() => save("")}>
            改回頻道通知
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
