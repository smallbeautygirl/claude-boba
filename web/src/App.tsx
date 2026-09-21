import { BrowserRouter, Link, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth";
import { JobDetail } from "./pages/JobDetail";
import { Ledger } from "./pages/Ledger";
import { Login } from "./pages/Login";
import { MyWorker } from "./pages/MyWorker";
import { Notifications } from "./pages/Notifications";
import { Submit } from "./pages/Submit";

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Shell />
      </BrowserRouter>
    </AuthProvider>
  );
}

function Shell() {
  const { me, loading, signOut } = useAuth();

  if (loading) return <div className="card">載入中…</div>;
  if (!me) return <Login />;

  return (
    <>
      <nav className="nav">
        {/* 首頁是提交頁，不是儀表板 —— 使用情境是「我額度爆了，很急」，
            那個當下最不需要的就是儀表板（web-spec §3）。 */}
        <Link to="/">丟 job</Link>
        <Link to="/ledger">帳本</Link>
        <Link to="/worker">我的 worker</Link>
        <Link to="/notifications">通知</Link>
        <span className="spacer" />
        <span className="muted">{me.display_name}</span>
        <button className="link" onClick={signOut}>
          登出
        </button>
      </nav>
      <Routes>
        <Route path="/" element={<Submit />} />
        <Route path="/jobs/:id" element={<JobDetail />} />
        <Route path="/ledger" element={<Ledger />} />
        <Route path="/worker" element={<MyWorker />} />
        <Route path="/notifications" element={<Notifications />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </>
  );
}
