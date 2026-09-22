/* 這頁現在沒有資料可以顯示，但它不寫「即將推出」——
   那四個字沒有資訊量，三個月後還在的話會變成「這個產品沒人維護」的訊號。
   排行榜缺的不是程式，是第二個使用者，所以這頁講的是那件事。 */
export function Leaderboard() {
  return (
    <div className="card">
      <h1>排行榜</h1>
      <p className="lede">還沒有東西可以排 —— 目前只有你一個人在用。</p>

      <p>
        排行榜排的是人情債：誰幫別人跑得最多、誰欠得最多、誰還得最快。
        這三個數字都需要至少兩個人，而 <strong>人不能欠自己</strong>。
      </p>

      <h2>要讓這頁長出來，需要第二個人借一次</h2>
      <ol className="steps">
        <li>把這個網址傳給一個同事</li>
        <li>他用自己的 Observ 帳號登入</li>
        <li>他丟一個 job，由你的 worker 跑</li>
      </ol>
      <p className="hint">
        那一筆跑完的當下，他欠你一杯，這頁就會有第一行。
      </p>
    </div>
  );
}
