// 站台管理者的維運頁。
//
// **分界：看得到什麼壞了，不能瀏覽誰欠誰。** 在一個以人情債為核心的產品裡，
// 「誰用得兇、誰欠誰多少」是社交敏感資訊 —— 那不是技術問題，是這個工具會不會
// 被同事信任的問題。所以這裡只有系統狀態、不指名的聚合數字，與卡住的 job。
//
// 🚨 這頁的燈全部來自 hub 真的去量的結果。量不到的東西（例如 job 容器的對外
// 白名單）不放燈，放在「無法從這裡確認」那一區並寫出原因 —— 一顆永遠綠的燈
// 比沒有那一項危險，因為它會被相信。

import { useEffect, useState } from "react";
import {
  api,
  type AdminHealth,
  type AdminLendingAccount,
  type AdminStats,
  type StuckJob,
} from "../api";
import { useAuth } from "../auth";
import { usd } from "../money";

function mmss(total: number) {
  const m = Math.floor(total / 60);
  return m < 60 ? `${m} 分` : `${Math.floor(m / 60)} 小時 ${m % 60} 分`;
}

export function Admin() {
  const { me } = useAuth();
  const [health, setHealth] = useState<AdminHealth | null>(null);
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [stuck, setStuck] = useState<StuckJob[]>([]);
  const [accounts, setAccounts] = useState<AdminLendingAccount[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const admin = me?.is_admin ?? false;

  useEffect(() => {
    // 不是管理者就不要打那三支 —— 後端會回 403，但那時畫面上已經是一個
    // 空的管理頁殼了。**這不是權限控制**（真正的防線是後端那三個 403），
    // 只是不要讓走錯路的人看到一頁半成品。
    if (!admin) return;
    api
      .adminLendingAccounts()
      .then(setAccounts)
      .catch(() => setAccounts([]));
    Promise.all([api.adminHealth(), api.adminStats(), api.adminStuckJobs()])
      .then(([h, s, j]) => {
        setHealth(h);
        setStats(s);
        setStuck(j);
      })
      .catch((e) =>
        setError(e instanceof Error ? e.message : "讀不到系統狀態"),
      );
  }, [admin]);

  if (!admin) {
    return (
      <div className="card narrow">
        <h1>管理</h1>
        <p className="lede">這一頁只有站台管理者看得到。</p>
        <p className="hint">
          需要的話跟站台管理者說一聲 —— 名單在 hub 的設定檔裡，改一次要重啟。
        </p>
      </div>
    );
  }

  return (
    <div className="card">
      <h1>管理</h1>
      <p className="lede">
        看得到什麼壞了。<strong>這頁沒有誰欠誰</strong> —— 那是同事之間的事，
        不該因為有維運權限就看得到。
      </p>
      {error && <p className="error">{error}</p>}

      <h2>系統狀態</h2>
      {health?.checks.map((c) => (
        <div key={c.key} className="worker">
          <div>
            <strong>
              {c.ok ? "🟢" : "🔴"} {c.label}
            </strong>{" "}
            <span className="muted">{c.detail}</span>
            {/* 位址自成一行。它跟 detail 講的是兩件事 —— 一個是「量到什麼」，
                一個是「去問了誰」—— 擠在同一行時，紅燈的訊息會被位址稀釋。 */}
            <div className="check-target">
              {c.target ? (
                /* 純文字，不是連結（見 api.ts 的 target）。用 <code> 是因為它
                   要能被反白複製 —— 管理者拿到紅燈之後第一件事就是把它貼進
                   終端機。 */
                <code>{c.target}</code>
              ) : (
                <span className="muted">{c.target_note}</span>
              )}
              {c.open_url && (
                <a href={c.open_url} target="_blank" rel="noreferrer noopener">
                  開啟 ↗
                </a>
              )}
              {/* 「帳密去問誰」貼著連結放，不放在下面那段說明裡 —— 會需要它的
                  那一刻，是他已經點開 console、看到登入框、正在找人的時候。
                  隔了半頁的答案等於沒有答案。

                  點名字，不要寫「跟管理員拿」：那句話的收件人是「某個人」，
                  而讀到它的人正是在找那個人是誰。憑證本身當然不在畫面上
                  （security.md），但「去問誰」不是憑證。

                  語氣可以鬆（web-spec §9 的正經四處不含這裡）。**但下面那段
                  隱私說明不准跟著鬆** —— 那一段講的是「你會看到別人的東西」，
                  它跟這句話只隔幾公分，鬆掉就會一起被讀成裝飾。 */}
              {c.key === "storage" && c.open_url && (
                <span className="muted">帳密跟 vvn 要 🧋 畫面上永遠不會有</span>
              )}
            </div>
          </div>
        </div>
      ))}
      {health?.facts.map((f) => (
        <div key={f.label} className="worker">
          <div>
            {/* 刻意沒有燈：它在畫面上永遠會是綠的（不符就拒絕啟動），
                一顆永遠綠的燈沒有資訊量，只會稀釋旁邊那些真的燈。 */}
            <strong>{f.label}</strong>{" "}
            <span className="muted">
              <code>{f.value}</code> · {f.note}
            </span>
          </div>
        </div>
      ))}
      {health?.unknown.map((u) => (
        <p key={u.label} className="privacy">
          <strong>{u.label}：無法從這裡確認。</strong> {u.why}
        </p>
      ))}

      {/* MinIO console 的連結已經在上面那排的「檔案儲存」那一列（open_url），
          但這段說明要留著，而且要留在看得到那顆連結的同一頁 ——
          它講的不是怎麼用 console，是**點進去會看到誰的東西**。
          連結搬走、說明留下會變成孤兒；說明不搬，那顆連結就變成一個沒有
          上下文的按鈕，而它有隱私後果。

          那個 console 沒有「只看自己」的權限，登進去看得到所有人的對話與檔案。
          舊模型下它放在代跑者那頁還說得過去（代跑者是 RD、手上已經有更敏感的
          東西），託管模型下代跑者只是授權過的人，那個理由就沒了。 */}
      {me?.s3_console_url && (
        <>
          <p className="privacy">
            <strong>
              上面「檔案儲存」那顆〔開啟 ↗〕進去的是整個站台共用的 bucket，
              沒有「只看自己」的權限。
            </strong>
            你在裡面看得到的不只是自己的 job，也包含其他人的對話與檔案。
            這頁之外的地方不會出現那個連結，原因就是這個。
          </p>
          <p className="hint">
            一個 job 一個資料夾：<code>jobs/&lt;job id&gt;/</code>， job id
            就在該 job 詳情頁的網址列上。
          </p>
          {/* 「帳密跟 vvn 拿」以前在這裡，2026-09-23 搬到上面那顆〔開啟 ↗〕
              旁邊了 —— 它是操作性的答案，要貼著觸發它的那個動作。
              **不要在這裡補回一份**：同一句話出現兩次，人會開始懷疑
              是不是講的是兩件不同的事。 */}
        </>
      )}

      <h2>整站數字</h2>
      {stats && (
        <ul className="steps">
          <li>{stats.users} 個人用過</li>
          <li>
            {stats.jobs_total} 個 job（
            {Object.entries(stats.jobs_by_status)
              .map(([k, v]) => `${k} ${v}`)
              .join("、") || "還沒有"}
            ）
          </li>
          <li>
            成功率{" "}
            {stats.success_rate === null
              ? "還算不出來（沒有跑完的 job）"
              : `${Math.round(stats.success_rate * 100)}%`}
          </li>
          <li>
            {/* 美金是帳，台幣是附註 —— 所以美金在前、用 .cost，台幣用「約」。
                位數的處理在 money.ts，四個顯示金額的地方共用同一份。
                換算台幣用的是**未取整的原值**，不是畫面上那個兩位數。 */}
            累計花費 <span className="cost">{usd(stats.spend_usd)}</span>
            {stats.twd && (
              <>
                {" "}
                · 約 NT$
                {Math.round(
                  Number(stats.spend_usd) * Number(stats.twd.rate),
                ).toLocaleString("zh-TW")}
              </>
            )}
          </li>
        </ul>
      )}
      {/* 匯率這件事要嘛講清楚、要嘛不要講。拿不到的時候寫出原因，
          不要讓台幣無聲消失 —— 這一頁的規則是「量不到的東西連同原因一起講」。 */}
      {stats && (
        <p className="hint">
          {stats.twd ? (
            <>
              台幣是<strong>粗估</strong>：{stats.twd.source}，
              {stats.twd.quoted_on} 的 {stats.twd.rate}。帳單是美金，
              <strong>對帳以美金為準</strong>。
              {stats.twd_note && `（${stats.twd_note}）`}
            </>
          ) : (
            <>台幣換算暫時算不出來：{stats.twd_note || "沒有匯率"}。</>
          )}
        </p>
      )}
      <p className="hint">
        這裡只有總數。<strong>誰用得兇、誰欠誰，這頁查不到</strong>，那是帳本上
        當事人之間的事。
      </p>

      {/* 出借帳號，維運視角（2026-09-24，CONTEXT.md）。排序：等重新授權的在最前面
          （這一頁的用途是看什麼壞了），已停用的在最後、字淡掉。
          用量只有燈號 —— 百分比是代跑者自己的事。沒有 job 內容、不指名委託者。 */}
      <h2>出借帳號</h2>
      {accounts === null ? (
        <p className="hint">載入中…</p>
      ) : accounts.length === 0 ? (
        <p className="hint">還沒有人授權任何帳號。</p>
      ) : (
        <>
          <div className="table-scroll">
            <table className="accounts">
              <thead>
                <tr>
                  <th>狀態</th>
                  <th>代跑者</th>
                  <th>帳號</th>
                  <th>Claude 帳號</th>
                  <th>額度</th>
                  <th>上次派到</th>
                  <th>近 {accounts[0].days} 天</th>
                  <th>批准者</th>
                </tr>
              </thead>
              <tbody>
                {accounts.map((a) => (
                  <tr key={a.account_id} className={a.status}>
                    <td>{STATUS[a.status]}</td>
                    <td>{a.lender}</td>
                    <td>
                      {a.name}
                      {a.credits_required_models.length > 0 && (
                        <div className="muted small-note">
                          {a.credits_required_models.join("、")} 要買 credits
                        </div>
                      )}
                    </td>
                    <td className="muted">
                      {a.claude_email ?? "—"}
                      {a.claude_plan && ` · ${a.claude_plan}`}
                    </td>
                    <td>{LIGHT[a.quota]}</td>
                    <td className="muted">
                      {a.last_assigned_at
                        ? ago(a.last_assigned_at)
                        : "還沒被派到過"}
                    </td>
                    <td className="muted">
                      {a.jobs} 個 job · {usd(a.cost_usd)}
                    </td>
                    <td className="muted">{a.approver_note ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="hint">
            身分與狀態而已：用量只有燈號，沒有百分比；哪個 job
            是誰委託的，這裡沒有。 要停某人的帳號，去找那個人 ——
            撤銷是代跑者自己的事。
          </p>
        </>
      )}

      <h2>卡住的 job</h2>
      {stuck.length === 0 ? (
        // 空清單與「偵測不到」在畫面上長得一樣，所以把判定講出來。
        <p className="hint">
          沒有。判定是：排隊超過 10 分鐘沒被領走（站台 15 分鐘會作廢），
          或執行中超過 12 分鐘。
        </p>
      ) : (
        <>
          {stuck.map((j) => (
            <div key={j.id} className="worker">
              <div>
                <strong>
                  <code>{j.id.slice(0, 8)}</code>
                </strong>{" "}
                <span className="muted">
                  {j.status} · 卡了 {mmss(j.stuck_seconds)}
                  {j.worker ? ` · ${j.worker}` : " · 還沒有人領"}
                </span>
              </div>
            </div>
          ))}
          <p className="hint">只有狀態與時間 —— job 的內容不會出現在這裡。</p>
        </>
      )}
    </div>
  );
}

const STATUS: Record<AdminLendingAccount["status"], string> = {
  needs_reauth: "⚠️ 等重新授權",
  usable: "🟢 可用",
  retired: "已停用",
};

const LIGHT: Record<AdminLendingAccount["quota"], string> = {
  green: "🟢",
  yellow: "🟡",
  red: "🔴",
  unknown: "—",
};

function ago(iso: string): string {
  const sec = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (sec < 3600) return `${Math.round(sec / 60)} 分鐘前`;
  if (sec < 86400) return `${Math.round(sec / 3600)} 小時前`;
  return `${Math.round(sec / 86400)} 天前`;
}
