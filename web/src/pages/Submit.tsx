// 提交頁 = 登入後的首頁。
//
// 使用情境是「我額度爆了，很急」，所以最短路徑優先：一個框、一個下拉、一個勾選。
// 不做精靈式多步驟 —— 拆成三頁只是增加三次點擊（web-spec §3）。

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";

// 站台白名單。預設不含 Fable：它的 output 單價是 Haiku 的 10 倍、Sonnet 的 5 倍，
// 同一個 job 用 Haiku 是一杯手搖、用 Fable 就是一頓好料（SPEC §9）。
const MODELS = [
  { value: "sonnet", label: "Sonnet（預設）" },
  { value: "haiku", label: "Haiku（最省）" },
];

export function Submit() {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState("sonnet");
  const [consented, setConsented] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ready = name.trim() && prompt.trim() && consented && !busy;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!ready) return;
    setBusy(true);
    setError(null);
    try {
      const job = await api.createJob({ borrower_label: name, prompt, model });
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
        你是誰
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="例如：BD Kevin"
          maxLength={120}
        />
      </label>

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

      <div className="row">
        <label>
          出租者
          <select disabled>
            <option>自動（推薦）</option>
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
