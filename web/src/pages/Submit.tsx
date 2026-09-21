// 提交頁 = 登入後的首頁。
//
// 使用情境是「我額度爆了，很急」，所以最短路徑優先：一個框、一個下拉、一個勾選。
// 不做精靈式多步驟 —— 拆成三頁只是增加三次點擊（web-spec §3）。

import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type WorkerRow } from "../api";
import { CommandPicker } from "../CommandPicker";

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

// 站台白名單。順序即偏好順序，第一個就是預設 —— 預設不是 Opus，因為 BD/PM 不會知道
// 差別、會直接送出，而那等於每個 job 貴 2.5 倍（web-spec §3）。
const SITE_MODELS = [
  { value: "sonnet", label: "Sonnet（預設）" },
  { value: "haiku", label: "Haiku（最省）" },
];

// 選單顯示的是「站台白名單 ∩ 該出租者白名單」（web-spec §3）。
// 寫死一份清單的話，挑了只有 Haiku 的 worker 仍然選得到 Sonnet，要等 job 送出去才失敗 ——
// 而失敗不計債（SPEC §5），那台機器的額度就白燒了。
//
// 「自動」沒有特定出租者，取所有可接單 worker 的**交集**而不是聯集：
// Hub 的派單目前不按 model 過濾，聯集會把 job 派給一台跑不動它的機器。
function offeredModels(workers: WorkerRow[], workerId: string) {
  const pool = workerId
    ? workers.filter((w) => w.id === workerId)
    : workers.filter((w) => w.online && w.accepting);
  if (pool.length === 0) return SITE_MODELS;
  const offered = SITE_MODELS.filter((m) =>
    pool.every((w) => w.available_models.includes(m.value)),
  );
  // 交集是空的（worker 還沒回報過 config）就退回站台白名單 —— 總比給一個空下拉好。
  return offered.length ? offered : SITE_MODELS;
}

export function Submit() {
  const navigate = useNavigate();
  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState("sonnet");
  const [workerId, setWorkerId] = useState("");
  const [workers, setWorkers] = useState<WorkerRow[]>([]);
  const [consented, setConsented] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listWorkers().then(setWorkers).catch(() => setWorkers([]));
  }, []);

  const models = useMemo(() => offeredModels(workers, workerId), [workers, workerId]);
  const lender = workers.find((w) => w.id === workerId)?.owner ?? null;

  // 選中的 model 對方跑不動就退回第一個他跑得動的。在 render 期間收斂而不是用 effect ——
  // worker 清單是非同步載進來的，用 effect 會有一個 render 的空窗送出跑不動的 model。
  const chosen = models.some((m) => m.value === model) ? model : models[0].value;

  // 換出租者要重新勾同意：上一次勾的是對「另一個人」的揭露。
  // 點名字才是這個勾選的重點（web-spec §3），沿用等於把名字當裝飾。
  function pickWorker(id: string) {
    setWorkerId(id);
    setConsented(false);
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
        model: chosen,
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

      <CommandPicker value={prompt} onChange={setPrompt} />

      <div className="row">
        <label>
          出租者
          <select value={workerId} onChange={(e) => pickWorker(e.target.value)}>
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
          <select value={chosen} onChange={(e) => setModel(e.target.value)}>
            {models.map((m) => (
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
            我了解{" "}
            <strong>
              {lender ? `${lender} 技術上可以看到我送出的內容` : "出租者技術上可以看到我送出的內容"}
            </strong>
          </span>
        </label>
        <p>
          這個 job 會在{lender ? ` ${lender} ` : "對方"}的電腦上執行。
          系統預設不讓出租者查看內容，但技術上他有能力看到。
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
