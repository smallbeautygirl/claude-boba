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
import { api, type AdminHealth, type AdminStats, type StuckJob } from "../api";
import { useAuth } from "../auth";

function mmss(total: number) {
  const m = Math.floor(total / 60);
  return m < 60 ? `${m} 分` : `${Math.floor(m / 60)} 小時 ${m % 60} 分`;
}

export function Admin() {
  const { me } = useAuth();
  const [health, setHealth] = useState<AdminHealth | null>(null);
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [stuck, setStuck] = useState<StuckJob[]>([]);
  const [error, setError] = useState<string | null>(null);

  const admin = me?.is_admin ?? false;

  useEffect(() => {
    // 不是管理者就不要打那三支 —— 後端會回 403，但那時畫面上已經是一個
    // 空的管理頁殼了。**這不是權限控制**（真正的防線是後端那三個 403），
    // 只是不要讓走錯路的人看到一頁半成品。
    if (!admin) return;
    Promise.all([api.adminHealth(), api.adminStats(), api.adminStuckJobs()])
      .then(([h, s, j]) => {
        setHealth(h);
        setStats(s);
        setStuck(j);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "讀不到系統狀態"));
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
            累計花費 <span className="cost">US${stats.spend_usd}</span>
          </li>
        </ul>
      )}
      <p className="hint">
        這裡只有總數。<strong>誰用得兇、誰欠誰，這頁查不到</strong>，那是帳本上
        當事人之間的事。
      </p>

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

      {/* MinIO console 從「我來代跑」搬過來：那個 console 沒有「只看自己」的
          權限，登進去看得到所有人的對話與檔案。舊模型下它放在代跑者那頁還說得過去
          （代跑者是 RD、手上已經有更敏感的東西），託管模型下代跑者只是授權過的人，
          那個理由就沒了。 */}
      {me?.s3_console_url && (
        <>
          <h2>檔案儲存</h2>
          <p className="hint">
            對話紀錄、附件與產出都存在 MinIO 裡，一個 job 一個資料夾：
            <code>jobs/&lt;job id&gt;/</code>，job id 就在該 job 詳情頁的網址列上。
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
