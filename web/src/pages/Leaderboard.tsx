// 排行榜（web-spec §7、SPEC §4.9）。純聚合數字，沒有動態牆 ——
// 「誰跟誰借了什麼」本身就是資訊，會踩到 SPEC §4.3 的隱私線。
//
// 三張榜：欠債王（未結清筆數，不按金額）、金主榜（幫別人跑成功的 job 數，
// 不算債 —— 多數 job 不到一杯）、本月最大宗。玩心的預算花在這裡（web-spec §9）。

import { useEffect, useState } from "react";
import { api, type Leaderboard as Board } from "../api";
import { TierTable } from "../TierTable";
import { usd } from "../money";

export function Leaderboard() {
  const [data, setData] = useState<Board | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .leaderboard()
      .then(setData)
      .catch((e: Error) => setError(e.message));
  }, []);

  if (error)
    return (
      <div className="card">
        <p className="error">{error}</p>
      </div>
    );
  if (!data) return <div className="card">載入中…</div>;

  const empty = data.debtors.length === 0 && data.lenders.length === 0;

  return (
    <div className="card">
      <h1>排行榜</h1>

      {empty ? (
        <Empty users={data.users} />
      ) : (
        <>
          <p className="lede">誰欠得最多、誰幫得最多。只有數字，沒有內容。</p>

          <h2>🧋 欠債王</h2>
          {data.debtors.length === 0 ? (
            <p className="muted">目前沒有人欠著沒還。</p>
          ) : (
            <ol className="board">
              {data.debtors.map((d, i) => (
                <li key={d.name}>
                  <span className="rank">{i + 1}</span>
                  <span className="who">{d.name}</span>
                  <span className="stat">
                    欠著 {d.open_debts} 筆 · {usd(d.total_usd)}
                    {d.oldest_days > 0 && ` · 最久那筆 ${d.oldest_days} 天了`}
                  </span>
                </li>
              ))}
            </ol>
          )}

          <h2>🏆 金主榜</h2>
          <p className="hint">
            幫別人跑成功的 job 數。不到一杯的也算 —— 那才是大多數。
          </p>
          <ol className="board">
            {data.lenders.map((l, i) => (
              <li key={l.name}>
                <span className="rank">{i + 1}</span>
                <span className="who">{l.name}</span>
                <span className="stat">
                  {l.jobs} 個 job · 借出 {usd(l.total_usd)}
                </span>
              </li>
            ))}
          </ol>

          {data.biggest_this_month && (
            <>
              <h2>🍖 本月最大宗</h2>
              <p>
                {data.biggest_this_month.borrower} 用{" "}
                {data.biggest_this_month.lender} 的額度跑了一筆{" "}
                {usd(data.biggest_this_month.amount_usd)} 的 job ——{" "}
                {data.biggest_this_month.label}。
              </p>
            </>
          )}
        </>
      )}

      {/* 級距表展開放著：榜上寫「欠著 2 筆」，旁邊就要看得到一筆是怎麼算出來的
          （2026-09-24 回報）。帳本那邊是收合的，這裡是玩心的頁面，表本身就是內容。 */}
      <TierTable tiers={data.tiers ?? []} open />
    </div>
  );
}

/* 空狀態要說對話：「只有你一個人」跟「有六個人但還沒有人跨人借過」下一步不一樣。
   2026-09-24 回報：明明兩個人在用，頁面還寫只有一個 —— 因為那時這一頁是寫死的。 */
function Empty({ users }: { users: number }) {
  return (
    <>
      <p className="lede">
        {users <= 1
          ? "還沒有東西可以排 —— 目前只有你一個人在用。"
          : `已經有 ${users} 個人登入過，但還沒有人用別人的額度跑成功過。`}
      </p>
      <p>
        排行榜排的是人情：誰幫別人跑得最多、誰欠著最多。這兩個數字都需要至少兩個人，
        而 <strong>人不能欠自己</strong>。
      </p>
      <h2>要讓這頁長出來</h2>
      <ol className="steps">
        {users <= 1 && (
          <li>把這個網址傳給一個同事，他用自己的 Observ 帳號登入</li>
        )}
        <li>有人在「我來代跑」授權一個帳號</li>
        <li>另一個人丟一個 job，用他的額度跑</li>
      </ol>
      <p className="hint">那一筆跑完的當下，金主榜就會有第一行。</p>
    </>
  );
}
