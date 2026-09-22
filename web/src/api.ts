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
  lending_id: string | null;
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
  /** 指定代跑者（出借設定 id），不是帳號 —— 委託者看不到帳號的存在。 */
  requested_lending_id?: string | null;
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
  /** 站台管理者（hub 的 ADMIN_EMAILS）。管理分頁只對他渲染，但**後端每一支
      也都自己擋** —— 不渲染不等於不能呼叫。 */
  is_admin: boolean;
  /** MinIO console 的網址。**hub 只回給管理者**，其他人拿到空字串 ——
      那個 console 看得到所有人的檔案，靠前端不渲染擋不住（打開開發者工具就繞過了）。
      從 hub 來而不是前端寫死：端點換了畫面要跟著變。 */
  s3_console_url: string;
}

/** 提交頁下拉的一列：**一位代跑者**，不是一個帳號。

    他底下可能有兩個 Claude 帳號（個人 + 公司），但那對委託者完全不可見 ——
    他挑的是人，hub 自己決定用哪個帳號跑（ADR-0001）。`id` 是出借設定 id。 */
export interface LenderRow {
  id: string;
  name: string;
  owner: string;
  online: boolean;
  accepting: boolean;
  allow_full_network: boolean;
  available_models: string[];
  claude_code_version: string | null;
  /** 這個人的**池子**現在有多滿 —— 取他最充裕的那個帳號，不是平均。
      只有燈號，沒有百分比（web-spec §3）：精確數字會讓人盤算
      「他還有 66%，再送一個沒差」。 */
  quota: "green" | "yellow" | "red" | "unknown";
  /** 這個人跑過幾個 job。只有自己那一列有 —— 別人幫誰跑過幾次
      不該出現在別人的畫面上。 */
  job_count?: number;
  job_budget_usd?: string;
  /** 只有自己那一列有。 */
  accounts?: LendingAccount[];
}

/** 一個**出借帳號**：被借出去的那個 Claude 帳號本身。

    只出現在代跑者自己的「我來代跑」頁。**token 永遠不在這裡面**，
    連遮罩後的都沒有（security.md 紅線 2）。 */
export interface LendingAccount {
  id: string;
  name: string;
  has_token: boolean;
  /** token 失效（到期／被撤銷／帳號被收回）。**只有這個帳號停接單，
      其他照跑** —— job 不受影響，另一個帳號會接。 */
  needs_reauth: boolean;
  /** 「這是誰的額度／誰批准的」。公司帳號要填（SPEC §4.12）。 */
  approver_note: string | null;
  /** `rate_limit_event` 的整包 unifiedWindows。key 是窗名（five_hour、
      seven_day、還有 Anthropic 之後可能再加的），值含 utilization 與 resetsAt。
      不寫死窗名 —— 「顯示哪些窗」是這裡的決定，不是一次 migration。 */
  windows: Record<string, { utilization?: number; resetsAt?: string | number }>;
  quota_updated_at: string | null;
  /** 這包數字還算不算「現在」。false 就不要顯示百分比 —— 代跑者自己在別的
      地方也在燒同一個帳號，站台不會知道，掛一個理直氣壯的 34% 比不顯示更糟。 */
  quota_fresh: boolean;
}

/** 一位代跑者的出借條件。新的託管模型下他沒有機器，所以不是一份清單，
    就是一份設定（CONTEXT.md：出借設定）。
    **token 永遠不在這裡面** —— security.md 紅線 2，任何回應都不帶它。 */
export interface LendingSettings {
  /** 底下**任何一個**帳號可用就是 true。 */
  has_token: boolean;
  budget_usd: string;
  available_models: string[];
  allow_full_network: boolean;
  accepting: boolean;
  /** 領單主機在不在。託管模型下 job 全跑在同一台機器上 ——
      那台沒起來，誰的額度都借不出去。 */
  online: boolean;
  /** 他的出借帳號。一個的時候也是一列 —— 那一列不是為了多帳號而存在，
      它是放用量的地方（web-spec §8）。 */
  accounts: LendingAccount[];
}

/** 站台的 model 白名單。順序即偏好順序，第一個是預設 —— 預設不是 Opus，
    因為委託者不會知道差別、會直接送出，而那等於每個 job 貴 2.5 倍（web-spec §3）。
    放在這裡而不是某一頁裡面：提交頁與出借設定頁都要用同一份，
    兩份會各自漂移。對應 hub 的 `schemas.SITE_MODELS`。 */
export const SITE_MODELS = [
  { value: "sonnet", label: "Sonnet（預設）" },
  { value: "haiku", label: "Haiku（最省）" },
];

/** 管理頁的系統狀態。三種分類刻意分開，因為可信度不同：
    `checks` 是這次真的量到的、`facts` 是讀得出來但不是健康檢查、
    `unknown` 是從 hub 檢查不到的東西（連同原因一起顯示，不放假燈）。 */
export interface AdminHealth {
  checks: { key: string; label: string; ok: boolean; detail: string }[];
  facts: { label: string; value: string; note: string }[];
  unknown: { label: string; why: string }[];
}

export interface AdminStats {
  users: number;
  jobs_total: number;
  jobs_by_status: Record<string, number>;
  /** 分母只算跑完的（成功 + 失敗）。沒有跑完的 job 時是 null。 */
  success_rate: number | null;
  spend_usd: string;
}

export interface StuckJob {
  id: string;
  status: string;
  stuck_seconds: number;
  worker: string | null;
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
  // chip：文字框空著時直接露在框內的那幾個（docs/web-spec.md §3）。
  // 由 hub 的 commands.json 標記 —— 露哪幾個是編輯判斷，不是前端的事。
  commands: { name: string; label: string; desc: string; chip?: boolean }[];
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
  // 提交頁的代跑者下拉。一列一個**人**。
  listLenders: () =>
    fetch(`${HUB}/api/workers/lenders`, { headers: authed() }).then(json<LenderRow[]>),

  lending: () =>
    fetch(`${HUB}/api/workers/settings`, { headers: authed() }).then(
      json<LendingSettings>,
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
  /** 不帶 accountId = 新增一個出借帳號；帶了 = **換掉**那個帳號的 token。
      換掉這件事要在按鈕旁講清楚（舊的站台不再用，但它在 Claude 那邊
      仍然有效到期滿）—— 不擋，但不能無聲。 */
  submitAuthorizationCode: (
    code: string,
    opts?: { accountId?: string; approverNote?: string },
  ) =>
    fetch(`${HUB}/api/workers/authorize/code`, {
      method: "POST",
      headers: authed({ "content-type": "application/json" }),
      body: JSON.stringify({
        code,
        account_id: opts?.accountId ?? null,
        approver_note: opts?.approverNote ?? null,
      }),
    }).then(json<LendingSettings>),

  updateAccount: (id: string, patch: { name?: string; approver_note?: string }) =>
    fetch(`${HUB}/api/workers/accounts/${id}`, {
      method: "PUT",
      headers: authed({ "content-type": "application/json" }),
      body: JSON.stringify(patch),
    }).then(json<LendingSettings>),

  /** 移除一個出借帳號。跑過 job 的不會消失，只會被撤掉 token ——
      那些 job 的「由誰代跑」是人情債的依據，不能斷。 */
  removeAccount: (id: string) =>
    fetch(`${HUB}/api/workers/accounts/${id}`, {
      method: "DELETE",
      headers: authed(),
    }).then(json<LendingSettings>),

  setAccepting: (accepting: boolean) =>
    fetch(`${HUB}/api/workers/accepting?accepting=${accepting}`, {
      method: "POST",
      headers: authed(),
    }).then(json<{ accepting: boolean }>),

  adminHealth: () =>
    fetch(`${HUB}/api/admin/health`, { headers: authed() }).then(json<AdminHealth>),
  adminStats: () =>
    fetch(`${HUB}/api/admin/stats`, { headers: authed() }).then(json<AdminStats>),
  adminStuckJobs: () =>
    fetch(`${HUB}/api/admin/stuck-jobs`, { headers: authed() }).then(json<StuckJob[]>),

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
