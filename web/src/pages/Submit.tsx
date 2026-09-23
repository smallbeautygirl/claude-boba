// 提交頁 = 登入後的首頁。
//
// 使用情境是「我額度爆了，很急」，所以最短路徑優先：一個框、一個下拉、一個勾選。
// 不做精靈式多步驟 —— 拆成三頁只是增加三次點擊（web-spec §3）。

import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, DEFAULT_MODELS, SITE_MODELS, type LenderRow } from "../api";
import { Composer, fmtSize } from "../Composer";

// 額度只給紅綠燈，不給百分比：精確數字會讓人盤算「他還有 66%，再送一個沒差」，
// 把人情變成資源計算（web-spec §3）。
const QUOTA: Record<LenderRow["quota"], string> = {
  green: "🟢",
  yellow: "🟡",
  red: "🔴",
  unknown: "⚪️",
};

// 選單顯示的是「站台白名單 ∩ 該代跑者白名單」（web-spec §3）。
// 寫死一份清單的話，挑了只開 Haiku 的人仍然選得到 Sonnet，要等 job 送出去才失敗 ——
// 而失敗不計債（SPEC §5），那個帳號的額度就白燒了。
//
// 「自動」沒有特定代跑者，取**聯集**：2026-09-22 起派單在 Hub，它只會把 job
// 派給有開放這個 model 的人（SPEC §4.12）。舊版取交集是因為當時 Hub 挑不了人
// （誰先 poll 誰拿到），只能事先保證每個人都跑得動 —— 那讓一個人關掉 Sonnet
// 就擋住全站的 Sonnet。
//
// 退路是 DEFAULT_MODELS 而不是整份白名單：Fable 只在有人開的時候才該出現在下拉，
// 半夜沒人接單的時候讓它出現，送出去的 job 會排隊等一個可能永遠不會來的人。
function poolFor(lenders: LenderRow[], lendingId: string) {
  return lendingId
    ? lenders.filter((l) => l.id === lendingId)
    : lenders.filter((l) => l.online && l.accepting);
}

function offeredModels(lenders: LenderRow[], lendingId: string) {
  const pool = poolFor(lenders, lendingId);
  if (pool.length === 0) return DEFAULT_MODELS;
  const offered = SITE_MODELS.filter((m) =>
    pool.some((l) => l.available_models.includes(m.value)),
  );
  // 聯集是空的（沒有人回報過條件）就退回預設 —— 總比給一個空下拉好。
  return offered.length ? offered : DEFAULT_MODELS;
}

// Fable 不在下拉裡的時候，一行字說清楚「為什麼沒有」跟「該去找誰」（web-spec §3）。
// 四種狀態要分開講，因為使用者接下來該做的事不一樣：指定的人沒開 → 去拜託他或換人；
// 線上有人但都沒開 → 去拜託線上的人；有人開了但離線 → 等他，不是求他；全站沒人開 → 死心。
//
// 語氣是站台一貫的「好啦我請」，但**不能讓人以為問了就會有** —— CLAUDE.md 記過一次
// 錯誤訊息把人導錯方向的教訓。點名最多三個，超過就不點：一長串名字沒有人會真的去問。
function fableHint(lenders: LenderRow[], lendingId: string): string | null {
  const pool = poolFor(lenders, lendingId);
  if (pool.some((l) => l.available_models.includes("fable"))) return null;
  if (lendingId) {
    const who = lenders.find((l) => l.id === lendingId)?.owner ?? "他";
    return `${who} 沒開 Fable。帶杯手搖去拜託他，或換回自動。`;
  }
  const opened = lenders.filter((l) => l.available_models.includes("fable"));
  if (opened.length) {
    // 離線或暫停接單都算 —— 對委託者來說一樣是「現在派不到他」。
    return `${opened.map((l) => l.owner).join("、")} 有開 Fable，但現在沒在接單。`;
  }
  if (pool.length === 0) return "全站還沒有人開 Fable。";
  const names = pool.map((l) => l.owner);
  const target =
    names.length > 3 ? "一位線上的代跑者" : names.join("、");
  return `線上沒人開 Fable。想跑的話，帶杯手搖去拜託 ${target}。`;
}

// 前端先擋，不要讓人上傳三分鐘才說太大（web-spec §3）。
// 依據：一句 "pong" 的 transcript 就 224 KB，真實 RD session 估 10–50 MB；
// 超過 50 MB 的 session，`--resume` 本身也會慢到不實用。
const MAX_TRANSCRIPT_BYTES = 50 * 1024 * 1024;

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

// 選了資料夾之後只預覽時間最近的這幾個。讀十個檔案的開頭很便宜，
// 而「剛跑到一半額度就沒了」的那個一定在最前面。
const PREVIEW_COUNT = 10;
const PREVIEW_BYTES = 64 * 1024;

// 從 transcript 開頭抽第一則使用者訊息。
//
// 沒有這個，資料夾選取器只是把「認不出哪個是哪個」從終端機搬到瀏覽器 ——
// 檔名是 session id，列十個 UUID 跟列在終端機裡一樣沒用。
function firstUserText(row: unknown): string {
  const r = row as { type?: string; message?: { role?: string; content?: unknown } };
  if (r.type !== "user" || r.message?.role !== "user") return "";
  const content = r.message.content;
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  for (const block of content) {
    const b = block as { type?: string; text?: string };
    if (b.type === "text" && b.text) return b.text;
  }
  return "";
}

async function previewOf(file: File): Promise<string> {
  let head: string;
  try {
    head = await file.slice(0, PREVIEW_BYTES).text();
  } catch {
    return "";
  }
  for (const line of head.split("\n")) {
    if (!line.trim()) continue;
    let row: unknown;
    try {
      row = JSON.parse(line);
    } catch {
      continue; // 最後一行多半被 slice 切斷，跳過就好
    }
    const text = firstUserText(row).trim();
    // 開頭是標籤的多半是 CLI 自己塞的（<command-name>、<system-reminder>…），
    // 那不是使用者打的字，拿來當預覽認不出是哪個對話。
    if (!text || text.startsWith("<")) continue;
    return text.replace(/\s+/g, " ").slice(0, 80);
  }
  return "";
}

// Claude Code 每一則 assistant row 都帶 usage，最後一則就是那一刻的 context 佔用。
// 所以這個數字是**量到的不是估的** —— 不必 tokenize，讀檔案結尾就有。
//
// 分母固定 200K：run-job.sh 只帶 --model，沒有 1M context 的旗標。Sonnet / Haiku
// 是 200K；Fable 的 context 是 1M，所以這個警告對 Fable 的 job 偏保守 —— 它會在其實
// 裝得下的時候也講話。不為此分兩套：八成的人選的是 Sonnet，而多講一句的代價是零。
const CONTEXT_WINDOW = 200_000;

// 超過這裡才講話。門檻不是「快爆了」而是「代跑時會爆」—— 你的 prompt、附件，
// 以及 job 自己跑出來的那幾十輪，都疊在這份 session 之上。留 30% 給那一段。
//
// 不做常駐的百分比：精確數字會讓人盤算（同這個檔案開頭那條線）。八成的人送出的
// 對話根本不到這個數，這行字對他們不該存在。
const CONTEXT_WARN_TOKENS = CONTEXT_WINDOW * 0.7;

// ⚠️ 讀出來的數字可能**大於 200K**，那不是 bug。
// 產生這份 session 的機器可以是 1M context 的設定（實測四份真實檔案，兩份超過
// 200K，最大 563K）。而 job 跑的是 sonnet / haiku 的 200K —— 所以那種對話不是
// 「快滿了」，是**一定裝不下**，文案要分成兩種講法。

// 只讀結尾這麼多。最後一則 assistant row 一定在裡面，而整份 50 MB 拉進瀏覽器
// 只為了讀一個數字，代價與收益不成比例。
const USAGE_TAIL_BYTES = 512 * 1024;

interface Usage {
  input_tokens?: number;
  cache_creation_input_tokens?: number;
  cache_read_input_tokens?: number;
  output_tokens?: number;
}

/** 這一則佔了多少 context。四個欄位都要算 —— cache_read 是被讀回來的那一段，
    它一樣在 context 裡，漏掉它算出來的數字會小一個數量級。 */
function usageTotal(u: Usage): number {
  return (
    (u.input_tokens ?? 0) +
    (u.cache_creation_input_tokens ?? 0) +
    (u.cache_read_input_tokens ?? 0) +
    (u.output_tokens ?? 0)
  );
}

/** 從 session 檔尾端讀出目前的 context 佔用。讀不出來回 null —— 不要用猜的填。 */
async function contextUsedTokens(file: File): Promise<number | null> {
  let tail: string;
  try {
    tail = await file.slice(Math.max(0, file.size - USAGE_TAIL_BYTES)).text();
  } catch {
    return null;
  }
  const lines = tail.split("\n");
  // 由後往前找第一則有 usage 的。開頭那行多半被 slice 切斷，parse 失敗就跳過。
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i].trim();
    if (!line) continue;
    let row: unknown;
    try {
      row = JSON.parse(line);
    } catch {
      continue;
    }
    const usage = (row as { message?: { usage?: Usage } }).message?.usage;
    if (usage) {
      const total = usageTotal(usage);
      if (total > 0) return total;
    }
  }
  return null;
}

function fmtTokens(n: number): string {
  return `${Math.round(n / 1000)}K`;
}

function fmtWhen(ms: number): string {
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

interface Candidate {
  file: File;
  preview: string;
}

export function Submit() {
  const navigate = useNavigate();
  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState("sonnet");
  const [lendingId, setLendingId] = useState("");
  const [lenders, setLenders] = useState<LenderRow[]>([]);
  const [consented, setConsented] = useState(false);
  const [busy, setBusy] = useState(false);
  // 上傳的 session 檔。一選好就上傳，不是等到按送出 ——
  // 50 MB 的檔案在按下送出之後才開始傳，使用者會盯著一顆沒反應的按鈕。
  const [session, setSession] = useState<{
    name: string;
    size: number;
    key: string;
    /** 這份對話目前的 context 佔用。null = 檔案裡讀不出 usage。 */
    tokens: number | null;
  } | null>(null);
  const [uploading, setUploading] = useState(false);
  // 選了資料夾之後列出來的候選。null = 還沒選過資料夾。
  const [candidates, setCandidates] = useState<Candidate[] | null>(null);
  const [scanning, setScanning] = useState(false);
  // 這頁自己的錯誤（選 session 檔、掃資料夾）。附件與送出的錯誤由 Composer 顯示。
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listLenders().then(setLenders).catch(() => setLenders([]));
  }, []);

  const models = useMemo(
    () => offeredModels(lenders, lendingId),
    [lenders, lendingId],
  );
  const fableNote = useMemo(
    () => fableHint(lenders, lendingId),
    [lenders, lendingId],
  );
  const lender = lenders.find((l) => l.id === lendingId)?.owner ?? null;

  // 選中的 model 對方跑不動就退回第一個他跑得動的。在 render 期間收斂而不是用 effect ——
  // 代跑者清單是非同步載進來的，用 effect 會有一個 render 的空窗送出跑不動的 model。
  const chosen = models.some((m) => m.value === model) ? model : models[0].value;

  // 換代跑者要重新勾同意：上一次勾的是對「另一個人」的揭露。
  // 點名字才是這個勾選的重點（web-spec §3），沿用等於把名字當裝飾。
  function pickWorker(id: string) {
    setLendingId(id);
    setConsented(false);
  }


  async function scanFolder(picked: FileList | null) {
    if (!picked?.length) return;
    setError(null);
    const found = Array.from(picked)
      .filter((f) => f.name.endsWith(".jsonl") && f.size > 0)
      .sort((a, b) => b.lastModified - a.lastModified)
      .slice(0, PREVIEW_COUNT);

    if (!found.length) {
      setCandidates([]);
      return;
    }
    setScanning(true);
    try {
      setCandidates(
        await Promise.all(
          found.map(async (file) => ({ file, preview: await previewOf(file) })),
        ),
      );
    } finally {
      setScanning(false);
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
      const tokens = await contextUsedTokens(file);
      const key = await api.uploadTranscript(file);
      setSession({ name: file.name, size: file.size, key, tokens });
      setCandidates(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "上傳失敗，請再試一次");
    } finally {
      setUploading(false);
    }
  }

  async function submit(attachmentKeys: string[]) {
    setBusy(true);
    try {
      const job = await api.createJob({
        prompt,
        model: chosen,
        requested_lending_id: lendingId || null,
        transcript_key: session?.key ?? null,
        attachment_keys: attachmentKeys,
      });
      navigate(`/jobs/${job.id}`);
    } catch (err) {
      setBusy(false);
      throw err; // Composer 會顯示在送出鍵旁邊
    }
  }


  return (
    <div className="card">
      <h1>丟一個 job 出去 🧋</h1>
      <p className="lede">額度用完了？找還有額度的同事幫你跑。跑完請他喝一杯就好。</p>

      {/* 順序：說明 → job 參數 → 同意 → 輸入列。
          會擋住送出、或決定這個 job 花多少錢的東西，全部排在送出鍵**上面** ——
          送出鍵現在在輸入列裡，參數放它下面的話，使用者會按到一顆不會動的鍵
          卻不知道原因。 */}
      {/* under-field 拿掉了：它的負上緣是為了緊貼欄位，而這段警告現在上面
          沒有欄位（輸入列在它下面），留著會讓它往上貼到 lede。 */}
      <p className="hint">
        {session ? (
          <>
            ✅ 這是真的續跑 —— 代跑者那邊會用 <code>--resume</code> 接上你這份 session，
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
      <div className="row">
        <label>
          代跑者
          {/* 一列一個**人**。他底下有幾個 Claude 帳號不在這裡出現 ——
              委託者挑的是人，站台自己決定用哪個帳號跑（ADR-0001）。
              額度那顆燈取他最充裕的帳號，不是平均（web-spec §3）。 */}
          <select value={lendingId} onChange={(e) => pickWorker(e.target.value)}>
            <option value="">自動（推薦）</option>
            {lenders.map((l) => (
              <option key={l.id} value={l.id} disabled={!l.online}>
                {l.owner} · {l.online ? "🟢" : "⚫️"} ·{" "}
                {l.allow_full_network ? "🌐 開放網路" : "🔒 白名單"} · 額度 {QUOTA[l.quota]}
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
      {/* Fable 不在下拉裡的原因。放下拉外面而不是 disabled option：原生 <select>
          的 disabled option 掛不了說明，而「為什麼沒有」才是這行字的重點。 */}
      {fableNote && <p className="hint under-field">🍖 {fableNote}</p>}
      {/* 選到 Fable 才講，而且用級距表的詞、不出數字 —— 精確數字會讓人計較
          （SPEC §4.7）。帳本照舊只顯示級距；要算錢的人 job 詳情頁本來就看得到金額。 */}
      {chosen === "fable" && (
        <p className="hint under-field">
          🍖 Fable 一趟常常會超出級距表 —— 到那裡就不是請飲料的等級了。
        </p>
      )}

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
              {lender ? `${lender} 技術上可以看到我送出的內容` : "代跑者技術上可以看到我送出的內容"}
            </strong>
          </span>
        </label>
        <p>
          這個 job 會在{lender ? ` ${lender} ` : "對方"}的電腦上執行。
          系統預設不讓代跑者查看內容，但技術上他有能力看到。
          請不要送出公司機密、客戶個資，或任何你不希望被對方看到的東西。
        </p>
      </div>


      {/* 輸入列是共用元件（Composer.tsx）—— 接著問用的是同一個。
          使用者要的就是兩邊一樣，各寫一份一定會走樣；附件的四道上限與撞名
          去重尤其不能有兩套，那是安全與計費相關的規則。 */}
      <Composer
        value={prompt}
        onChange={setPrompt}
        onSubmit={submit}
        busy={busy}
        blockedReason={!prompt.trim() ? "先寫點東西" : !consented ? "先勾選上面的同意" : null}
        label={session ? "接下來要它做什麼" : "對話內容"}
        placeholder={
          session ? "沿用上傳的 session 繼續問…" : "把你的對話貼進來，或直接寫你要它做什麼"
        }
        rows={session ? 4 : 8}
        usedBytes={session?.size ?? 0}
      />

      <div className="resume">
        <div className="resume-head">接續一個 Claude Code 的對話</div>
        {/* 選 session 檔與掃資料夾的錯誤顯示在這裡，就是它們發生的地方。
            附件與送出的錯誤由 Composer 顯示在送出鍵旁邊。 */}
        {error && <p className="error">{error}</p>}

        {session ? (
          <>
          <div className="resume-picked">
            <div>
              <strong>{session.name}</strong>
              <span className="muted"> · {fmtSize(session.size)}</span>
            </div>
            <button
              type="button"
              className="small"
              onClick={() => setSession(null)}
            >
              移除
            </button>
          </div>
          {/* 只在真的會失真的時候講話。平常這行不存在 —— 看到警告卻無事發生的
              次數一多，這行字就死了。

              也只講給有 session 檔的人聽：貼上的文字與附件沒有 token 數，
              混一個精確值和一個爛估計在同一個提示裡，整個提示就都不可信了。 */}
          {session.tokens !== null && session.tokens > CONTEXT_WARN_TOKENS && (
            <p className="hint">
              {session.tokens > CONTEXT_WINDOW ? (
                <>
                  這份對話（{fmtTokens(session.tokens)}）比代跑用得到的 context
                  還大（200K）。它一定會被壓縮，而且壓掉的不只一點 ——
                </>
              ) : (
                <>
                  這份對話已經用掉{" "}
                  {Math.round((session.tokens / CONTEXT_WINDOW) * 100)}% 的 context
                  （{fmtTokens(session.tokens)} / 200K）。代跑時 Claude 會自動壓縮
                  較早的內容，細節可能會掉 ——
                </>
              )}{" "}
              <strong>重要的前提建議在下面重講一次</strong>。
            </p>
          )}
          </>
        ) : (
          <>
            <p className="muted">
              在自己電腦上用 Claude Code 跑到一半、額度沒了？（終端機、IDE 擴充、
              Desktop 的 Code 分頁，以及 Cowork 的 local session）選出那份
              session，代跑者那邊會真的 <code>--resume</code> 接上去，
              不是把對話當文字重讀。
            </p>

            <label className="session-pick">
              {scanning ? "讀取中…" : uploading ? "上傳中…" : "選 session 資料夾"}
              <input
                type="file"
                disabled={scanning || uploading}
                {...({ webkitdirectory: "", directory: "" } as Record<string, string>)}
                onChange={(e) => {
                  void scanFolder(e.target.files);
                  e.target.value = "";
                }}
              />
            </label>

            <p className="muted">
              選 <code>~/.claude/projects</code>。它在 Finder 裡是隱藏的 ——
              對話框打開後按 <kbd>Cmd</kbd>+<kbd>Shift</kbd>+<kbd>.</kbd>{" "}
              才看得到（Windows 檔案總管：檢視 → 顯示 → 隱藏的項目）。
            </p>

            {/* 瀏覽器確實讀了他其他對話的內容，即使一個 byte 都沒上傳。
                這種事要正經講（web-spec §9），不能用輕鬆語氣帶過。 */}
            <p className="privacy">
              🔒 這些檔案只在你的瀏覽器裡開啟，只有你選中的那一個會被上傳。
            </p>

            {candidates?.length === 0 && (
              <p className="muted">
                這個資料夾底下沒有 <code>.jsonl</code>。
                確認選的是 <code>~/.claude/projects</code>；
                如果那裡也是空的，代表這個對話沒有本機 session 檔，用貼的就好。
              </p>
            )}

            {candidates && candidates.length > 0 && (
              <ul className="candidates">
                {candidates.map((c) => (
                  <li key={`${c.file.name}-${c.file.lastModified}`}>
                    <button type="button" onClick={() => void pickFile(c.file)}>
                      <span className="cand-when">{fmtWhen(c.file.lastModified)}</span>
                      <span className="cand-preview">
                        {c.preview || <span className="muted">（讀不出開頭）</span>}
                      </span>
                      <span className="cand-size">{fmtSize(c.file.size)}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}

            {/* 指令不砍掉：選取器在某個瀏覽器或 OS 上失敗時，使用者不能完全沒有路。 */}
            <details className="cmds findfile">
              <summary>或者用終端機</summary>
              <p className="muted">跑這行 —— 有列出東西就是有：</p>
              <pre>ls -t ~/.claude/projects/*/*.jsonl | head -5</pre>
              <p className="muted">
                剛跑到一半額度就沒了的話，通常就是最上面那個。
                什麼都沒有就用貼的 —— Chat、claude.ai、手機，以及 Cowork 的
                cloud session 都不留本機檔，貼上也幾乎不會少東西。
              </p>
              {/* Cowork 沒有「專案路徑」可以對，但它的 metadata 有標題。
                  照標題找出 cliSessionId，再拿去 projects/ 底下比對。 */}
              <p className="muted">
                Cowork 的對話沒有專案路徑可以對，改用標題找（macOS）：
              </p>
              {/* 路徑用引號包住、glob 留在引號外，整條不含任何反斜線。
                  原本寫成 `Application\ Support` 加行尾續行 —— 那兩個反斜線是
                  **JS 的**跳脫，在 template literal 裡就被吃掉了，渲染出來是一行
                  沒跳脫的 Application Support，貼進 bash 會 No such file or
                  directory。畫面上看起來對，實際是假的。
                  用 $HOME 不用 ~，因為 ~ 在引號裡不展開。 */}
              <pre>
                {'grep -l \'你對話標題的關鍵字\' "$HOME/Library/Application Support/Claude/claude-code-sessions"/*/*/local_*.json'}
              </pre>
              <p className="muted">
                找到的 <code>local_*.json</code> 裡有 <code>cliSessionId</code>，
                那就是 <code>~/.claude/projects/</code> 底下對應的檔名。
              </p>
            </details>
          </>
        )}
      </div>



    </div>
  );
}
