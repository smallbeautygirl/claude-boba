// Job 結束後的區塊：結果 / 失敗說明 / 出租者的停止鈕。
//
// 獨立成一個元件而不是塞進 JobDetail，是為了讓 UI 改版與後端功能可以各自
// 進行 —— 串接只需要在 JobDetail 裡放一行 <JobOutcome … />。
//
// 失敗的分類與文案都由後端決定（hub/app/failures.py）。這裡只渲染，
// 不自己判斷「這是哪一種錯」—— 那份判斷 Teams 通知也要用，放兩份會分岔。

import { useState } from "react";
import { api, type JobDetail as Job } from "./api";
import "./JobOutcome.css";

export function JobOutcome({ job, onChange }: { job: Job; onChange: (j: Job) => void }) {
  return (
    <>
      {job.can_stop && <StopPanel job={job} onChange={onChange} />}
      {job.status === "succeeded" ? <Success job={job} /> : job.failure && <Failed job={job} />}
      <VersionNote job={job} />
    </>
  );
}

function Success({ job }: { job: Job }) {
  return (
    <div className="outcome ok">
      <pre>{job.result_text}</pre>
      <p className="cost">
        花費 US${job.total_cost_usd} — {job.debt_label}
      </p>
    </div>
  );
}

function Failed({ job }: { job: Job }) {
  const f = job.failure!;
  return (
    <div className="outcome bad">
      <p className="error">
        <strong>{f.title}</strong>
      </p>

      {/* 出租者留的話。放在最前面 —— 沒有它，停止會被讀成拒絕。 */}
      {job.stop_note && <blockquote className="stop-note">{job.stop_note}</blockquote>}

      {f.hint && <p className="hint">{f.hint}</p>}

      {f.blocked_by_network && (
        <p className="hint">
          {/* 〔改送〕按鈕還沒做 —— 目前只有一台 worker，按了沒地方送。
              後端的 blocked_by_network 已經備好，有第二台再接。 */}
          目前只有一台 worker，還不能改送給別人。
        </p>
      )}

      {/* 系統問題的 stderr 對使用者沒有意義，也幫不上忙 —— 後端會關掉它。 */}
      {f.show_detail && job.error_detail && <pre>{job.error_detail}</pre>}

      <p className="muted">這次不計債。</p>
    </div>
  );
}

function StopPanel({ job, onChange }: { job: Job; onChange: (j: Job) => void }) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function stop() {
    setBusy(true);
    setError(null);
    try {
      onChange(await api.stopJob(job.id, note));
    } catch (e) {
      setError(e instanceof Error ? e.message : "中止失敗");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stop-panel">
      <label>
        中止這個 job（你是出租者）
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          maxLength={500}
          placeholder="留一句話，例如：這個看起來會跑很久，我等下要開會，晚點再幫你跑"
        />
      </label>
      {/* 那句話是選填的，但沒有它，對方收到的只會是「你的 job 被終止了」。 */}
      <p className="hint">不填也可以，但對方只會看到「你的 job 被中止了」。</p>
      {error && <p className="error">{error}</p>}
      <button type="button" className="danger" onClick={stop} disabled={busy}>
        {busy ? "中止中…" : "中止"}
      </button>
    </div>
  );
}

// 版本不符只警告、不擋下 —— 跨 81 版的 resume 實測雙向都可行（SPEC §11 spike 3）。
function VersionNote({ job }: { job: Job }) {
  const { borrower_cli_version: a, lender_cli_version: b } = job;
  if (!a || !b || a === b) return null;
  return (
    <p className="warn">
      ⚠️ 你的 Claude Code 是 {a}，出租者是 {b}。實測跨版本可行，但若結果怪怪的，這可能是原因。
    </p>
  );
}
