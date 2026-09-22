// 用 Observ（公司帳號）登入。Hub 不儲存密碼，只轉交一次換 token。

import { useState } from "react";
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
      <p className="lede">用你的 Observ 帳號登入。</p>
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
