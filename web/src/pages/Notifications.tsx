// Teams 通知設定。
//
// 每個人自己在 Teams 建一個 webhook 貼進來 —— Workflows 的 chat 範本，
// 目的地是建立時固定的，沒辦法靠 payload 指定對象。好處是不需要管理員權限，
// 也不需要 email 對照表，而且每個人能自己關掉。

import { useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";

export function Notifications() {
  const { me } = useAuth();
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [on, setOn] = useState(me?.has_teams_webhook ?? false);

  async function save(next: string) {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.setTeamsWebhook(next);
      setOn(r.has_teams_webhook);
      setMsg(
        r.has_teams_webhook
          ? "已儲存，剛剛送了一則測試訊息到你的 Teams。沒收到的話代表網址不對。"
          : "已關閉通知。",
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
      <p className="lede">
        {on ? "🔔 已開啟" : "🔕 尚未設定"} — job 跑完、掛債、結清時會通知你。
      </p>

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
        不需要管理員權限，也不需要付費授權。
        <br />
        ⚠️ 舊的「Incoming Webhook」連接器已於 2026-05-22 停用，網路上多數教學已經過時。
      </p>

      <label>
        Webhook 網址
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://prod-xx.westus.logic.azure.com/workflows/..."
        />
      </label>

      {msg && <p className="hint">{msg}</p>}

      <div className="actions">
        <button type="button" disabled={busy || !url.trim()} onClick={() => save(url)}>
          {busy ? "儲存中…" : "儲存並測試"}
        </button>
        {on && (
          <button type="button" className="small" disabled={busy} onClick={() => save("")}>
            關閉通知
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
