/* 金額的呈現只有這一個地方。
 *
 * web-spec §9 把金額列為全站三個「完全正經」的欄位之一：顯示 US$3.20 就顯示
 * US$3.20，不要模糊化。但**後端存的是 Numeric(12,6)**，照字串印出來會是
 * US$0.183921 —— 那不是不模糊，那是把浮點數的尾巴倒在使用者臉上，
 * 後面四位沒有人做得了任何決定。
 *
 * 所以取到小數點後兩位，但有一個例外：
 *
 * **不可以印出 US$0.00。** SPEC §11 量到一個小任務大約是「一杯飲料的 1/100」，
 * 也就是一兩分錢 —— 取兩位之後它們會全部變成 0.00，而「花費 US$0.00」讀起來是
 * 「這次沒花到錢」，那是假的。低於半分錢的印 `< US$0.01`：它同時說出了
 * 「有花錢」與「少到看不出來」，而這兩件事都是真的。
 */
export function usd(amount: string | number | null | undefined): string {
  if (amount === null || amount === undefined) return "—";
  const n = Number(amount);
  if (!Number.isFinite(n)) return "—";
  // 負數不該出現在這個產品裡（沒有退款），真的出現時照實印出來，
  // 讓它看起來就是錯的 —— 不要用 Math.abs 把它藏掉。
  if (n > 0 && n < 0.005) return "< US$0.01";
  return `US$${n.toFixed(2)}`;
}
