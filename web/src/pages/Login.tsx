// 用 Observ（公司帳號）登入。Hub 不儲存密碼，只轉交一次換 token。

import { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth";

export function Login() {
  const { signIn } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [reveal, setReveal] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(email, password);
    } catch (err) {
      setError(err instanceof Error ? err.message : "登入失敗");
      setBusy(false);
    }
  }

  return (
    <form className="card narrow" onSubmit={submit}>
      <h1>claude-boba 🧋</h1>
      {/* 介紹連結放在 lede 旁邊，**不要**搬到底部那句「密碼只會轉交給 Observ」
          附近 —— 那句話要被當真，旁邊不能站著別的東西（web-spec §10 的註記、
          §9 對許願板貼圖提示的同一條理由）。
          §10 禁止的是跟登入無關、只為了有趣的裝飾；這個連結回答的是
          「我為什麼要登入」。 */}
      <p className="lede">
        用你的 Observ 帳號登入。 <Link to="/about">這是什麼？</Link>
      </p>
      <label>
        Email
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoComplete="username"
        />
      </label>
      {/* 顯示鈕用 secondary：這頁唯一的主要動作是「登入」，讓兩顆實心按鈕並排的話，
          位置更靠上的「顯示」會先被看到。它也永遠可按 —— 它切的是欄位的顯示模式，
          不是對資料的動作，密碼還空著也是合法狀態。 */}
      <div className="field-row">
        <label>
          密碼
          <input
            type={reveal ? "text" : "password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
          />
        </label>
        <button type="button" className="secondary" onClick={() => setReveal(!reveal)}>
          {reveal ? "隱藏" : "顯示"}
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      <button type="submit" disabled={busy || !email || !password}>
        {busy ? "登入中…" : "登入"}
      </button>
      <p className="hint">密碼只會轉交給 Observ 換取 token，claude-boba 不儲存它。</p>
    </form>
  );
}
