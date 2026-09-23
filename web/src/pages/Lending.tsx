// 「我來代跑」的新畫面：一位代跑者一份出借設定，不是一份機器清單。
//
// 舊模型下「多筆」對應「我有桌機和筆電」；新模型下他只有一個 Claude 帳號，
// 也沒有機器 —— 所以這頁從「產生 token 給你的機器用」變成**「你出借的條件」**。
//
// 那三個條件不是裝飾：花費上限、可用 model、要不要開放外網，以前全在代跑者
// 自己機器的 worker/.env 裡。他沒有機器之後，不搬進網頁就等於憑空消失，
// 而那正是「把額度借出去」讓人敢做的原因 —— 一個 Opus job 八分鐘能燒掉
// US$25，那是他的錢。

import { Fragment, useCallback, useEffect, useState } from "react";
import {
  api,
  SITE_MODELS,
  type LendingAccount,
  type LendingSettings,
} from "../api";

export function Lending() {
  const [settings, setSettings] = useState<LendingSettings | null>(null);
  const reload = useCallback(() => {
    api.lending().then(setSettings).catch(() => setSettings(null));
  }, []);
  useEffect(reload, [reload]);

  if (!settings) return <p className="muted">載入中…</p>;
  return settings.has_token ? (
    <>
      <Conditions settings={settings} reload={reload} />
      <Accounts settings={settings} reload={reload} />
    </>
  ) : (
    <StartLending reload={reload} />
  );
}

/* 出借帳號那一段。**一個帳號的時候也列一列** —— 那一列不是為了多帳號而存在，
   它是放用量的地方（web-spec §8）。而這也是「出借帳號」這個概念在整個 UI 裡
   唯一露臉的地方：委託者那側從頭到尾看不到它（ADR-0001）。 */
function Accounts({
  settings,
  reload,
}: {
  settings: LendingSettings;
  reload: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const [replacing, setReplacing] = useState<LendingAccount | null>(null);

  return (
    <>
      <h2>你的出借帳號</h2>
      <ul className="accounts">
        {/* 已停用的排到最後。它們不能真的刪掉（跑過的 job 要看得出是誰跑的），
            但它們也不該跟還在服役的那些混在一起 —— 2026-09-23 的實際情況是
            四張卡片裡三張已經停用，而畫面上完全看不出差別。 */}
        {[...settings.accounts]
          .sort((x, y) => Number(y.has_token) - Number(x.has_token))
          .map((a) => (
          <Fragment key={a.id}>
            <AccountRow account={a} reload={reload} onReplace={setReplacing} />
            {/* 表單就地展開在**被按的那一列底下**。原本它渲染在整份清單的最後，
                所以按第一列的「重新授權」時，表單跳到畫面外的底部 —— 使用者
                看到的是「按了沒反應」（2026-09-22 回報）。

                帳號多起來之後這個距離只會更遠，而「哪一列」正是這個動作最需要
                講清楚的事：按錯列會作廢掉另一個帳號的 token。 */}
            {replacing?.id === a.id && (
              <li className="account authorizing">
                <Authorize
                  account={a}
                  onDone={() => {
                    setReplacing(null);
                    reload();
                  }}
                  onCancel={() => setReplacing(null)}
                />
              </li>
            )}
          </Fragment>
        ))}
      </ul>

      {adding ? (
        <Authorize
          account={null}
          onDone={() => {
            setAdding(false);
            reload();
          }}
          onCancel={() => setAdding(false)}
        />
      ) : (
        !replacing && (
          <p className="actions">
            <button type="button" className="small" onClick={() => setAdding(true)}>
              再授權一個帳號
            </button>
          </p>
        )
      )}
    </>
  );
}

/* 窗名 → 人看得懂的名字。不寫死「只有這兩個」—— 存下來的是整包 unifiedWindows，
   Anthropic 之後再加一個窗，這裡沒列到就照原名顯示，而不是消失。 */
const WINDOW_LABELS: Record<string, string> = {
  five_hour: "五小時窗",
  seven_day: "七天窗",
};

function resetsIn(at: string | number | undefined): string | null {
  if (at === undefined) return null;
  const ms = (typeof at === "number" ? at * 1000 : Date.parse(at)) - Date.now();
  if (!Number.isFinite(ms) || ms <= 0) return null;
  const hours = ms / 3_600_000;
  if (hours < 1) return `${Math.round(hours * 60)} 分鐘後重置`;
  if (hours < 48) return `${Math.round(hours)} 小時後重置`;
  return `${Math.round(hours / 24)} 天後重置`;
}

function ago(at: string | null): string {
  if (!at) return "還沒有資料";
  const mins = (Date.now() - Date.parse(at)) / 60_000;
  if (mins < 1) return "資料為剛剛";
  if (mins < 60) return `資料為 ${Math.round(mins)} 分鐘前`;
  return `資料為 ${Math.round(mins / 60)} 小時前`;
}

/* 純粹的「多久以前」。`ago()` 吐的是「資料為 3 小時前」—— 那個「資料為」
   是給額度回報用的，接在別的句子後面會變成「上次被派到 job：資料為 3 小時前」。 */
function since(at: string): string {
  const mins = (Date.now() - Date.parse(at)) / 60_000;
  if (mins < 1) return "剛剛";
  if (mins < 60) return `${Math.round(mins)} 分鐘前`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)} 小時前`;
  return `${Math.round(mins / 60 / 24)} 天前`;
}

function AccountRow({
  account,
  reload,
  onReplace,
}: {
  account: LendingAccount;
  reload: () => void;
  onReplace: (a: LendingAccount) => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(account.name);
  const windows = Object.entries(account.windows);

  async function remove() {
    setError(null);
    try {
      await api.removeAccount(account.id);
      reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "移除不了，請重新整理再試");
    }
  }

  // 改名的暫存。開著編輯時才有值。
  async function refreshIdentity() {
    setError(null);
    try {
      const next = await api.refreshIdentity(account.id);
      // 「這兩列其實是同一個帳號」要當場講。不講的話他會看到兩列一樣的 email，
      // 然後自己去猜哪一個才是真的。
      if (next.same_as) {
        setError(`這跟「${next.same_as}」是同一個 Claude 帳號 —— 留一個就好。`);
      }
      reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "查不到，請重新整理再試");
    }
  }

  async function rename() {
    setError(null);
    try {
      await api.updateAccount(account.id, { name: draft.trim() });
      setRenaming(false);
      reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "改不了，請重新整理再試");
    }
  }

  async function retryCredits() {
    setError(null);
    try {
      await api.retryCredits(account.id);
      reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "改不了，請重新整理再試");
    }
  }

  return (
    <li className="account">
      <div className="account-head">
        {/* 名字點下去就能改。**這是他唯一分得出哪個帳號是哪個的東西** ——
            而在這之前，既有的帳號叫「帳號 2」「帳號 3」且完全改不了
            （PUT /accounts/{id} 早就支援改名，只是沒有人接上 UI）。 */}
        {renaming ? (
          <span className="rename">
            <input
              type="text"
              maxLength={80}
              value={draft}
              autoFocus
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && draft.trim()) rename();
                if (e.key === "Escape") setRenaming(false);
              }}
            />
            <button
              type="button"
              className="small"
              onClick={rename}
              disabled={!draft.trim()}
            >
              改名
            </button>
            <button type="button" className="small" onClick={() => setRenaming(false)}>
              取消
            </button>
          </span>
        ) : (
          <button
            type="button"
            className="rename-open"
            title="改名"
            onClick={() => {
              setDraft(account.name);
              setRenaming(true);
            }}
          >
            <strong>{account.name}</strong> ✎
          </button>
        )}
        {/* **這個帳號到底是哪一個 Claude 帳號。** 名字是他自己打的一串字，
            在這行字出現之前，站台與他都無從分辨兩列是不是同一個帳號。 */}
        {account.claude_email && (
          <span className="muted">
            {account.claude_email}
            {account.claude_plan && ` · ${account.claude_plan}`}
          </span>
        )}
        {account.approver_note && (
          <span className="muted">（批准者：{account.approver_note}）</span>
        )}
        {account.needs_reauth && (
          <span className="warn">
            ⚠️ 需重新授權 —— 這個帳號目前不接單，你其他的帳號照常
          </span>
        )}
        {/* 按過「不再出借」的帳號。**它為什麼還在這裡**要當場講 ——
            不講的話它看起來就像那顆鈕沒有作用（2026-09-23 真的被這樣問）。 */}
        {!account.has_token && !account.needs_reauth && (
          <span className="muted">已停用</span>
        )}
      </div>

      {/* credits 標記常駐，不做「跳一次就消失」的提示：這是帳號的狀態，跟它的
          額度燈一樣。只提示一次的話，一個月後他自己也想不起來為什麼 Fable 的
          job 都跑在另一個帳號上。

          文案不寫「這個帳號不能跑 Fable」—— 它能，只是要買 credits，而那是他
          按幾下就能改變的事。寫成能力會讓人以為要換帳號。 */}
      {account.credits_required_models.length > 0 && (
        <p className="warn">
          這個帳號跑 {account.credits_required_models.join("、")} 需要 usage
          credits，所以站台先不把這些 model 的 job 派給它（其他 model 照常）。
          買好之後按這裡：{" "}
          <button type="button" className="small" onClick={retryCredits}>
            我買了 credits，再試一次
          </button>
        </p>
      )}

      {/* 百分比與重置倒數**只給本人**。知道「他一小時後就滿血」會直接變成
          「那我現在多送幾個」—— 這條跟 web-spec §3 不顯示百分比同一個理由。 */}
      {/* 停用的帳號不必再談額度 —— 它不會被派到。這裡改成回答那個真正的問題：
          「我按了不再出借，為什麼它還在？」 */}
      {!account.has_token && !account.needs_reauth ? (
        <p className="hint">
          已經不再出借了，token 也撤掉了。<strong>紀錄留著是因為它跑過 job</strong> ——
          那些 job 的「由誰代跑」是人情債的依據，帳號整列刪掉那條線就斷了。
        </p>
      ) : account.quota_fresh && windows.length > 0 ? (
        <>
          {windows.map(([key, w]) => (
            <div key={key} className="quota-row">
              <span className="quota-name">{WINDOW_LABELS[key] ?? key}</span>
              <meter max={1} value={w.utilization ?? 0} />
              <span className="quota-pct">
                {w.utilization === undefined
                  ? "—"
                  : `${Math.round(w.utilization * 100)}%`}
              </span>
              <span className="muted">{resetsIn(w.resetsAt) ?? ""}</span>
            </div>
          ))}
          <p className="hint">{ago(account.quota_updated_at)}</p>
        </>
      ) : (
        /* 過期的數字不顯示成即時的：你自己在別的地方也在燒同一個帳號，站台不會
           知道 —— 掛一個理直氣壯的 34% 比說「不知道」更糟。 */
        <p className="hint">
          目前不知道這個帳號用掉多少 —— 它只在跑 job 時回報。
          {account.quota_updated_at && `（最後一次是 ${ago(account.quota_updated_at)}）`}
        </p>
      )}

      {/* 有好幾個帳號的人，這是他唯一看得出「哪一個在輪、哪一個從來沒動過」
          的地方 —— 而那是他看到三張卡片時會問的第一個問題。
          **null 是「還沒被派到過」，不是「不知道」**：那兩件事分開講
          （同 LendingAccount.utilization 的那條註解）。 */}
      {/* 身分有三種狀態，而**「查不到」與「還沒查」必須分開講** ——
          兩個都是空白的話，他不知道該去按那顆按鈕還是該去找 Anthropic。 */}
      {account.has_token && !account.claude_email && (
        <p className="hint">
          {account.claude_identity_checked_at
            ? "查過了，Anthropic 不給這組 token 的帳號資訊（多半是授權範圍不夠）。"
            : "還不知道這是哪一個 Claude 帳號。"}{" "}
          <button type="button" className="small" onClick={refreshIdentity}>
            查一次
          </button>
        </p>
      )}

      <p className="hint">
        {account.last_assigned_at
          ? `上次被派到 job：${since(account.last_assigned_at)}`
          : "還沒被派到過 job"}
      </p>

      <p className="actions">
        <button type="button" className="small" onClick={() => onReplace(account)}>
          {account.has_token ? "重新授權" : "重新啟用"}
        </button>
        {/* 已經停用的就不再給這顆 —— 按下去什麼也不會發生，而一顆沒有作用的
            按鈕正是讓人以為「刪不掉」的原因。 */}
        {account.has_token && (
          <button type="button" className="small" onClick={remove}>
            不再出借這個帳號
          </button>
        )}
      </p>
      {error && <p className="error">{error}</p>}
    </li>
  );
}

/* 還沒授權。這頁的重點是講清楚他答應的是什麼 —— 不是把帳號給出去，
   是讓這個站台用他的額度跑別人的 job，而且上限由他自己定。 */
function StartLending({ reload }: { reload: () => void }) {
  return (
    <>
      <h2>開始出借</h2>
      <p>
        授權一次就結束：<strong>不用開機、不用裝東西、不用讓筆電開著</strong>。
        之後你只需要偶爾看帳本、去收飲料。
      </p>
      <Authorize account={null} onDone={reload} />
    </>
  );
}

/* 授權流程。新增一個帳號與換掉某個帳號的 token 走的是同一條路 ——
   差別只在送出時帶不帶 account_id，以及上面那句話要講什麼。 */
function Authorize({
  account,
  onDone,
  onCancel,
}: {
  account: LendingAccount | null;
  onDone: () => void;
  onCancel?: () => void;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [note, setNote] = useState(account?.approver_note ?? "");
  const [name, setName] = useState(account?.name ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      setUrl((await api.startAuthorization()).authorize_url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "開始授權失敗，請再試一次");
    } finally {
      setBusy(false);
    }
  }

  async function finish() {
    setBusy(true);
    setError(null);
    try {
      const next = await api.submitAuthorizationCode(code.trim(), {
        accountId: account?.id,
        approverNote: note.trim() || undefined,
        name: name.trim() || undefined,
      });
      // **他剛才授權的是他已經借出來的那個 Claude 帳號。** 新 token 被換到既有
      // 那一列上了，清單上不會多出他剛命名的那一個 —— 不講的話他會以為授權失敗。
      //
      // 這條路刻意不是「退回去叫他重來」：`claude setup-token` 產一次就作廢上一組，
      // 退回等於把他剛拿到的那組燒掉，而他做錯的只是重複授權了同一個帳號。
      if (next.merged_into) {
        alert(
          `這是你已經借出來的那個 Claude 帳號，所以新的 token 換到「${next.merged_into}」上了，` +
            "沒有新增一筆。",
        );
      }
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "這串碼沒有被接受，請再授權一次");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {/* 這段照 web-spec §9 正經寫。他授權出去的是「用他的額度執行別人的內容」，
          不是「把檔案給別人看」，兩者的風險不同，寫錯人會誤判。 */}
      <p className="privacy">
        <strong>你答應的是讓這個站台用你的 Claude 額度，執行同事送進來的內容。</strong>
        花掉的是你的額度，上限由你自己設定。授權之後任何畫面都不會再顯示那串
        token，包含這一頁。
      </p>

      {/* ⚠️ **這段話 2026-09-22 被真實事件推翻過一次，不要改回去。**

          原本寫的是「舊的那組站台不會再用，但它在 Claude 那邊仍然有效到期滿」。
          那是錯的：hub 一按下去就在跑 `claude setup-token`（app/authorize.py），
          而它會**當場作廢同一個帳號的舊 token** —— 不是等你把授權碼貼回來才換。

          當天實際發生的事：按了、沒貼碼、離開，然後下一個 job 就 401，
          而畫面把它說成「超出預算，換 Haiku 再試」。那句話錯的方向特別糟 ——
          它讓人以為中途放棄沒有代價。 */}
      <p className="warn">
        {account ? (
          <>
            按下去就會<strong>當場作廢</strong>「{account.name}」現在那組 token，
            不是等你貼完授權碼才換。
          </>
        ) : (
          <>按下去會當場在 Claude 那邊產生一組新的 token。</>
        )}{" "}
        <strong>中途放棄的話，這個帳號會變成不能用</strong>，要重新走完一次才會恢復。
        所以開始之前先確認你等一下拿得到那串授權碼。
      </p>

      {/* 帳號的名字。**新增時必填**（送出鈕會擋）。
          站台認不出這是哪個 Claude 帳號 —— 三個 API 端點對不同帳號回的東西
          逐字相同（SPEC §9 spike #11），所以這個名字是他**唯一**分得出
          「個人 Max」與「公司配的」的線索。
          這個欄位以前不存在，名字由後端給「帳號 2」「帳號 3」，結果是三個月後
          沒有人知道那是什麼 —— 2026-09-23 真的被問了。 */}
      <label>
        這個帳號叫什麼
        <input
          type="text"
          maxLength={80}
          value={name}
          placeholder="例如：我的個人 Max、公司配的那個"
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <p className="hint under-field">
        只有你看得到。<strong>站台認不出這是哪個 Claude 帳號</strong> ——
        你有好幾個的時候，這個名字是唯一分得出來的東西。
      </p>

      {/* 公司帳號的具名批准者（SPEC §4.12）。站台不驗證內容 —— 它沒有辦法知道
          一個帳號是不是公司的。這一欄的用途是三個月後有人問「誰放的」時，
          答案存在某個地方。 */}
      <label>
        這是誰的額度／誰批准的（選填）
        <input
          type="text"
          maxLength={200}
          value={note}
          placeholder="例如：我的個人 Max；或 公司帳號，<主管> 已同意"
          onChange={(e) => setNote(e.target.value)}
        />
      </label>
      <p className="hint under-field">
        個人帳號留空沒關係。<strong>公司配的帳號請填</strong> ——
        被停權時出事的是公司資產，而那時一定會有人問是誰放上去的。
      </p>

      {url ? (
        <>
          <p className="console-open">
            <a className="chip-btn" href={url} target="_blank" rel="noreferrer noopener">
              到 Claude 完成授權 ↗
            </a>
          </p>
          {/* 授權完成之後 Claude 會把碼交給 platform.claude.com，不是交回這裡
              （spike #9），所以那串碼一定要有人貼回來。**貼回這個網頁，不是
              終端機** —— 代跑者的情境就是「登入網頁、授權一次、結束」，
              要他開終端機等於把整條路退回舊模型。 */}
          <label>
            按同意之後，把那一頁給你的授權碼貼回來
            <input
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="貼上授權碼"
              autoComplete="off"
              spellCheck={false}
            />
          </label>
          <p className="hint under-field">
            這串是<strong>用完即失效的一次性碼</strong>，不是你的 token ——
            它換到的 token 由這個站台自己收下，不會回到這個畫面上。
            {/* 五分鐘是 hub 那邊 authorize.SESSION_TTL_SECONDS 的值。
                碼本身的有效期還沒實測（spike #9 標為未驗），所以這句只講
                「站台這邊保留多久」，不要講成「這串碼五分鐘後失效」。 */}
            站台這邊只保留五分鐘，超過就回來重按一次「開始出借」。
          </p>
          <p className="actions">
            {/* 新增帳號時名字必填 —— 那是他唯一認得出這個帳號的線索。
                換 token 時不必填：那個帳號已經有名字了，留白就是不改。
                後端仍會給一個退路名稱，不會因為少一個欄位就把剛產生的 token
                丟掉（`claude setup-token` 產一次就作廢上一組）。 */}
            <button
              type="button"
              onClick={finish}
              disabled={busy || !code.trim() || (!account && !name.trim())}
            >
              {busy ? "確認中…" : "完成授權"}
            </button>
            {/* 這一步會等 hub 跟 Anthropic 來回，最久要一分鐘。沒有這句話，
                使用者會以為畫面當掉了 —— 而他這時最可能做的事是重新整理，
                那會讓整個授權作廢、要從頭再來一次。 */}
            {busy && <span className="muted">最久要一分鐘，先不要重新整理</span>}
          </p>
        </>
      ) : (
        <p className="actions">
          <button type="button" onClick={start} disabled={busy}>
            {busy ? "準備中…" : "開始出借"}
          </button>
        </p>
      )}
      {onCancel && (
        <p className="actions">
          <button type="button" className="small" onClick={onCancel}>
            取消
          </button>
        </p>
      )}
      {error && <p className="error">{error}</p>}
    </>
  );
}

/* 已授權：他的出借條件。條件屬於人，不屬於帳號 —— 兩個帳號共用這一份。 */
function Conditions({
  settings,
  reload,
}: {
  settings: LendingSettings;
  reload: () => void;
}) {
  const [budget, setBudget] = useState(settings.budget_usd);
  const [models, setModels] = useState<string[]>(settings.available_models);
  const [network, setNetwork] = useState(settings.allow_full_network);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const dirty =
    budget !== settings.budget_usd ||
    network !== settings.allow_full_network ||
    models.join() !== settings.available_models.join();

  // hub 會擋掉這兩種（400），但擋在這裡才講得出原因 —— 送出去才被拒絕的話，
  // 使用者看到的是一句錯誤訊息，而不是「哪一格要改」。
  // 範圍跟 hub 的 LendingPatch 一模一樣（`gt=0, le=100`）。前端不要自己發明
  // 更嚴的下限 —— 那會讓人填得出來的值被畫面擋掉，而擋它的理由只存在於這裡。
  const amount = Number(budget);
  const badBudget = !(amount > 0 && amount <= 100);
  const canSave = dirty && models.length > 0 && !badBudget;

  function toggleModel(value: string) {
    setModels((m) => (m.includes(value) ? m.filter((x) => x !== value) : [...m, value]));
  }

  async function save() {
    setError(null);
    try {
      await api.updateLending({
        budget_usd: budget,
        available_models: models,
        allow_full_network: network,
      });
      setSaved(true);
      reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "存不起來，請再試一次");
    }
  }

  return (
    <>
      <div className="worker">
        <div>
          <strong>{settings.accepting ? "接單中" : "已暫停接單"}</strong>{" "}
          <span className="muted">
            {settings.online
              ? "🟢 站台可以用你的額度"
              : "⚫️ 站台的執行主機目前沒有回報 —— 這時誰的額度都借不出去"}
          </span>
        </div>
        <div className="worker-actions">
          <button
            className="small"
            onClick={() => api.setAccepting(!settings.accepting).then(reload)}
          >
            {settings.accepting ? "暫停接單" : "恢復接單"}
          </button>
        </div>
      </div>

      <h2>你出借的條件</h2>
      {settings.accounts.length > 1 && (
        <p className="hint">
          這一份條件套用在你<strong>所有</strong>的出借帳號上 ——
          上限講的是你對風險的態度，不是對某個帳號的態度。
        </p>
      )}

      <label>
        每個 job 的花費上限（US$）
        <input
          type="number"
          min="0.01"
          max="100"
          step="0.5"
          value={budget}
          onChange={(e) => setBudget(e.target.value)}
        />
      </label>
      {/* 這個數字是唯一擋得住「一個 job 燒掉一整天額度」的東西，所以要說實話。 */}
      <p className="hint under-field">
        超過就中止那個 job。<strong>花掉的是你借出去的額度</strong> ——
        一個大 model 的 job 跑八分鐘就可能燒掉 US$25。上限最高只能設到 US$100；
        設得太低的話 job 會一開始就被中止。
      </p>
      {badBudget && <p className="warn">要填大於 0、不超過 100 的數字。</p>}

      <h2 className="sr-only">可用 model</h2>
      <p className="hint">
        可以用哪些 model：只勾 Haiku 的話，同事送 Sonnet 的 job 就不會派給你。
        Fable 預設關著，勾了才會有人把 Fable 的 job 派給你。
      </p>
      <div className="chips">
        {SITE_MODELS.map((m) => (
          <button
            key={m.value}
            type="button"
            className={models.includes(m.value) ? "chip-btn on" : "chip-btn"}
            aria-pressed={models.includes(m.value)}
            onClick={() => toggleModel(m.value)}
          >
            {m.label}
          </button>
        ))}
      </div>
      {models.length === 0 && (
        <p className="warn">至少要留一個 —— 一個都沒有的話沒有人派得動你。</p>
      )}
      {/* 「他勾了」和「他派得動」是兩件事，對外用的是交集。勾著卻沒有一個帳號
          跑得動的話，他會以為 Fable 是開著的，而委託者的下拉裡根本沒有它 ——
          兩邊都不知道發生了什麼。這一行就是把那個落差講出來。

          用 settings（存起來的）而不是 models（畫面上勾的）：剛勾還沒存的那一刻
          後端還不知道，這時講「沒有帳號跑得動」會是錯的。 */}
      {settings.available_models
        .filter((m) => !settings.runnable_models.includes(m))
        .map((m) => (
          <p key={m} className="warn">
            你開了 {m}，但現在沒有帳號跑得動它 —— {m} 的 job 不會派給你。
            原因在上面那個帳號的說明裡。
          </p>
        ))}
      {/* 定性、不估分鐘數：反推出來的分鐘數是假精確，而且會隨 model 版本變。
          他該知道的是「撞到就中止、不計債、燒的是自己的額度」—— 那是 Fable 對
          代跑者真正的代價，不是價目表。上限本身不加欄位也不設門檻（SPEC §4.12：
          上限講的是他對風險的態度，不分 model）。 */}
      {models.includes("fable") && (
        <p className="warn">
          你現在的上限是 US${budget || "?"}。Fable 會很快撞到它 ——
          撞到就中止、不計債，燒掉的是你的額度，而委託者什麼都沒拿到。
          開 Fable 的話，考慮把上限調高一點。
        </p>
      )}

      {/* 外網不是效能設定，是安全邊界（security.md 紅線 3）。預設關著，
          而且要說清楚打開之後多出來的能力是什麼 —— 不是「可能有風險」這種空話。 */}
      <div className="consent">
        <label className="checkbox">
          <input
            type="checkbox"
            checked={network}
            onChange={(e) => setNetwork(e.target.checked)}
          />
          允許 job 連到整個網際網路
        </label>
        <p>
          預設只放行 Claude 本身與這個站台的儲存空間。打開之後，同事送進來的內容
          就能從執行環境對外連線 —— 需要它的情境（例如 <code>pip install</code>）
          確實存在，但打開的是一道邊界，不是一個效能選項。
        </p>
      </div>

      <div className="actions">
        <button type="button" onClick={save} disabled={!canSave}>
          儲存條件
        </button>
        {saved && !dirty && <span className="muted">已儲存</span>}
      </div>
      {error && <p className="error">{error}</p>}
    </>
  );
}
