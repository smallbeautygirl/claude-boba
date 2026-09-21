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
  borrower: string;
  lender: string | null;
  parent_job_id: string | null;
  can_follow_up: boolean;
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
  prompt: string;
  model: string;
  requested_worker_id?: string | null;
}

export interface Me {
  id: string;
  email: string;
  display_name: string;
}

export interface WorkerRow {
  id: string;
  name: string;
  owner: string;
  online: boolean;
  accepting: boolean;
  allow_full_network: boolean;
  available_models: string[];
  claude_code_version: string | null;
  quota: "green" | "yellow" | "red" | "unknown";
  job_budget_usd?: string;
  max_concurrency?: number;
}

export interface DebtRow {
  id: string;
  job_id: string;
  direction: "owe" | "owed";
  counterpart: string;
  amount_usd: string;
  tier: string;
  label: string;
  status: "open" | "nudged" | "settled";
  days: number;
}

export interface Ledger {
  i_owe: DebtRow[];
  owed_to_me: DebtRow[];
  settled: DebtRow[];
}

// token 存在 localStorage。Hub 每次都會跟 Observ 驗證（結果快取 60 秒），
// 所以這裡不需要自己處理過期 —— 過期就會拿到 401，前端導回登入頁。
const TOKEN_KEY = "boba.token";

export const tokenStore = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (t: string) => localStorage.setItem(TOKEN_KEY, t),
  clear: () => localStorage.removeItem(TOKEN_KEY),
};

export class Unauthorized extends Error {}

async function json<T>(res: Response): Promise<T> {
  if (res.status === 401) throw new Unauthorized("請重新登入");
  if (!res.ok) throw new Error(await friendly(res));
  return res.json() as Promise<T>;
}

// 錯誤訊息一律正經、可行動（web-spec §9）。Observ 的錯誤會被包成
// {detail: {detail: "..."}}，直接丟給使用者看沒有意義。
async function friendly(res: Response): Promise<string> {
  try {
    const body = await res.json();
    const d = body?.detail;
    const inner = typeof d === "object" && d !== null ? (d as { detail?: string }).detail : d;
    if (typeof inner === "string") return inner;
  } catch {
    /* 非 JSON，往下走 */
  }
  return `伺服器回應 ${res.status}`;
}

function authed(extra: HeadersInit = {}): HeadersInit {
  const token = tokenStore.get();
  return token ? { ...extra, Authorization: `Bearer ${token}` } : extra;
}

export const api = {
  login: async (email: string, password: string) => {
    const body = await fetch(`${HUB}/api/auth/login`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email, password }),
    }).then(json<{ token: string }>);
    tokenStore.set(body.token);
    return body.token;
  },

  me: () => fetch(`${HUB}/api/auth/me`, { headers: authed() }).then(json<Me>),

  createJob: (body: CreateJobInput) =>
    fetch(`${HUB}/api/jobs`, {
      method: "POST",
      headers: authed({ "content-type": "application/json" }),
      body: JSON.stringify(body),
    }).then(json<JobDetail>),

  followUp: (id: string, prompt: string) =>
    fetch(`${HUB}/api/jobs/${id}/follow-up`, {
      method: "POST",
      headers: authed({ "content-type": "application/json" }),
      body: JSON.stringify({ prompt }),
    }).then(json<JobDetail>),

  listJobs: () => fetch(`${HUB}/api/jobs`, { headers: authed() }).then(json<JobDetail[]>),

  getJob: (id: string) =>
    fetch(`${HUB}/api/jobs/${id}`, { headers: authed() }).then(json<JobDetail>),

  // Poll fallback。SSE 斷線時打這裡，資料與串流完全相同（web-spec §1）。
  getEvents: (id: string, fromSeq: number) =>
    fetch(`${HUB}/api/jobs/${id}/events?from_seq=${fromSeq}`, { headers: authed() }).then(
      json<StreamItem[]>,
    ),

  // EventSource 無法設自訂 header，所以 token 只能走 query string。
  // 這是 SSE 的已知限制，不是偷懶（Hub 因此不記錄 query string）。
  streamUrl: (id: string, fromSeq: number) =>
    `${HUB}/api/jobs/${id}/stream?from_seq=${fromSeq}&token=${encodeURIComponent(
      tokenStore.get() ?? "",
    )}`,

  listWorkers: () => fetch(`${HUB}/api/workers`, { headers: authed() }).then(json<WorkerRow[]>),

  createWorker: (name: string) =>
    fetch(`${HUB}/api/workers`, {
      method: "POST",
      headers: authed({ "content-type": "application/json" }),
      body: JSON.stringify({ name }),
    }).then(json<{ id: string; name: string; token: string }>),

  setAccepting: (id: string, accepting: boolean) =>
    fetch(`${HUB}/api/workers/${id}/accepting?accepting=${accepting}`, {
      method: "POST",
      headers: authed(),
    }).then(json<{ accepting: boolean }>),

  ledger: () => fetch(`${HUB}/api/ledger`, { headers: authed() }).then(json<Ledger>),

  settle: (id: string) =>
    fetch(`${HUB}/api/ledger/${id}/settle`, { method: "POST", headers: authed() }).then(
      json<{ status: string }>,
    ),

  nudge: (id: string) =>
    fetch(`${HUB}/api/ledger/${id}/nudge`, { method: "POST", headers: authed() }).then(
      json<{ status: string }>,
    ),
};
