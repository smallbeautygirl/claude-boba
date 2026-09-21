// Hub 的型別化 client。契約見 ../../SPEC.md §9。

export const HUB = import.meta.env.VITE_HUB_URL ?? "http://127.0.0.1:8787";

export type JobStatus =
  | "queued"
  | "claimed"
  | "running"
  | "succeeded"
  | "failed"
  | "timeout"
  | "cancelled"
  | "expired"
  | "over_budget";

export const TERMINAL: readonly JobStatus[] = [
  "succeeded",
  "failed",
  "timeout",
  "cancelled",
  "expired",
  "over_budget",
];

export interface JobDetail {
  id: string;
  status: JobStatus;
  borrower_label: string;
  model: string;
  created_at: string;
  finished_at: string | null;
  total_cost_usd: string | null;
  prompt: string;
  source_type: "paste" | "transcript";
  worker_id: string | null;
  result_text: string | null;
  error_kind: string | null;
  error_detail: string | null;
  stop_note: string | null;
  lender_cli_version: string | null;
  borrower_cli_version: string | null;
  debt_label: string | null;
}

export interface StreamItem {
  seq: number;
  payload: Record<string, unknown>;
}

export interface CreateJobInput {
  borrower_label: string;
  prompt: string;
  model: string;
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json() as Promise<T>;
}

export const api = {
  createJob: (body: CreateJobInput) =>
    fetch(`${HUB}/api/jobs`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<JobDetail>),

  getJob: (id: string) => fetch(`${HUB}/api/jobs/${id}`).then(json<JobDetail>),

  // Poll fallback。SSE 斷線時打這裡，資料與串流完全相同（web-spec §1）。
  getEvents: (id: string, fromSeq: number) =>
    fetch(`${HUB}/api/jobs/${id}/events?from_seq=${fromSeq}`).then(json<StreamItem[]>),

  streamUrl: (id: string, fromSeq: number) =>
    `${HUB}/api/jobs/${id}/stream?from_seq=${fromSeq}`,
};
