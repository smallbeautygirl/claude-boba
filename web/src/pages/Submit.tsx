// 提交頁 = 登入後的首頁。
//
// 使用情境是「我額度爆了，很急」，所以最短路徑優先：一個框、一個下拉、一個勾選。
// 不做精靈式多步驟 —— 拆成三頁只是增加三次點擊（web-spec §3）。

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type CommandCatalog, type WorkerRow } from "../api";

// 站台白名單。預設不含 Fable：它的 output 單價是 Haiku 的 10 倍、Sonnet 的 5 倍，
// 同一個 job 用 Haiku 是一杯手搖、用 Fable 就是一頓好料（SPEC §9）。
// 額度只給紅綠燈，不給百分比：精確數字會讓人盤算「他還有 66%，再送一個沒差」，
// 把人情變成資源計算（web-spec §3）。
const QUOTA: Record<WorkerRow["quota"], string> = {
  green: "🟢",
  yellow: "🟡",
  red: "🔴",
  unknown: "⚪️",
};

const MODELS = [
  { value: "sonnet", label: "Sonnet（預設）" },
  { value: "haiku", label: "Haiku（最省）" },
];

export function Submit() {
  const navigate = useNavigate();
  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState("sonnet");
  const [workerId, setWorkerId] = useState("");
  const [workers, setWorkers] = useState<WorkerRow[]>([]);
  const [catalog, setCatalog] = useState<CommandCatalog | null>(null);
  const [consented, setConsented] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listWorkers().then(setWorkers).catch(() => setWorkers([]));
    api.commands().then(setCatalog).catch(() => setCatalog(null));
  }, []);

  // 點一下把指令插到輸入框最前面，游標留在後面讓人接著打。
  // 不直接送出 —— 多數指令要搭配內容才有意義。
  function insert(name: string) {
    setPrompt((prev) => (prev.startsWith(name) ? prev : `${name} ${prev}`.trimEnd() + " "));
  }

  const ready = prompt.trim() && consented && !busy;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!ready) return;
    setBusy(true);
    setError(null);
    try {
      const job = await api.createJob({
        prompt,
        model,
        requested_worker_id: workerId || null,
      });
      navigate(`/jobs/${job.id}`);
    } catch (err) {
      // 錯誤訊息一律正經，不用俏皮話 —— 使用者要的是「發生什麼事、我該怎麼辦」。
      setError(err instanceof Error ? err.message : "送出失敗，請再試一次");
      setBusy(false);
    }
  }

  return (
    <form className="card" onSubmit={submit}>
      <h1>丟一個 job 出去 🧋</h1>
      <p className="lede">額度用完了？找還有額度的同事幫你跑。跑完請他喝一杯就好。</p>

      <label>
        對話內容
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          rows={12}
          placeholder="把你的對話貼進來，或直接寫你要它做什麼"
        />
      </label>
      <p className="hint">
        ⚠️ 貼上的對話會被「讀過」，但 Claude 不會真的記得當時的環境。
        用 Claude Code 的話，上傳 <code>.jsonl</code> 可以真正接續。
      </p>

      {catalog && (
        <details className="cmds">
          <summary>可以用的指令（點一下插入）</summary>
          {catalog.groups.map((g) => (
            <div key={g.id} className="cmd-group">
              <div className="cmd-head">
                {g.title} <span className="muted">· {g.audience}</span>
              </div>
              <p className="muted">{g.hint}</p>
              <div className="chips">
                {g.commands.map((c) => (
                  <button
                    type="button"
                    key={c.name}
                    className="chip-btn"
                    title={`${c.name} — ${c.desc}`}
                    onClick={() => insert(c.name)}
                  >
                    {c.label}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </details>
      )}

      <div className="row">
        <label>
          出租者
          <select value={workerId} onChange={(e) => setWorkerId(e.target.value)}>
            <option value="">自動（推薦）</option>
            {workers.map((w) => (
              <option key={w.id} value={w.id} disabled={!w.online}>
                {w.owner} · {w.online ? "🟢" : "⚫️"} ·{" "}
                {w.allow_full_network ? "🌐 開放網路" : "🔒 白名單"} · 額度 {QUOTA[w.quota]}
              </option>
            ))}
          </select>
        </label>
        <label>
          Model
          <select value={model} onChange={(e) => setModel(e.target.value)}>
            {MODELS.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {/* 必須主動勾選，不能預設打勾 —— 預設打勾的同意等於沒有揭露（web-spec §3）。 */}
      <div className="consent">
        <label className="checkbox">
          <input
            type="checkbox"
            checked={consented}
            onChange={(e) => setConsented(e.target.checked)}
          />
          <span>
            我了解 <strong>出租者技術上可以看到我送出的內容</strong>
          </span>
        </label>
        <p>
          這個 job 會在對方的電腦上執行。系統預設不讓出租者查看內容，但技術上他有能力看到。
          請不要送出公司機密、客戶個資，或任何你不希望被對方看到的東西。
        </p>
      </div>

      {error && <p className="error">{error}</p>}

      <button type="submit" disabled={!ready}>
        {busy ? "送出中…" : "送出"}
      </button>
    </form>
  );
}
