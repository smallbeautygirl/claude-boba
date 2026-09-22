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
      {/* MinIO console。只有這一頁有，不放頁尾 —— 這個 bucket 是整站共用、
          prefix 分區，console 沒有「只看自己」這種權限。借用者點了不是進不去
          （白給一個挫折），就是繞過 hub 的擁有者檢查。出租者不一樣：這頁只有
          他們看得到，而且他們手上已經有更敏感的東西（自己的 Anthropic 憑證）。
          網址沒設就整段不出現 —— 一個連不上的連結比沒有連結更糟。 */}
      {me?.s3_console_url && (
        <>
          <h2>job 的檔案放在哪</h2>
          <p className="hint">
            對話紀錄、附件與產出都存在 MinIO 裡，一個 job 一個資料夾：
            <code>jobs/&lt;job id&gt;/</code>。平常不需要進去 —— job 頁面的下載
            連結走的是短效期的預簽網址。
          </p>
          <p className="privacy">
            <strong>這個 bucket 是整個站台共用的，沒有「只看自己」的權限。</strong>
            你在 console 裡看得到的不只是自己的 job，也包含其他人的對話與檔案。
            這頁之外的地方不會出現這個連結，原因就是這個。
          </p>
          <p className="console-open">
            <a
              className="chip-btn"
              href={me.s3_console_url}
              target="_blank"
              rel="noreferrer noopener"
            >
              開啟 MinIO console ↗
            </a>
          </p>
          <p className="hint">登入憑證跟管理員拿，畫面上不會有。</p>
        </>
      )}
    </div>
  );
}
