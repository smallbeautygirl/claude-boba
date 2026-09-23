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

  async function act() {
    setBusy(true);
    try {
      if (action === "settle") await api.settle(debt.id);
      if (action === "nudge") await api.nudge(debt.id);
      onDone();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="debt">
      <div>
        <div className="debt-label">{debt.label}</div>
        <div className="muted">
          {debt.direction === "owe" ? `欠 ${debt.counterpart}` : `${debt.counterpart} 欠你`}
          {" · "}
          {usd(debt.amount_usd)}
          {debt.status !== "settled" && ` · 已經 ${debt.days} 天了`}
          {debt.status === "nudged" && " · 對方說請過了"}
        </div>
      </div>
      {action && debt.status !== "settled" && (
        <button onClick={act} disabled={busy} className="small">
          {action === "settle" ? "他還了" : "我請過了"}
        </button>
      )}
    </div>
  );
}
