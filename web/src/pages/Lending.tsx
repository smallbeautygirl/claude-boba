// 「我來代跑」的新畫面：一位代跑者一份出借設定，不是一份機器清單。
//
// 舊模型下「多筆」對應「我有桌機和筆電」；新模型下他只有一個 Claude 帳號，
// 也沒有機器 —— 所以這頁從「產生 token 給你的機器用」變成**「你出借的條件」**。
//
// 那三個條件不是裝飾：花費上限、可用 model、要不要開放外網，以前全在代跑者
// 自己機器的 worker/.env 裡。他沒有機器之後，不搬進網頁就等於憑空消失，
// 而那正是「把額度借出去」讓人敢做的原因 —— 一個 Opus job 八分鐘能燒掉
// US$25，那是他的錢。

import { useCallback, useEffect, useState } from "react";
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
        {settings.accounts.map((a) => (
          <AccountRow key={a.id} account={a} reload={reload} onReplace={setReplacing} />
        ))}
      </ul>

      {adding || replacing ? (
        <Authorize
          account={replacing}
          onDone={() => {
            setAdding(false);
            setReplacing(null);
            reload();
          }}
          onCancel={() => {
            setAdding(false);
            setReplacing(null);
          }}
        />
      ) : (
        <p className="actions">
          <button type="button" className="small" onClick={() => setAdding(true)}>
            再授權一個帳號
          </button>
        </p>
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

  return (
    <li className="account">
      <div className="account-head">
        <strong>{account.name}</strong>
        {account.approver_note && (
          <span className="muted">（批准者：{account.approver_note}）</span>
        )}
        {account.needs_reauth && (
          <span className="warn">
            ⚠️ 需重新授權 —— 這個帳號目前不接單，你其他的帳號照常
          </span>
        )}
      </div>

      {/* 百分比與重置倒數**只給本人**。知道「他一小時後就滿血」會直接變成
          「那我現在多送幾個」—— 這條跟 web-spec §3 不顯示百分比同一個理由。 */}
      {account.quota_fresh && windows.length > 0 ? (
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

      <p className="actions">
        <button type="button" className="small" onClick={() => onReplace(account)}>
          重新授權
        </button>
        <button type="button" className="small" onClick={remove}>
          不再出借這個帳號
        </button>
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
      await api.submitAuthorizationCode(code.trim(), {
        accountId: account?.id,
        approverNote: note.trim() || undefined,
      });
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
            <button type="button" onClick={finish} disabled={busy || !code.trim()}>
              {busy ? "確認中…" : "完成授權"}
            </button>
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
