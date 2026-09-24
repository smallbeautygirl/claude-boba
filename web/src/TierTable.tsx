// 級距表。帳本（收合）與排行榜（展開）共用 —— 兩處各畫一份會各自漂。
// 資料來自 hub 的 pricing._TIERS，前端不另抄。最底那格 floor_usd 是 "0"，
// 畫面上顯示成「< US$1」而不是「≥ US$0」。數字只到門檻，不出每筆的精確金額
// （SPEC §4.7：精確會讓人計較）。

import type { TierRow } from "./api";

export function TierTable({
  tiers,
  open = false,
}: {
  tiers: TierRow[];
  open?: boolean;
}) {
  if (tiers.length === 0) return null;
  const min = tiers.length >= 2 ? tiers[tiers.length - 2].floor_usd : null;
  return (
    <details className="tiers" open={open}>
      <summary>級距怎麼算</summary>
      <p className="hint">
        看的是<strong>單筆 job 的花費</strong>（Anthropic API
        等價金額），一筆一筆算、不累計。
        失敗、中止、自己跑自己的不計。全站同一張表。
      </p>
      <table>
        <tbody>
          {tiers.map((t) => (
            <tr key={t.tier}>
              <td className="tier-range">
                {t.tier === "none"
                  ? `< US$${trim(min)}`
                  : `≥ US$${trim(t.floor_usd)}`}
              </td>
              <td>{t.label}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

function trim(n: string | null): string {
  if (n === null) return "?";
  return String(Number(n));
}
