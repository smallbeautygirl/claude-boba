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

// 失敗分類由後端決定（hub/app/failures.py），前端只負責渲染。
// 同一份判斷 Teams 通知也要用，放前端會有兩套。
export interface Failure {
  kind: "fixable" | "system" | "claude";
  title: string;
  hint: string | null;
  // 系統問題的 stderr 對使用者沒有意義，後端會把這個設成 false。
  show_detail: boolean;
  // 被 egress 白名單擋掉。之後要據此顯示〔改送給開放網路的 worker〕。
  blocked_by_network: boolean;
}

export interface JobDetail {
  id: string;
  status: JobStatus;
  borrower: string;
  lender: string | null;
  parent_job_id: string | null;
  can_follow_up: boolean;
  model: string;
  created_at: string;
  // 第一個事件抵達時才設。null = 還在排隊或還在開容器。
  started_at: string | null;
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
  failure: Failure | null;
  can_stop: boolean;
}

export interface StreamItem {
  seq: number;
  payload: Record<string, unknown>;
}

export interface CreateJobInput {
  prompt: string;
  model: string;
  requested_worker_id?: string | null;
  // 上傳的 Claude Code session 檔。有帶的話這個 job 走 `claude --resume`，
  // 是真的續跑，不是把對話當文字重貼一次。
  transcript_key?: string | null;
  // 要它處理的檔案。worker 放進工作目錄根層，Claude 一進去就看得到。
  attachment_keys?: string[];
}

export interface UploadTicket {
  key: string;
  put_url: string;
  max_bytes: number;
}

export interface Me {
  id: string;
  email: string;
  display_name: string;
  has_teams_webhook: boolean;
  /** 共用頻道的 webhook 有沒有在 hub 上設好。沒有的話，沒設個人 webhook 的人
      一則通知都收不到 —— 通知設定頁要靠這個才講得出實話。 */
  channel_notifications: boolean;
  /** 那個頻道叫什麼，原字串照顯示。 */
  channel_name: string;
  /** MinIO console 的網址。空字串代表這個站台沒開，「我的 worker」頁整段不顯示。
      從 hub 來而不是前端寫死：端點換了畫面要跟著變。 */
  s3_console_url: string;
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
  /** 這台跑過幾個 job。只有自己的 worker 有這個欄位 —— 別人幫誰跑過幾次
      不該出現在別人的畫面上。跑過就不能刪（那些紀錄的出租者會斷掉）。 */
  job_count?: number;
  job_budget_usd?: string;
  max_concurrency?: number;
}

/** 一位代跑者的出借條件。新的託管模型下他沒有機器，所以不是一份清單，
    就是一份設定（CONTEXT.md：出借設定）。
    **token 永遠不在這裡面** —— security.md 紅線 2，任何回應都不帶它。 */
export interface LendingSettings {
  has_token: boolean;
  budget_usd: string;
  available_models: string[];
  allow_full_network: boolean;
  accepting: boolean;
  online: boolean;
}

/** 站台的 model 白名單。順序即偏好順序，第一個是預設 —— 預設不是 Opus，
    因為委託者不會知道差別、會直接送出，而那等於每個 job 貴 2.5 倍（web-spec §3）。
    放在這裡而不是某一頁裡面：提交頁與出借設定頁都要用同一份，
    兩份會各自漂移。對應 hub 的 `schemas.SITE_MODELS`。 */
export const SITE_MODELS = [
  { value: "sonnet", label: "Sonnet（預設）" },
  { value: "haiku", label: "Haiku（最省）" },
];

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

export interface JobSummary {
  id: string;
  status: JobStatus;
  borrower: string;
  preview: string;
  is_follow_up: boolean;
  model: string;
  created_at: string;
  // 第一個事件抵達時才設。null = 還在排隊或還在開容器。
  started_at: string | null;
  finished_at: string | null;
  total_cost_usd: string | null;
}

export interface ArtifactRow {
  name: string;
  size_bytes: number;
  download_url: string;
}

export interface CommandGroup {
  id: string;
  title: string;
  audience: string;
  hint: string;
  commands: { name: string; label: string; desc: string }[];
}

export interface CommandCatalog {
  groups: CommandGroup[];
}

export interface Ledger {
  i_owe: DebtRow[];
  owed_to_me: DebtRow[];
  settled: DebtRow[];
}

// token 存在 localStorage。Hub 每次都會跟 Observ 驗證（結果快取 60 秒），
// 所以這裡不需要自己處理過期 —— 過期就會拿到 401，前端導回登入頁。
const TOKEN_KEY = "boba.token";

// localStorage 在無痕視窗與「封鎖網站資料」時是**存取就丟例外**，不是回 null。
// 沒有這幾個 try/catch 的話，AuthProvider 的 effect 會在第一次讀 token 時炸掉，
// 結果是整頁空白 —— 連登入頁都沒有，使用者看不出發生什麼事（實測 2026-09-22：
// body.innerText 是空字串、兩個未捕捉例外）。
//
// 失敗時退回記憶體，不是直接放棄：只包 try/catch 的話畫面救回來了，但 token
// 存不住，使用者每次重整都要重登。退回記憶體至少讓這個分頁能正常用完 ——
// 那正是「我額度爆了，很急」那一刻需要的。
let memoryToken: string | null = null;

export const tokenStore = {
  get: () => {
    try {
      return localStorage.getItem(TOKEN_KEY) ?? memoryToken;
    } catch {
      return memoryToken;
    }
  },
  set: (t: string) => {
    memoryToken = t;
    try {
      localStorage.setItem(TOKEN_KEY, t);
    } catch {
      // 記憶體那份已經寫好了，這個分頁照常運作。
    }
  },
  clear: () => {
    memoryToken = null;
    try {
      localStorage.removeItem(TOKEN_KEY);
    } catch {
      // 本來就沒存進去，沒有東西要清。
    }
  },
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

  setTeamsWebhook: (url: string) =>
    fetch(`${HUB}/api/auth/me/teams-webhook`, {
      method: "PUT",
      headers: authed({ "content-type": "application/json" }),
      body: JSON.stringify({ url }),
    }).then(json<{ has_teams_webhook: boolean }>),

  // 檔案直接 PUT 進 MinIO，不經過 Hub —— 產出檔案的下載已經是這個模式，
  // 50 MB 的 session 檔沒有理由在 Hub 的記憶體裡轉一手。
  uploadTranscript: async (file: File): Promise<string> => {
    const ticket = await fetch(`${HUB}/api/uploads/transcript`, {
      method: "POST",
      headers: authed(),
    }).then(json<UploadTicket>);

    const res = await fetch(ticket.put_url, { method: "PUT", body: file });
    if (!res.ok) throw new Error("上傳失敗，請再試一次");
    return ticket.key;
  },

  // 一次一個檔（契約：要傳多個就呼叫多次）。filename 由我們送 ——
  // 撞名的去重是**客戶端的責任**，worker 的序號只是防呆：它的改名是隱形的，
  // 使用者不會知道產出裡的 report-2.pdf 是怎麼來的。
  uploadAttachment: async (file: File, filename: string): Promise<string> => {
    const ticket = await fetch(`${HUB}/api/uploads/attachment`, {
      method: "POST",
      headers: authed({ "content-type": "application/json" }),
      body: JSON.stringify({ filename }),
    }).then(json<UploadTicket>);

    const res = await fetch(ticket.put_url, { method: "PUT", body: file });
    if (!res.ok) throw new Error(`${filename} 上傳失敗，請再試一次`);
    return ticket.key;
  },

  createJob: (body: CreateJobInput) =>
    fetch(`${HUB}/api/jobs`, {
      method: "POST",
      headers: authed({ "content-type": "application/json" }),
      body: JSON.stringify(body),
    }).then(json<JobDetail>),

  // 只有正在跑這個 job 的出租者能停。note 選填，但它才是重點 ——
  // 沒有那句話，停止會被讀成拒絕（web-spec §8）。
  stopJob: (id: string, note: string) =>
    fetch(`${HUB}/api/jobs/${id}/stop`, {
      method: "POST",
      headers: authed({ "content-type": "application/json" }),
      body: JSON.stringify({ note: note.trim() || null }),
    }).then(json<JobDetail>),

  followUp: (id: string, prompt: string, attachment_keys: string[] = []) =>
    fetch(`${HUB}/api/jobs/${id}/follow-up`, {
      method: "POST",
      headers: authed({ "content-type": "application/json" }),
      body: JSON.stringify({ prompt, attachment_keys }),
    }).then(json<JobDetail>),

  listJobs: () => fetch(`${HUB}/api/jobs`, { headers: authed() }).then(json<JobSummary[]>),

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

  commands: () => fetch(`${HUB}/api/commands`).then(json<CommandCatalog>),

  artifacts: (id: string) =>
    fetch(`${HUB}/api/jobs/${id}/artifacts`, { headers: authed() }).then(
      json<ArtifactRow[]>,
    ),

  // 提交頁的代跑者下拉。**不是 /api/workers** —— 那一支在託管模型下改成回
  // 「我的出借設定」單一物件了，清單搬到 /lenders。
  listWorkers: () =>
    fetch(`${HUB}/api/workers/lenders`, { headers: authed() }).then(json<WorkerRow[]>),

  createWorker: (name: string) =>
    fetch(`${HUB}/api/workers`, {
      method: "POST",
      headers: authed({ "content-type": "application/json" }),
      body: JSON.stringify({ name }),
    }).then(json<{ id: string; name: string; token: string }>),

  /** 過渡期：舊 hub 回一個 worker 陣列，新 hub 回一份出借設定（單一物件）。
      兩種都要接得住 —— 前後端不可能同一秒上線，而中間那段時間使用者還在用。
      A 的端點落地、舊路徑不再需要之後，這個分岔連同 MyWorker 的舊畫面一起刪。 */
  lending: () =>
    fetch(`${HUB}/api/workers`, { headers: authed() }).then(
      json<LendingSettings | WorkerRow[]>,
    ),

  updateLending: (patch: Partial<Omit<LendingSettings, "has_token" | "online">>) =>
    fetch(`${HUB}/api/workers/settings`, {
      method: "PUT",
      headers: authed({ "content-type": "application/json" }),
      body: JSON.stringify(patch),
    }).then(json<LendingSettings>),

  /** 開始授權。回的是要讓代跑者去點的網址 —— token 由 hub 自己取回，
      不經過瀏覽器，也不會出現在任何回應裡。 */
  startAuthorization: () =>
    fetch(`${HUB}/api/workers/authorize`, {
      method: "POST",
      headers: authed(),
    }).then(json<{ authorize_url: string }>),

  /** 把代跑者貼回來的**一次性授權碼**送給 hub。
      spike #9：`redirect_uri` 指向 platform.claude.com 而不是 localhost，
      所以那串碼沒辦法自動回到我們手上，一定要有人貼。
      貼的是用完即失效的碼，**不是一年期的 token** —— 兩者外洩的後果差很遠。 */
  submitAuthorizationCode: (code: string) =>
    fetch(`${HUB}/api/workers/authorize/code`, {
      method: "POST",
      headers: authed({ "content-type": "application/json" }),
      body: JSON.stringify({ code }),
    }).then(json<LendingSettings>),

  // 只有從來沒跑過 job 的 worker 刪得掉。擋下來時 hub 回 409，訊息會說
  // 跑過幾個 —— 直接顯示那句，不要自己另外編一句。
  deleteWorker: async (id: string) => {
    const res = await fetch(`${HUB}/api/workers/${id}`, {
      method: "DELETE",
      headers: authed(),
    });
    if (res.status === 401) throw new Unauthorized("請重新登入");
    if (!res.ok) throw new Error(await friendly(res));
  },

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
