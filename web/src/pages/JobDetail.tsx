// Job 詳情：即時串流 + 結果。
//
// 心智模型是「可以離開」（web-spec §4）：job 可能跑十分鐘，明講可以關掉分頁、
// 跑完會通知，等待感就消失了。回來時 Hub 會先重播歷史事件再接上即時流。

import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { TERMINAL, api, type JobDetail as Job, type StreamItem } from "../api";
import { describe, toLine, type Line } from "../events";

const POLL_MS = 5000;

export function JobDetail() {
  const { id = "" } = useParams();
  const [job, setJob] = useState<Job | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  const [elapsed, setElapsed] = useState(0);
  const [live, setLive] = useState(true);
  const seqRef = useRef(0);
  const startRef = useRef(Date.now());

  const done = job ? TERMINAL.includes(job.status) : false;

  // 「已執行 N 秒」由瀏覽器自己算，不需要伺服器推 ——
  // 執行中唯一會變的就是時間，為此推送事件是浪費（SPEC §7）。
  useEffect(() => {
    if (done) return;
    const t = setInterval(() => setElapsed(Math.floor((Date.now() - startRef.current) / 1000)), 1000);
    return () => clearInterval(t);
  }, [done]);

  useEffect(() => {
    let cancelled = false;
    const absorb = (items: StreamItem[]) => {
      const next: Line[] = [];
      for (const item of items) {
        if (item.seq > seqRef.current) seqRef.current = item.seq;
        const line = toLine(item);
        if (line) next.push(line);
      }
      if (next.length) setLines((prev) => [...prev, ...next]);
    };

    api.getJob(id).then((j) => {
      if (cancelled) return;
      setJob(j);
      startRef.current = new Date(j.created_at).getTime();
    });

    const source = new EventSource(api.streamUrl(id, 0));
    source.onmessage = (e) => absorb([JSON.parse(e.data) as StreamItem]);
    source.onerror = () => {
      // SSE 斷了就退回 poll。本來就只有幾個事件，使用者不該察覺差別（web-spec §1）。
      source.close();
      setLive(false);
    };
    return () => {
      cancelled = true;
      source.close();
    };
  }, [id]);

  // 輪詢 job 狀態；SSE 掉線時也負責補事件。
  useEffect(() => {
    if (done) return;
    const t = setInterval(async () => {
      const j = await api.getJob(id);
      setJob(j);
      if (!live) {
        const items = await api.getEvents(id, seqRef.current + 1);
        for (const item of items) if (item.seq > seqRef.current) seqRef.current = item.seq;
        const mapped = items.map(toLine).filter((l): l is Line => l !== null);
        if (mapped.length) setLines((prev) => [...prev, ...mapped]);
      }
    }, POLL_MS);
    return () => clearInterval(t);
  }, [id, done, live]);

  if (!job) return <div className="card">載入中…</div>;

  return (
    <div className="card">
      <Link className="back" to="/">
        ← 再丟一個
      </Link>
      <h1>Job {job.id.slice(0, 8)}</h1>
      {job.parent_job_id && (
        <p className="hint">
          接續自 <Link to={`/jobs/${job.parent_job_id}`}>{job.parent_job_id.slice(0, 8)}</Link>
        </p>
      )}
      <p className="lede">
        {job.borrower} · {job.model}
        {job.lender && ` · 由 ${job.lender} 代跑`} · <StatusChip status={job.status} />
      </p>

      {!done && (
        <p className="hint">
          ⏱ 已執行 {fmt(elapsed)}
          {!live && " · 即時連線中斷，改用輪詢"}
          <br />
          可以關掉這頁，跑完會用 Teams 通知你。
        </p>
      )}

      <div className="stream">
        {lines.map((line, i) => (
          <LineView key={i} line={line} />
        ))}
        {!done && lines.length === 0 && <div className="muted">排隊中…</div>}
      </div>

      {done && <Outcome job={job} />}
      {job.can_follow_up && <FollowUp jobId={job.id} />}
    </div>
  );
}

// 接著問：帶著上一個 job 的 transcript 真的 resume，不是把對話重貼一次
// （跨機器 resume 已於 SPEC §11 spike #2 驗證可行）。
function FollowUp({ jobId }: { jobId: string }) {
  const navigate = useNavigate();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send() {
    setBusy(true);
    setError(null);
    try {
      const next = await api.followUp(jobId, text);
      navigate(`/jobs/${next.id}`);
      setText("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "送出失敗");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="followup">
      <label>
        接著問
        <textarea
          rows={3}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="沿用上面的對話繼續問…"
        />
      </label>
      {error && <p className="error">{error}</p>}
      <button onClick={send} disabled={busy || !text.trim()}>
        {busy ? "送出中…" : "送出"}
      </button>
    </div>
  );
}

function LineView({ line }: { line: Line }) {
  if (line.kind === "tool") return <div className="ev tool">{describe(line)}</div>;
  if (line.kind === "thinking")
    return (
      <details className="ev think">
        <summary>💭 思考中…</summary>
        <p className="muted">內容未串流。</p>
      </details>
    );
  if (line.kind === "text") return <div className="ev text">{line.text}</div>;
  return null;
}

function Outcome({ job }: { job: Job }) {
  if (job.status === "succeeded") {
    return (
      <div className="outcome ok">
        <pre>{job.result_text}</pre>
        <p className="cost">
          花費 US${job.total_cost_usd} — {job.debt_label}
        </p>
        <VersionNote job={job} />
      </div>
    );
  }
  // 失敗一律不計債（SPEC §5），而且訊息要正經、要有下一步。
  return (
    <div className="outcome bad">
      <p className="error">
        <strong>{FAIL_TITLE[job.status] ?? "執行失敗"}</strong>
      </p>
      {job.stop_note && <p className="note">「{job.stop_note}」</p>}
      {job.error_detail && <pre>{job.error_detail}</pre>}
      <p className="muted">這次不計債。</p>
      <VersionNote job={job} />
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

const FAIL_TITLE: Partial<Record<Job["status"], string>> = {
  failed: "執行失敗",
  timeout: "超過時間上限，已中止",
  cancelled: "出租者中止了這個 job",
  over_budget: "超過花費上限，已中止",
  expired: "目前沒人有空，這個 job 已作廢",
};

const STATUS_LABEL: Record<Job["status"], string> = {
  queued: "排隊中",
  claimed: "已派給出租者",
  running: "執行中",
  succeeded: "完成",
  failed: "失敗",
  timeout: "逾時",
  cancelled: "已中止",
  expired: "已作廢",
  over_budget: "超出預算",
};

function StatusChip({ status }: { status: Job["status"] }) {
  return <span className={`chip ${status}`}>{STATUS_LABEL[status]}</span>;
}

function fmt(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return m ? `${m} 分 ${String(s).padStart(2, "0")} 秒` : `${s} 秒`;
}
