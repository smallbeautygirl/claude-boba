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

// 附件的上限，跟 hub 的 schemas.py 對齊（契約：單檔 50 MB、合計 50 MB、
// 含 transcript 的 job 輸入合計 100 MB、最多 20 個）。前端擋一次是為了不要讓人
// 傳三分鐘才說太大；後端仍然會擋，前端的檢查不算數。
const MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024;
const MAX_ATTACHMENTS_TOTAL_BYTES = 50 * 1024 * 1024;
const MAX_JOB_INPUT_BYTES = 100 * 1024 * 1024;
const MAX_ATTACHMENTS = 20;

// 撞名就加序號，規則跟 worker 一致（`a.pdf` → `a-2.pdf`）。
//
// worker 也有一份同樣的迴圈，但那是防呆不是功能 —— 它的改名是**隱形的**：
// 使用者從兩個資料夾各挑一個 report.pdf，產出清單裡冒出一個 report-2.pdf，
// 而他從頭到尾沒看過這個名字。在這裡做，他挑完檔的當下就在清單上看到最終檔名。
function uniqueName(name: string, taken: Set<string>): string {
  if (!taken.has(name)) return name;
  const dot = name.lastIndexOf(".");
  const stem = dot > 0 ? name.slice(0, dot) : name;
  const ext = dot > 0 ? name.slice(dot) : "";
  for (let n = 2; ; n++) {
    const candidate = `${stem}-${n}${ext}`;
    if (!taken.has(candidate)) return candidate;
  }
}

interface Attachment {
  key: string;
  name: string;
  // 使用者挑的原始檔名。跟 name 不同就表示撞名被改過，要講出來。
  original: string;
  size: number;
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
  const [files, setFiles] = useState<Attachment[]>([]);

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

  async function pickAttachments(picked: FileList | null) {
    if (!picked?.length) return;
    setError(null);

    const taken = new Set(files.map((f) => f.name));
    let total = files.reduce((n, f) => n + f.size, 0);
    const queued: { file: File; name: string; original: string }[] = [];

    for (const file of Array.from(picked)) {
      if (files.length + queued.length >= MAX_ATTACHMENTS) {
        setError(`最多 ${MAX_ATTACHMENTS} 個附件`);
        break;
      }
      if (file.size === 0) {
        setError(`${file.name} 是空的`);
        continue;
      }
      if (file.size > MAX_ATTACHMENT_BYTES) {
        setError(`${file.name} 是 ${fmtSize(file.size)}，單檔上限 50 MB`);
        continue;
      }
      if (total + file.size > MAX_ATTACHMENTS_TOTAL_BYTES) {
        setError("附件合計超過 50 MB，這個加不進去");
        break;
      }
      if ((session?.size ?? 0) + total + file.size > MAX_JOB_INPUT_BYTES) {
        setError("這個 job 的輸入合計超過 100 MB（session 檔加附件）");
        break;
      }
      const name = uniqueName(file.name, taken);
      taken.add(name);
      total += file.size;
      queued.push({ file, name, original: file.name });
    }

    if (!queued.length) return;
    setUploading(true);
    try {
      // 一個一個傳並逐一寫進清單 —— 傳到一半失敗時，已經成功的那些要留著，
      // 不然使用者得把整批重挑一次。
      for (const q of queued) {
        const key = await api.uploadAttachment(q.file, q.name);
        setFiles((prev) => [...prev, { key, name: q.name, original: q.original, size: q.file.size }]);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "上傳失敗，請再試一次");
    } finally {
      setUploading(false);
    }
  }

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
        attachment_keys: files.map((f) => f.key),
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
      <p className="hint under-field">
        {session ? (
          <>
            ✅ 這是真的續跑 —— 出租者那邊會用 <code>--resume</code> 接上你這份 session，
            不是把對話當文字重讀一次。
          </>
        ) : (
          <>⚠️ 貼上的對話 Claude 讀得到文字，但讀不到當時執行過的指令與開啟的檔案。</>
        )}
      </p>

      {/* 順序照使用頻率排，不照技術正確性排。多數人（尤其 BD/PM）的動作就是
          貼上，所以貼上的實務建議放前面；「跑一行指令看有沒有 session 檔」是
          少數人的路，收進下面那顆按鈕旁邊的展開區。

          先前版本把那行 `ls -t` 當成所有人的第一步 —— 對不開終端機的人來說，
          第一個指示就是一個他做不到、而且做完會看到空的動作。路由正確，
          但把摩擦加在最需要簡單路徑的那群人身上。 */}
      {!session && (
        <div className="sources">
          <p>
            <em>不用整段複製</em> —— 貼最後幾輪、再寫一句要它接著做什麼，通常就夠了。
            貼越多讀越久、越貴，而那筆錢是要算進人情債的。
          </p>
          <p>這個 job 跑完之後用「接著問」，之後每一輪都是真的續跑。</p>
        </div>
      )}

      {/* 附件排在 .jsonl 上傳之前：對 Cowork / BD 使用者來說，要它處理的檔案
          才是主要動作，session 檔是少數人的路。 */}
      <div className="attach">
        <label className="session-pick">
          {uploading ? "上傳中…" : "加附件"}
          <input
            type="file"
            multiple
            disabled={uploading}
            onChange={(e) => {
              void pickAttachments(e.target.files);
              e.target.value = "";
            }}
          />
        </label>
        <span className="muted">
          要它處理的檔案。job 一開始就會在工作目錄裡看到它們 ——
          貼上的對話帶不動檔案，這裡才行。
        </span>

        {files.length > 0 && (
          <ul className="attach-list">
            {files.map((f) => (
              <li key={f.key}>
                <div className="attach-main">
                  <strong>{f.name}</strong>
                  <span className="muted"> · {fmtSize(f.size)}</span>
                  {f.name !== f.original && (
                    <div className="muted">
                      你選的是 {f.original} —— 已經有同名的，所以改成這個名字。
                      job 裡和產出清單上都會是 <code>{f.name}</code>。
                    </div>
                  )}
                </div>
                <button
                  type="button"
                  className="small"
                  onClick={() => setFiles((prev) => prev.filter((x) => x.key !== f.key))}
                >
                  移除
                </button>
              </li>
            ))}
          </ul>
        )}

        {files.length > 0 && (
          <p className="muted">
            跑完之後，「產出的檔案」只會列出<strong>新檔案</strong>與
            <strong>被改過的</strong>附件 —— 你原樣傳進去、它沒動的不會再回來一次。
          </p>
        )}
      </div>

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
            <span className="muted">
              在自己電腦上用 Claude Code 跑的話（終端機、IDE 擴充、Desktop 的 Code
              分頁，以及 Cowork 的 local session），上傳 session 檔是
              <strong>真的續跑</strong>。
            </span>
            {/* 「去 ~/.claude/projects/ 找」不是可執行的指示 —— 那底下一個專案
                一個資料夾，隨便一台機器就上百個 session 檔，而檔名是 session id，
                看不出內容。要給就要給到能貼進終端機的程度。 */}
            <details className="cmds findfile">
              <summary>我的對話有 session 檔嗎？</summary>
              <p className="muted">在終端機跑這行 —— 有列出東西就是有：</p>
              <pre>ls -t ~/.claude/projects/*/*.jsonl | head -5</pre>
              <p className="muted">
                剛跑到一半額度就沒了的話，通常就是最上面那個。
                什麼都沒有就用貼的 —— Chat、claude.ai、手機，以及 Cowork 的
                cloud session 都不留本機檔，貼上也幾乎不會少東西。
              </p>
              <p className="muted">
                一個專案一個資料夾，資料夾名稱是專案路徑把 <code>/</code> 換成{" "}
                <code>-</code>。檔名是 session id，看不出內容 ——
                挑那個專案底下時間最近的通常就對。
              </p>
              {/* Cowork 沒有「專案路徑」可以對，但它的 metadata 有標題。
                  照標題找出 cliSessionId，再拿去 projects/ 底下比對。 */}
              <p className="muted">
                Cowork 的對話沒有專案路徑可以對，改用標題找（macOS）：
              </p>
              <pre>{`grep -l '你對話標題的關鍵字' \
  ~/Library/Application\ Support/Claude/claude-code-sessions/*/*/local_*.json`}</pre>
              <p className="muted">
                找到的 <code>local_*.json</code> 裡有 <code>cliSessionId</code>，
                那就是 <code>~/.claude/projects/</code> 底下對應的檔名。
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
