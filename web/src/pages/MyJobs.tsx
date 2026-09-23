// 我的 job 列表。
//
// 沒有這頁，job 一旦離開詳情頁就等於消失 —— 這對「可以關掉分頁、跑完再回來」
// 的設計是致命的（docs/web-spec.md §4）。
//
// 只列自己送出的。列表顯示 prompt 摘要，不然一排 UUID 沒人認得出哪個是哪個。

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { TERMINAL, api, type JobStatus, type JobSummary } from "../api";
import { usd } from "../money";

const POLL_MS = 10000;

const LABEL: Record<JobStatus, string> = {
  queued: "排隊中",
  claimed: "已派出",
  running: "執行中",
  succeeded: "完成",
  failed: "失敗",
  timeout: "逾時",
  cancelled: "已中止",
  expired: "已作廢",
  over_budget: "超出預算",
};

export function MyJobs() {
  const [jobs, setJobs] = useState<JobSummary[] | null>(null);

  const load = useCallback(() => {
    api.listJobs().then(setJobs).catch(() => setJobs([]));
  }, []);

  useEffect(load, [load]);

  // 有還在跑的就持續更新，全部結束就停 —— 沒必要一直打。
  const busy = jobs?.some((j) => !TERMINAL.includes(j.status)) ?? false;
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [busy, load]);

  if (!jobs) return <div className="card">載入中…</div>;

  return (
    <div className="card">
      <h1>我的 job</h1>
      {jobs.length === 0 ? (
        <p className="lede">
          還沒丟過東西。<Link to="/">丟一個看看</Link>
        </p>
      ) : (
        jobs.map((j) => (
          <Link key={j.id} to={`/jobs/${j.id}`} className="jobrow">
            <div className="jobrow-main">
              <div className="jobrow-title">
                {j.is_follow_up && <span className="muted">↳ </span>}
                {j.preview || <span className="muted">（沒有內容）</span>}
              </div>
              <div className="muted">
                {ago(j.created_at)} · {j.model}
                {j.total_cost_usd && ` · ${usd(j.total_cost_usd)}`}
              </div>
            </div>
            <span className={`chip ${j.status}`}>{LABEL[j.status]}</span>
          </Link>
        ))
      )}
    </div>
  );
}

function ago(iso: string): string {
  const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 60) return "剛剛";
  if (secs < 3600) return `${Math.floor(secs / 60)} 分鐘前`;
  if (secs < 86400) return `${Math.floor(secs / 3600)} 小時前`;
  return `${Math.floor(secs / 86400)} 天前`;
}
