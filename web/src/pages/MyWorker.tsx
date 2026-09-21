// 出租者的介面。極簡：狀態卡 + 產生 token + 暫停接單。
//
// 不做「封鎖特定借用者」（web-spec §8）—— 在人情系統裡那是社交核彈，
// 一旦按鈕存在，沒按也會有人猜想。而它要解決的問題在 10 人團隊裡用講的比較快。

import { useCallback, useEffect, useState } from "react";
import { api, type WorkerRow } from "../api";
import { useAuth } from "../auth";

const QUOTA: Record<WorkerRow["quota"], string> = {
  green: "🟢 充裕",
  yellow: "🟡 快滿了",
  red: "🔴 幾乎沒了",
  unknown: "⚪️ 還不知道",
};

export function MyWorker() {
  const { me } = useAuth();
  const [workers, setWorkers] = useState<WorkerRow[]>([]);
  const [name, setName] = useState("");
  const [issued, setIssued] = useState<string | null>(null);

  const load = useCallback(() => {
    api.listWorkers().then(setWorkers).catch(() => setWorkers([]));
  }, []);
  useEffect(load, [load]);

  const mine = workers.filter((w) => w.owner === me?.display_name);

  async function create() {
    const w = await api.createWorker(name || "我的電腦");
    setIssued(w.token);
    setName("");
    load();
  }

  return (
    <div className="card">
      <h1>我的 worker</h1>
      <p className="lede">借出額度給同事。你的 Claude 憑證不會離開這台機器。</p>

      {mine.map((w) => (
        <div key={w.id} className="worker">
          <div>
            <strong>{w.name}</strong>{" "}
            <span className="muted">
              {w.online ? "🟢 線上" : "⚫️ 離線"} · 額度 {QUOTA[w.quota]} ·{" "}
              {w.allow_full_network ? "🌐 開放網路" : "🔒 白名單網路"} ·{" "}
              {w.available_models.join(" / ") || "—"}
              {w.claude_code_version && ` · CLI ${w.claude_code_version}`}
            </span>
          </div>
          <button
            className="small"
            onClick={() => api.setAccepting(w.id, !w.accepting).then(load)}
          >
            {w.accepting ? "暫停接單" : "恢復接單"}
          </button>
        </div>
      ))}

      <h2>新增一台</h2>
      {/* 以前用一個 &nbsp; 的 label 包住按鈕來對齊 —— label 裡包 button，
          讀屏軟體會念得很奇怪。改成一般的底端對齊。 */}
      <div className="field-row">
        <label>
          名稱
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="我的電腦" />
        </label>
        <button type="button" onClick={create}>
          產生 worker token
        </button>
      </div>

      {issued && (
        <div className="consent">
          <p>
            <strong>把這串貼進 worker 的 <code>.env</code>：</strong>
          </p>
          <pre>WORKER_TOKEN={issued}</pre>
          <p>
            這串只會顯示這一次。worker 用它連 Hub，完全不需要你的 Observ 帳密 ——
            把公司密碼寫進 <code>.env</code> 是不必要的風險。
          </p>
        </div>
      )}
    </div>
  );
}
