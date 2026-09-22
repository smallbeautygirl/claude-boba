// Job 詳情：即時串流 + 結果。
//
// 心智模型是「可以離開」（web-spec §4）：job 可能跑十分鐘，明講可以關掉分頁、
// 跑完會通知，等待感就消失了。回來時 Hub 會先重播歷史事件再接上即時流。

import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Composer } from "../Composer";
import { JobOutcome } from "../JobOutcome";
import {
  TERMINAL,
  api,
  type ArtifactRow,
  type JobDetail as Job,
  type StreamItem,
} from "../api";
import { describe, toLine, type Line } from "../events";

const POLL_MS = 5000;

export function JobDetail() {
  const { id = "" } = useParams();
  const [job, setJob] = useState<Job | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  const [, tick] = useState(0);
  const [live, setLive] = useState(true);
  const seqRef = useRef(0);

  const done = job ? TERMINAL.includes(job.status) : false;

  // 時間由瀏覽器自己算，不需要伺服器推 —— 執行中唯一會變的就是時間，
  // 為此推送事件是浪費（SPEC §7）。這裡只負責每秒重畫一次，起點與標籤
  // 由 phaseOf() 依狀態決定。
  useEffect(() => {
    if (done) return;
    const t = setInterval(() => tick((n) => n + 1), 1000);
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

    api
      .getJob(id)
      .then((j) => {
        if (cancelled) return;
        setJob(j);
      })
      // 沒有這個 catch，任何載入失敗都會永遠停在「載入中…」——
      // 使用者看不出是壞了還是慢。
      .catch((e: Error) => {
        if (!cancelled) setLoadError(e.message);
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

  // 這一頁的標題再加上狀態。基準標題由 App 的 useDocumentTitle 依路徑設定，
  // 這裡只覆寫掉它 —— 離開這一頁時路徑會變，那邊會自己蓋回去，不需要 cleanup。
  //
  // 「可以離開」的心智模型（web-spec §4）靠 Teams 通知撐著，但開著的分頁本身是更便宜
  // 的一條：跑完時分頁上的字自己會變，不用切回來看。狀態放最前面，因為分頁很窄，
  // 被截掉的一定是後面。
  useEffect(() => {
    if (!job) return;
    const status = `${MARK[job.status] ?? "⏳"} ${STATUS_LABEL[job.status]}`;
    document.title = `${status} · job ${job.id.slice(0, 8)} · claude-boba`;
  }, [job]);

  if (loadError) {
    return (
      <div className="card">
        <Link className="back" to="/jobs">
          ← 我的 job
        </Link>
        <p className="error">這個 job 載入失敗：{loadError}</p>
      </div>
    );
  }
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

      <Phase job={job} live={live} done={done} />

      <div className="stream">
        {lines.map((line, i) => (
          <LineView key={i} line={line} />
        ))}
        {!done && lines.length === 0 && <div className="muted">排隊中…</div>}
      </div>

      <JobOutcome job={job} onChange={setJob} />
      {done && <Artifacts jobId={job.id} />}
      {job.can_follow_up && <FollowUp jobId={job.id} />}
      {done && <WishNudge />}
    </div>
  );
}

/* 許願板的入口（web-spec §12）。**放在這裡是刻意的** —— 痛感發生的那一秒，
   人還在現場。§10 拒絕「獨立的新手說明頁」的理由（沒人會主動點進去）直接適用
   於那面牆，這個入口是它不變成死牆的關鍵。

   ⚠️ **不要幫使用者帶上 job id 或錯誤訊息。** 這個位置會非常自然地誘導出那個
   設計，而那一步就是 SPEC §4.3 的破口：一則帶著 prompt 片段、署名、永久的
   貼文。要知道是哪個 job，去問他 —— 那正是許願板強制署名的理由。

   鷹架，跟著許願板一起拆。 */
function WishNudge() {
  return (
    <p className="hint">
      剛剛哪裡卡卡的？<Link to="/wishes">寫去許願板</Link> —— 這東西還在試玩，
      你的抱怨比讚美有用。
    </p>
  );
}

function Phase({ job, live, done }: { job: Job; live: boolean; done: boolean }) {
  const phase = phaseOf(job);
  if (!phase) return null;
  return (
    <p className="hint">
      ⏱ {phase.label}
      {phase.since && ` ${fmt(secondsSince(phase.since))}`}
      {!done && !live && " · 即時連線中斷，改用輪詢"}
      {!done && (
        <>
          <br />
          可以關掉這頁，跑完會用 Teams 通知你。
        </>
      )}
    </p>
  );
}

// 產出檔案。下載連結是短效期的預簽 URL，每次載入這一頁才現開 ——
// 它本身就是憑證，不該被存起來或轉貼。
function Artifacts({ jobId }: { jobId: string }) {
  const [files, setFiles] = useState<ArtifactRow[]>([]);

  useEffect(() => {
    api.artifacts(jobId).then(setFiles).catch(() => setFiles([]));
  }, [jobId]);

  if (files.length === 0) return null;
  return (
    <div className="artifacts">
      <h2>產出的檔案</h2>
      {files.map((f) => (
        <div key={f.name} className="debt">
          <div>
            <div>{f.name}</div>
            <div className="muted">{fmtSize(f.size_bytes)}</div>
          </div>
          <a className="chip-btn" href={f.download_url} download>
            下載
          </a>
        </div>
      ))}
    </div>
  );
}

function fmtSize(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

// 接著問：帶著上一個 job 的 transcript 真的 resume，不是把對話重貼一次
// （跨機器 resume 已於 SPEC §11 spike #2 驗證可行）。
function FollowUp({ jobId }: { jobId: string }) {
  const navigate = useNavigate();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);

  async function send(attachmentKeys: string[]) {
    setBusy(true);
    try {
      const next = await api.followUp(jobId, text, attachmentKeys);
      setText("");
      navigate(`/jobs/${next.id}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="followup">
      <h2>接著問</h2>
      {/* 沒有第二個隱私勾選。同意是針對這條 job 鏈給的，每一輪再問一次只會
          把同意變成反射性點擊，那正好摧毀 §9 想保住的東西。

          但有一行可見的提醒，而且講的是**接著問特有**的那件事：這一輪不一定
          回到原本那個出租者。follow_up() 的 requested_worker_id 是 None
          （hub/app/routers/jobs.py）—— transcript 在 MinIO 上，任何 worker 都
          拿得到，原本那台離線時不該讓使用者卡住。提交頁的勾選點名了一個人，
          而這一輪可能是別人。 */}
      <p className="hint">
        ⚠️ 這一輪會派給當下有空的代跑者，<strong>不一定是剛才那一位</strong>
        —— 你接下來寫的內容與附件，那個人技術上一樣看得到。
      </p>
      <Composer
        value={text}
        onChange={setText}
        onSubmit={send}
        busy={busy}
        blockedReason={text.trim() ? null : "先寫點東西"}
        label="接著問"
        placeholder="沿用上面的對話繼續問…"
        rows={4}
      />
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




const STATUS_LABEL: Record<Job["status"], string> = {
  queued: "排隊中",
  claimed: "已派給代跑者",
  running: "執行中",
  succeeded: "完成",
  failed: "失敗",
  timeout: "逾時",
  cancelled: "已中止",
  expired: "已作廢",
  over_budget: "超出預算",
};

// 失敗的幾種狀態共用 ⚠️：分頁標籤只有幾個字的寬度，分那麼細沒有意義，
// 「要不要切回來看」這個決定只需要知道成功或不成功。
const MARK: Partial<Record<Job["status"], string>> = {
  succeeded: "✅",
  failed: "⚠️",
  timeout: "⚠️",
  cancelled: "⚠️",
  expired: "⚠️",
  over_budget: "⚠️",
};

function StatusChip({ status }: { status: Job["status"] }) {
  return <span className={`chip ${status}`}>{STATUS_LABEL[status]}</span>;
}

// 一個標籤講不了三件事。
//
// 以前不論哪個階段都寫「⏱ 已執行 N」，而且從 created_at 起算 —— 排隊 10 分鐘、
// 實跑 30 秒的 job 會說「已執行 10 分 30 秒」。債務是照實際花費算的，所以畫面
// 上的時間跟使用者欠的錢對不起來。
//
// 排隊那段刻意不藏：等了十分鐘還沒人接，跟跑了十分鐘，是兩種完全不同的處境
// —— 前者該去敲人，後者該去泡茶。
//
// claimed 只寫「準備中」不帶秒數：那段容器在 docker run、在複製 org skill
// 模板，出租者的機器確實在工作，但 started_at 還沒設。與其算一個語意不明的
// 數字，不如說清楚它在做什麼。
function phaseOf(job: Job): { label: string; since: string | null } | null {
  if (job.status === "queued") return { label: "排隊中", since: job.created_at };
  if (job.status === "claimed") return { label: "準備中 · 代跑者的 job 容器正在啟動", since: null };
  if (job.status === "running") return { label: "已執行", since: job.started_at };
  // 終態：有 started_at 才講得出「實際跑了多久」。
  if (job.started_at && job.finished_at) {
    const sec = Math.floor(
      (new Date(job.finished_at).getTime() - new Date(job.started_at).getTime()) / 1000,
    );
    return { label: `總共執行 ${fmt(Math.max(0, sec))}`, since: null };
  }
  return null;
}

function secondsSince(iso: string): number {
  return Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
}

function fmt(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return m ? `${m} 分 ${String(s).padStart(2, "0")} 秒` : `${s} 秒`;
}
