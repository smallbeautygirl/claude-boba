// 「我來代跑」的新畫面：一位代跑者一份出借設定，不是一份機器清單。
//
// 舊模型下「多筆」對應「我有桌機和筆電」；新模型下他只有一個 Claude 帳號，
// 也沒有機器 —— 所以這頁從「產生 token 給你的機器用」變成**「你出借的條件」**。
//
// 那三個條件不是裝飾：花費上限、可用 model、要不要開放外網，以前全在代跑者
// 自己機器的 worker/.env 裡。他沒有機器之後，不搬進網頁就等於憑空消失，
// 而那正是「把額度借出去」讓人敢做的原因 —— 一個 Opus job 八分鐘能燒掉
// US$25，那是他的錢。

import { useState } from "react";
import { api, SITE_MODELS, type LendingSettings } from "../api";

export function Lending({
  settings,
  reload,
}: {
  settings: LendingSettings;
  reload: () => void;
}) {
  return settings.has_token ? (
    <Conditions settings={settings} reload={reload} />
  ) : (
    <StartLending reload={reload} />
  );
}

/* 還沒授權。這頁的重點是講清楚他答應的是什麼 —— 不是把帳號給出去，
   是讓這個站台用他的額度跑別人的 job，而且上限由他自己定。 */
function StartLending({ reload }: { reload: () => void }) {
  const [url, setUrl] = useState<string | null>(null);
  const [code, setCode] = useState("");
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
      await api.submitAuthorizationCode(code.trim());
      reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "這串碼沒有被接受，請再授權一次");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h2>開始出借</h2>
      <p>
        授權一次就結束：<strong>不用開機、不用裝東西、不用讓筆電開著</strong>。
        之後你只需要偶爾看帳本、去收飲料。
      </p>
      {/* 這段照 web-spec §9 正經寫。他授權出去的是「用他的額度執行別人的內容」，
          不是「把檔案給別人看」，兩者的風險不同，寫錯人會誤判。 */}
      <p className="privacy">
        <strong>你答應的是讓這個站台用你的 Claude 額度，執行同事送進來的內容。</strong>
        花掉的是你的額度，上限由你在下面設定。授權之後任何畫面都不會再顯示那串
        token，包含這一頁。
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
      {error && <p className="error">{error}</p>}
    </>
  );
}

/* 已授權：他的出借條件。 */
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
            {settings.online ? "🟢 站台可以用你的額度" : "⚫️ 站台目前連不上你的授權"}
          </span>
        </div>
        <div className="worker-actions">
          <button
            className="small"
            onClick={() =>
              api.updateLending({ accepting: !settings.accepting }).then(reload)
            }
          >
            {settings.accepting ? "暫停接單" : "恢復接單"}
          </button>
        </div>
      </div>

      <h2>你出借的條件</h2>

      <label>
        每個 job 的花費上限（US$）
        <input
          type="number"
          min="0"
          step="0.5"
          value={budget}
          onChange={(e) => setBudget(e.target.value)}
        />
      </label>
      {/* 這個數字是唯一擋得住「一個 job 燒掉一整天額度」的東西，所以要說實話。 */}
      <p className="hint under-field">
        超過就中止那個 job。<strong>花掉的是你的額度</strong> ——
        一個大 model 的 job 跑八分鐘就可能燒掉 US$25。
      </p>

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
        <p className="warn">一個都沒勾的話不會有 job 派給你。</p>
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
        <button type="button" onClick={save} disabled={!dirty}>
          儲存條件
        </button>
        {saved && !dirty && <span className="muted">已儲存</span>}
      </div>
      {error && <p className="error">{error}</p>}
    </>
  );
}
