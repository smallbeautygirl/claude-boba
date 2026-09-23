// 輸入列（CONTEXT.md）。提交頁與「接著問」共用。
//
// 刻意不叫「對話框」—— 這裡不是對話，是一次性的提交，送出去會花別人的錢、
// 跑在別人的機器上、記一筆人情債。
//
// 抽成共用元件而不是各寫一份：使用者的要求就是「接著問的輸入框要跟丟 job
// 一樣」，兩份一定會走樣。附件的驗證（四道上限、撞名去重）尤其不能有兩套
// —— 那是安全與計費相關的規則。

import { useState, type ReactNode } from "react";
import { CommandChips, CommandPicker } from "./CommandPicker";
import { api } from "./api";
import "./Composer.css";

// 跟 hub 的 schemas.py 對齊。前端擋一次是為了不要讓人傳三分鐘才說太大；
// 後端仍然會擋，前端的檢查不算數。
const MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024;
const MAX_ATTACHMENTS_TOTAL_BYTES = 50 * 1024 * 1024;
const MAX_JOB_INPUT_BYTES = 100 * 1024 * 1024;
const MAX_ATTACHMENTS = 20;
// 貼進 prompt 的網址。容器打不開它們，送出前要講（見 Composer 裡那段註解）。
const LINK = /https?:\/\/[^\s)]+/i;
const ARTIFACT = /claude\.ai\/(code\/)?artifact\//i;

export interface Attachment {
  key: string;
  name: string;
  // 使用者挑的原始檔名。跟 name 不同就表示撞名被改過，要講出來。
  original: string;
  size: number;
}

export function fmtSize(n: number): string {
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

// 撞名就加序號，規則跟 worker 一致（`a.pdf` → `a-2.pdf`）。
//
// worker 也有一份同樣的迴圈，但那是防呆不是功能 —— 它的改名是**隱形的**：
// 使用者從兩個資料夾各挑一個 report.pdf，產出清單裡冒出一個 report-2.pdf，
// 而他從頭到尾沒看過這個名字。在這裡做，他挑完檔的當下就看到最終檔名。
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

export function Composer({
  value,
  onChange,
  onSubmit,
  busy,
  blockedReason,
  label,
  placeholder,
  submitLabel = "送出",
  rows = 8,
  // 這個 job 已經佔掉的輸入額度（例如上傳的 session 檔、或接著問要續跑的
  // 那份 transcript）。100 MB 是**整個 job 的輸入合計**，不是附件的合計。
  usedBytes = 0,
  note,
}: {
  value: string;
  onChange: (next: string) => void;
  onSubmit: (attachmentKeys: string[]) => Promise<void> | void;
  busy: boolean;
  // null = 可以送出。非 null 就是不能送的原因，會顯示在送出鍵旁邊 ——
  // 一顆停用又不說原因的按鈕，使用者只會盯著它。
  blockedReason: string | null;
  label: string;
  placeholder: string;
  submitLabel?: string;
  rows?: number;
  usedBytes?: number;
  note?: ReactNode;
}) {
  const [files, setFiles] = useState<Attachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [showCommands, setShowCommands] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ready = !blockedReason && !busy && !uploading;

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
      if (usedBytes + total + file.size > MAX_JOB_INPUT_BYTES) {
        setError("這個 job 的輸入合計超過 100 MB");
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
        setFiles((prev) => [
          ...prev,
          { key, name: q.name, original: q.original, size: q.file.size },
        ]);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "上傳失敗，請再試一次");
    } finally {
      setUploading(false);
    }
  }

  async function submit() {
    if (!ready) return;
    setError(null);
    try {
      await onSubmit(files.map((f) => f.key));
    } catch (err) {
      setError(err instanceof Error ? err.message : "送出失敗，請再試一次");
    }
  }

  return (
    <div className="composer">
      <label className="composer-field">
        <span className="sr-only">{label}</span>
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          rows={rows}
          placeholder={placeholder}
        />
      </label>

      {/* 空的時候才出現，一打字就收。它教的不只是「有哪些指令」，
          還有 `/` 這個手勢本身 —— 點下去文字框開頭就會出現那個指令。 */}
      <CommandChips value={value} onChange={onChange} />

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

      {/* 錯誤貼著送出鍵。留在頁尾的話，按了送出失敗的人根本看不到它。 */}
      {/* 容器的網路是白名單（security.md 紅線 3），只通得到 api.anthropic.com 與站台儲存 ——
          任何貼進來的連結它都打不開，Claude 會回「我沒辦法開這個網址」然後結束，
          job 照樣計費。2026-09-23 真的發生過：貼一個 claude.ai/artifact 連結要它做簡報，
          US$0.10 換來一句「請把內容貼進來」。
          只警告不阻擋：RD 把網址當參考文字貼進 prompt 是合理的。 */}
      {LINK.test(value) && (
        <p className="warn composer-error">
          這個 job 跑在沒有網路的容器裡，<strong>貼進來的連結它打不開</strong>
          （claude.ai、Google Docs、GitHub 都一樣）。把內容貼進來，或存成檔案用 + 附上。
          {/* artifact 是 PM/BD 最常見的來源，所以直接教怎麼拿：左上角標題的下拉選單。
              Copy as Markdown 比 Download 好 —— 連檔案都不用，而且會連同頁面上
              現場追加的內容一起帶出來（那些存在 db 裡，抓 HTML 是拿不到的）。 */}
          {ARTIFACT.test(value) && (
            <>
              {" "}
              claude.ai 的 artifact：點<strong>左上角的標題</strong> →{" "}
              <strong>Copy as Markdown</strong> 貼進來；或 Export → Download 後用 + 附上。
            </>
          )}
        </p>
      )}
      {error && <p className="error composer-error">{error}</p>}

      <div className="composer-bar">
        <label className="icon-btn" title="加附件">
          <span aria-hidden="true">+</span>
          <span className="sr-only">加附件</span>
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
        <button
          type="button"
          className={showCommands ? "icon-btn on" : "icon-btn"}
          aria-expanded={showCommands}
          title="可以用的指令"
          onClick={() => setShowCommands((v) => !v)}
        >
          <span aria-hidden="true">/</span>
          <span className="sr-only">可以用的指令</span>
        </button>
        <span className="composer-spacer" />
        {blockedReason && !busy && (
          <span className="muted composer-why">{blockedReason}</span>
        )}
        <button type="button" onClick={() => void submit()} disabled={!ready}>
          {busy ? "送出中…" : uploading ? "上傳中…" : submitLabel}
        </button>
      </div>

      {showCommands && (
        <div className="composer-panel">
          <CommandPicker value={value} onChange={onChange} bare />
        </div>
      )}

      {files.length > 0 && (
        <p className="muted">
          跑完之後，「產出的檔案」只會列出<strong>新檔案</strong>與
          <strong>被改過的</strong>附件 —— 你原樣傳進去、它沒動的不會再回來一次。
        </p>
      )}
      {note}
    </div>
  );
}
