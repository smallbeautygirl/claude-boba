// 出租者的介面。極簡：狀態卡 + 產生 token + 暫停接單。
//
// 不做「封鎖特定借用者」（web-spec §8）—— 在人情系統裡那是社交核彈，
// 一旦按鈕存在，沒按也會有人猜想。而它要解決的問題在 10 人團隊裡用講的比較快。

import { useCallback, useEffect, useState } from "react";
import { api, type WorkerRow } from "../api";
import { useAuth } from "../auth";

const QUOTA: Record<WorkerRow["quota"], string> = {
  green: "🟢 充裕",
  yellow: "🟡 快滿了",
  red: "🔴 幾乎沒了",
  unknown: "⚪️ 還不知道",
};

export function MyWorker() {
  const { me } = useAuth();
  const [workers, setWorkers] = useState<WorkerRow[]>([]);
  const [name, setName] = useState("");
  const [issued, setIssued] = useState<string | null>(null);
  // 刪除是兩段式：先按「刪除」，那一列才換成「確定刪除？」。
  // 不用 window.confirm —— 它跳出來的字沒辦法講「這台跑過幾個 job」。
  const [confirming, setConfirming] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.listWorkers().then(setWorkers).catch(() => setWorkers([]));
  }, []);
  useEffect(load, [load]);

  const mine = workers.filter((w) => w.owner === me?.display_name);

  async function remove(id: string) {
    setError(null);
    try {
      await api.deleteWorker(id);
      setConfirming(null);
      load();
    } catch (e) {
      // hub 的 409 訊息已經說得出「跑過幾個 job」，直接顯示它。
      setError(e instanceof Error ? e.message : "刪不掉，請重新整理再試");
    }
  }

  async function create() {
    const w = await api.createWorker(name || "我的電腦");
    setIssued(w.token);
    setName("");
    load();
  }

  return (
    <div className="card">
      <h1>我來代跑</h1>
      {/* 標題跟分頁一致，都是動詞，跟「丟 job」對稱。頁面主體還在描述
          「在自己機器上代跑」那條路 —— 那條仍然支援，所以這裡還不能改寫成
          託管模型的說法，整頁重寫是另一筆。 */}
      <p className="lede">
        借出額度給同事。這條路是在你自己的電腦上跑，Claude 憑證不會離開那台機器。
      </p>

      {mine.map((w) => (
        <div key={w.id} className="worker">
          <div>
            <strong>{w.name}</strong>{" "}
            <span className="muted">
              {w.online ? "🟢 線上" : "⚫️ 離線"} · 額度 {QUOTA[w.quota]} ·{" "}
              {w.allow_full_network ? "🌐 開放網路" : "🔒 白名單網路"} ·{" "}
              {w.available_models.join(" / ") || "—"}
              {w.claude_code_version && ` · CLI ${w.claude_code_version}`}
            </span>
          </div>
          <div className="worker-actions">
            <button
              className="small"
              onClick={() => api.setAccepting(w.id, !w.accepting).then(load)}
            >
              {w.accepting ? "暫停接單" : "恢復接單"}
            </button>
            {/* 跑過 job 的機器不給刪，但**不是把按鈕灰掉** —— 灰掉的按鈕不會
                告訴人為什麼。刪掉會讓那些 job 失去出租者，而「由誰代跑」
                正是人情債的依據。 */}
            {w.job_count === 0 &&
              (confirming === w.id ? (
                <>
                  <button className="small danger" onClick={() => remove(w.id)}>
                    確定刪除
                  </button>
                  <button className="small" onClick={() => setConfirming(null)}>
                    取消
                  </button>
                </>
              ) : (
                <button className="small" onClick={() => setConfirming(w.id)}>
                  刪除
                </button>
              ))}
          </div>
        </div>
      ))}
      {mine.some((w) => (w.job_count ?? 0) > 0) && (
        <p className="hint">
          跑過 job 的機器不能刪 —— 那些紀錄會失去代跑者，而「由誰代跑」是人情債的依據。
        </p>
      )}
      {error && <p className="error">{error}</p>}

      <h2>新增一台</h2>
      {/* 使用者真的問過「新增一台 worker 後，背後是新建一個 container 嗎？」——
          那個疑問本身就是問題：這顆按鈕讀起來像雲端幫你開了一台機器。
          詞的定義在 CONTEXT.md。 */}
      <p className="hint">
        這裡產生的只是一組 token。<strong>實際接單的是你自己電腦上跑起來的程式</strong>，
        不是雲端幫你開的機器；每個 job 會在那台電腦上另外開一個用完即丟的容器。
      </p>
      {/* 以前用一個 &nbsp; 的 label 包住按鈕來對齊 —— label 裡包 button，
          讀屏軟體會念得很奇怪。改成一般的底端對齊。 */}
      <div className="field-row">
        <label>
          名稱
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="我的電腦" />
        </label>
        <button type="button" onClick={create}>
          產生 worker token
        </button>
      </div>

      {issued && (
        <div className="consent">
          <p>
            <strong>把這串貼進 worker 的 <code>.env</code>：</strong>
          </p>
          <pre>WORKER_TOKEN={issued}</pre>
          <p>
            這串只會顯示這一次。worker 用它連 Hub，完全不需要你的 Observ 帳密 ——
            把公司密碼寫進 <code>.env</code> 是不必要的風險。
          </p>
          {/* 這頁以前到這裡就結束了，於是沒有任何地方說還要設 CLAUDE_CREDENTIALS
              —— 少了它 worker 根本跑不起來。步驟不抄進頁面（web-spec §10 選的是
              文件而不是互動式引導），這裡只指路。寫檔案路徑不寫連結：出租者
              手上本來就有這份 repo，worker 就是從那裡跑起來的。 */}
          <p>
            <strong>還沒完</strong>：worker 還要設定你的 Claude Code 憑證，
            照 <code>worker/README.md</code>〈出借你的額度〉做。
            懶得一步步做的話跑 <code>worker/install.sh</code>。
          </p>
        </div>
      )}
      {/* MinIO console。只有這一頁有，不放頁尾 —— 這個 bucket 是整站共用、
          prefix 分區，console 沒有「只看自己」這種權限。借用者點了不是進不去
          （白給一個挫折），就是繞過 hub 的擁有者檢查。出租者不一樣：這頁只有
          他們看得到，而且他們手上已經有更敏感的東西（自己的 Anthropic 憑證）。
          網址沒設就整段不出現 —— 一個連不上的連結比沒有連結更糟。 */}
      {me?.s3_console_url && (
        <>
          <h2>job 的檔案放在哪</h2>
          <p className="hint">
            對話紀錄、附件與產出都存在 MinIO 裡，一個 job 一個資料夾：
            <code>jobs/&lt;job id&gt;/</code>，<strong>job id 就在該 job 詳情頁的
            網址列上</strong>。平常不需要進去 —— job 頁面的下載連結走的是短效期的
            預簽網址。
          </p>
          <p className="privacy">
            <strong>這個 bucket 是整個站台共用的，沒有「只看自己」的權限。</strong>
            你在 console 裡看得到的不只是自己的 job，也包含其他人的對話與檔案。
            這頁之外的地方不會出現這個連結，原因就是這個。
          </p>
          <p className="console-open">
            <a
              className="chip-btn"
              href={me.s3_console_url}
              target="_blank"
              rel="noreferrer noopener"
            >
              開啟 MinIO console ↗
            </a>
          </p>
          <p className="hint">登入憑證跟管理員拿，畫面上不會有。</p>
        </>
      )}
    </div>
  );
}
