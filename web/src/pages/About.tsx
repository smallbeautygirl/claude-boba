// 介紹頁（web-spec §13）。**次要入口，不是說明的主力** —— 登入後的落點仍然是
// 提交頁，說明仍然長在提交頁本身。不要把 §3 的文案搬過來，那才是真的推翻 §10。
//
// 四段、一屏、不捲動。級距表、失敗不計債、隱私宣告**都不進來**：隱私已經有提交頁
// 的勾選擋在送出之前，而那裡是 §9 指定的「完全正經」四個地方之一 —— 搬進一頁玩笑
// 語氣的介紹文會讓它變淡。
//
// 這一頁在**登入牆外**（App.tsx）：最可能的抵達方式是同事把連結貼在群組裡，
// 而那個人還沒登入。

import { Link } from "react-router-dom";
import { useAuth } from "../auth";
import { YourTurn } from "../YourTurn";

export function About() {
  const { me } = useAuth();

  return (
    <div className="card about">
      {/* 未登入時這一頁**沒有導覽列**，所以它需要自己的出口 —— 否則整頁是個死巷，
          唯一走得出去的是「我來代跑」，而想登入的人不該被迫走招募那條路。
          登入後不顯示：那時導覽列在，🧋 就是回首頁的路。 */}
      {!me && (
        <Link className="back" to="/">
          ← 回登入
        </Link>
      )}
      <h1>claude-boba 🧋</h1>
      <p className="lede">同事之間的 Claude 代跑互助平台。不收錢，只記人情。</p>

      <p>
        額度用完的人把手上的對話丟進來，還有額度的同事用自己的帳號幫忙跑完，結果
        交還。系統算出這趟在外面要花多少錢，換算成「請喝飲料」的人情債 ——
        <strong>不轉帳、不收錢，只記你欠誰一杯。</strong>
      </p>

      <ol className="steps">
        <li>
          <strong>丟出去</strong>
          <span>貼上你的對話，或上傳 Claude Code 留下的 session 檔。</span>
        </li>
        <li>
          <strong>別人的額度跑</strong>
          <span>結果即時串回你的畫面。關掉瀏覽器也沒關係，回來還在。</span>
        </li>
        <li>
          <strong>請他喝一杯</strong>
          <span>照這趟花掉的錢換算成一杯飲料或一頓飯，記在帳本上。</span>
        </li>
      </ol>

      {/* 「委託者」「代跑者」這兩個詞刻意不教，只描述行為（web-spec §13）：
          它們在畫面上只露臉一次，為了一個欄位教兩個名詞不划算，而突然定義術語
          會讓這頁讀起來像產品手冊。 */}
      <div className="sides">
        <section>
          <h2>額度用完的時候</h2>
          <p>
            你丟一個 job 出去，站台自己找一位還有額度、而且現在願意接單的同事幫你跑。
            跑完你欠他一杯 —— 失敗或中途停掉的不算。
          </p>
        </section>
        <section>
          <h2>額度還有的時候</h2>
          <p>
            你把額度借出去。別人的 job 用你的帳號跑完，欠你一杯。你自己決定單次花費
            上限、開放哪些 model，以及現在接不接單。
          </p>
        </section>
      </div>

      {/* 未登入時照顯示，按鈕導向登入（web-spec §13）—— 對一個還沒登入的讀者，
          招募正是這頁存在的理由之一。已經是代跑者的人看不到。 */}
      {!me?.is_lender && <YourTurn tone="intro" />}
    </div>
  );
}
