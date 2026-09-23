// 人情債帳本。
//
// 結清只有債主能按（SPEC §4.8）—— 現實中請客是出租者被請，他最清楚有沒有發生。
// 借用者只能「戳一下」，把催促的責任放在欠債的人身上。
// 債務不設到期日，但顯示欠了幾天：比自動勾銷更有社交壓力，也更好笑。

import { useCallback, useEffect, useState } from "react";
import { api, type DebtRow, type Ledger as LedgerData } from "../api";
import { usd } from "../money";

export function Ledger() {
  const [data, setData] = useState<LedgerData | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.ledger().then(setData).catch((e: Error) => setError(e.message));
  }, []);

  useEffect(load, [load]);

  // Teams 通知連到 /ledger#<debt id>。瀏覽器自己的錨點跳轉在這裡沒有用 ——
  // 進站時那一列還不存在（資料是後來才 fetch 回來的），等它渲染完瀏覽器
  // 早就放棄了。所以資料到齊之後自己滾一次。
  useEffect(() => {
    if (!data) return;
    const id = window.location.hash.slice(1);
    if (!id) return;
    document.getElementById(id)?.scrollIntoView({ block: "center" });
  }, [data]);

  if (error) return <div className="card"><p className="error">{error}</p></div>;
  if (!data) return <div className="card">載入中…</div>;

  const nothing =
    data.i_owe.length === 0 && data.owed_to_me.length === 0 && data.settled.length === 0;

  return (
    <div className="card">
      <h1>帳本</h1>
      {nothing && <p className="lede">乾乾淨淨，不欠任何人 ✨</p>}

      {data.i_owe.length > 0 && (
        <>
          <h2>你欠別人</h2>
          {data.i_owe.map((d) => (
            <Row key={d.id} debt={d} action="nudge" onDone={load} />
          ))}
        </>
      )}

      {data.owed_to_me.length > 0 && (
        <>
          <h2>別人欠你</h2>
          {data.owed_to_me.map((d) => (
            <Row key={d.id} debt={d} action="settle" onDone={load} />
          ))}
        </>
      )}

      {data.settled.length > 0 && (
        <>
          <h2>已結清</h2>
          {data.settled.map((d) => (
            <Row key={d.id} debt={d} onDone={load} />
          ))}
        </>
      )}
    </div>
  );
}

function Row({
  debt,
  action,
  onDone,
}: {
  debt: DebtRow;
  action?: "settle" | "nudge";
  onDone: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);

  async function act() {
    setBusy(true);
    setFailed(null);
    try {
      if (action === "settle") await api.settle(debt.id);
      if (action === "nudge") await api.nudge(debt.id);
      onDone();
    } catch (e) {
      // 這裡原本沒有 catch，錯誤變成未捕捉的 rejection —— 按鈕恢復可按、
      // 畫面一個字都不變。而這兩支端點**真的會拒絕**：對方先動了、或這頁
      // 是舊的，就會拿到 403/409。沉默地什麼都不做是最糟的那種回應（web-spec §9）。
      setFailed(e instanceof Error ? e.message : "沒送出去，再試一次");
    } finally {
      setBusy(false);
    }
  }

  return (
    // id 是給 Teams 通知的錨點用的：那則通知連到 /ledger#<debt id>，
    // 瀏覽器會滾到這一列，`.debt:target` 把它標出來。
    <div className="debt" id={debt.id}>
      <div>
        <div className="debt-label">{debt.label}</div>
        <div className="muted">
          {debt.direction === "owe" ? `欠 ${debt.counterpart}` : `${debt.counterpart} 欠你`}
          {" · "}
          {usd(debt.amount_usd)}
          {debt.status !== "settled" && ` · 已經 ${debt.days} 天了`}
          {/* 「請過了」是借用者自己宣告的。對他說「對方說請過了」，
              說的人就變成別人 —— 兩邊看到的字必須不一樣。 */}
          {debt.status === "nudged" &&
            (debt.direction === "owe" ? " · 你說請過了" : " · 對方說請過了")}
        </div>
        {failed && <div className="error">{failed}</div>}
      </div>
      {action && debt.status !== "settled" && (
        <button onClick={act} disabled={busy} className="small">
          {action === "settle" ? "他還了" : "我請過了"}
        </button>
      )}
    </div>
  );
}
