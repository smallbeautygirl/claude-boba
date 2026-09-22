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

// 前端先擋，不要讓人上傳三分鐘才說太大（web-spec §3）。
// 依據：一句 "pong" 的 transcript 就 224 KB，真實 RD session 估 10–50 MB；
// 超過 50 MB 的 session，`--resume` 本身也會慢到不實用。
const MAX_TRANSCRIPT_BYTES = 50 * 1024 * 1024;

function fmtSize(n: number): string {
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

// 只看第一行。整份 50 MB 在瀏覽器裡解析，只為了確認它是 JSONL，划不來。
async function looksLikeSession(file: File): Promise<boolean> {
  const head = await file.slice(0, 64 * 1024).text();
  const first = head.split("\n").find((l) => l.trim());
  if (!first) return false;
  try {
    JSON.parse(first);
    return true;
  } catch {
    return false;
  }
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
  // 上傳的 session 檔。一選好就上傳，不是等到按送出 ——
  // 50 MB 的檔案在按下送出之後才開始傳，使用者會盯著一顆沒反應的按鈕。
  const [session, setSession] = useState<{ name: string; size: number; key: string } | null>(
    null,
  );
  const [uploading, setUploading] = useState(false);

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

  const ready = prompt.trim() && consented && !busy && !uploading;

  async function pickFile(file: File | undefined) {
    if (!file) return;
    setError(null);
    if (file.size > MAX_TRANSCRIPT_BYTES) {
      setError(
        `這個 session 檔 ${fmtSize(file.size)}，超過 50 MB 的上限。` +
          "這麼大的 session，--resume 本身也會慢到不實用。",
      );
      return;
    }
    if (!(await looksLikeSession(file))) {
      setError(
        "這不像 Claude Code 的 session 檔。它在 ~/.claude/projects/<專案>/ 底下，" +
          "副檔名 .jsonl，每行是一個 JSON。",
      );
      return;
    }
    setUploading(true);
    try {
      const key = await api.uploadTranscript(file);
      setSession({ name: file.name, size: file.size, key });
    } catch (err) {
      setError(err instanceof Error ? err.message : "上傳失敗，請再試一次");
    } finally {
      setUploading(false);
    }
  }

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
        transcript_key: session?.key ?? null,
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

      {/* 帶了 session 檔之後，這個框的意思就變了：上下文在檔案裡，
          這裡要填的是「接下來做什麼」，不再是「把對話貼進來」。 */}
      <label>
        {session ? "接下來要它做什麼" : "對話內容"}
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          rows={session ? 5 : 12}
          placeholder={
            session
              ? "沿用上傳的 session 繼續問…"
              : "把你的對話貼進來，或直接寫你要它做什麼"
          }
        />
      </label>
      <p className="hint">
        {session ? (
          <>
            ✅ 這是真的續跑 —— 出租者那邊會用 <code>--resume</code> 接上你這份 session，
            不是把對話當文字重讀一次。
          </>
        ) : (
          <>
            ⚠️ 貼上的對話 Claude 讀得到文字，但讀不到當時執行過的指令與開啟的檔案。
          </>
        )}
      </p>

      {/* 兩種來源分開講。原本只寫「上傳 .jsonl 可以真正接續」是 RD 視角 ——
          BD/PM 看了會去找一個他們拿不到的檔案：claude.ai 與桌面 app 的對話
          不是 Claude Code 的 session，`--resume` 吃不下，這不是少做一個功能。
          而對他們來說貼上本來就幾乎不會少東西，因為那種對話沒有本機環境。 */}
      {!session && (
        <div className="sources">
          {/* 分界不是「有沒有碰你的檔案」，是 **session 存在哪裡**。
              Cowork 會讀寫你指定資料夾裡的檔案，但它跑在 Anthropic 的伺服器上、
              session 存在你的 Claude 帳號裡（所以手機上打得開同一個工作）——
              本機沒有 .jsonl。用「有沒有碰檔案」當條件會把 Cowork 判錯邊。

              也不要用產品名稱：職稱不是分界（用過 Claude Code 的 PM 也有檔案），
              app 也不是（Claude Desktop 一個 app 裡就有 Chat / Cowork / Code）。
              名稱與分頁結構還會再變，但「狀態存在哪」是這件事的成因。 */}
          <p>
            <strong>你能在手機上打開同一個對話、接著問嗎？</strong>
            可以的話它存在你的 Claude 帳號裡（Chat、Cowork、claude.ai）——
            本機沒有 session 檔，全選複製、貼上就好。那種對話貼上幾乎不會少東西，
            <em>也不需要</em>檔案。附件目前還帶不過來。
          </p>
          <p>
            <strong>只存在那一台電腦上</strong>（終端機的 <code>claude</code>、
            VS Code / JetBrains 擴充、Claude Desktop 的 Code 分頁）：那是
            Claude Code，它留了 <code>.jsonl</code> —— 上傳它才是真的續跑。
          </p>
          <p>不論哪一種，這個 job 跑完之後用「接著問」，之後每一輪都是真的續跑。</p>
        </div>
      )}

      <div className="session">
        {session ? (
          <>
            <div className="session-file">
              <strong>{session.name}</strong>
              <span className="muted"> · {fmtSize(session.size)}</span>
            </div>
            <button type="button" className="small" onClick={() => setSession(null)}>
              移除
            </button>
          </>
        ) : (
          <>
            <label className="session-pick">
              {uploading ? "上傳中…" : "上傳 .jsonl"}
              <input
                type="file"
                accept=".jsonl"
                disabled={uploading}
                onChange={(e) => {
                  void pickFile(e.target.files?.[0]);
                  e.target.value = "";
                }}
              />
            </label>
            <span className="muted">在自己電腦上跑到一半、額度用完了？</span>
            {/* 「去 ~/.claude/projects/ 找」不是可執行的指示 —— 那底下一個專案
                一個資料夾，隨便一台機器就上百個 session 檔，而檔名是 session id，
                看不出內容。要給就要給到能貼進終端機的程度。 */}
            <details className="cmds findfile">
              <summary>怎麼找到那個檔案？</summary>
              <p className="muted">在終端機跑這行，列出最近改動的三個 session：</p>
              <pre>ls -t ~/.claude/projects/*/*.jsonl | head -3</pre>
              <p className="muted">
                一個專案一個資料夾，資料夾名稱是專案路徑把 <code>/</code> 換成{" "}
                <code>-</code>。檔名是 session id，看不出內容 ——
                挑剛剛那個專案底下時間最近的通常就對。
              </p>
            </details>
          </>
        )}
      </div>

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
