import { useEffect } from "react";
import {
  BrowserRouter,
  Navigate,
  NavLink,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";
import { AuthProvider, useAuth } from "./auth";
import { JobDetail } from "./pages/JobDetail";
import { Ledger } from "./pages/Ledger";
import { Login } from "./pages/Login";
import { MyJobs } from "./pages/MyJobs";
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

// 分頁標題跟著頁面走。開了好幾個分頁時，光看「claude-boba」分不出哪個是哪個。
const TITLES: Record<string, string> = {
  "/": "丟 job",
  "/jobs": "我的 job",
  "/ledger": "帳本",
  "/worker": "我的 worker",
  "/notifications": "通知",
};

function useDocumentTitle() {
  const { pathname } = useLocation();
  useEffect(() => {
    const page = TITLES[pathname] ?? (pathname.startsWith("/jobs/") ? "Job" : null);
    document.title = page ? `${page} · claude-boba` : "claude-boba";
  }, [pathname]);
}

// 目前頁面是一顆 accent-soft 藥丸。少了它，五個連結完全一樣，
// 使用者得靠記憶知道自己在哪。
function Tab({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <NavLink
      to={to}
      end={to === "/"}
      className={({ isActive }) => (isActive ? "tab active" : "tab")}
    >
      {children}
    </NavLink>
  );
}

function Shell() {
  const { me, loading, signOut } = useAuth();
  useDocumentTitle();

  if (loading) return <div className="card">載入中…</div>;
  if (!me) return <Login />;

  return (
    <>
      {/* 三段：站台 ｜ 頁面 ｜ 我。以前七個元素長得一模一樣，看不出在哪一頁，
          中間還空一大塊把左右拆成兩組不相干的東西。 */}
      <nav className="nav">
        <NavLink className="brand" to="/">
          <span aria-hidden="true">🧋</span>
          claude-boba
        </NavLink>
        <span className="sep" />
        {/* 首頁是提交頁，不是儀表板 —— 使用情境是「我額度爆了，很急」，
            那個當下最不需要的就是儀表板（web-spec §3）。 */}
        <Tab to="/">丟 job</Tab>
        <Tab to="/jobs">我的 job</Tab>
        <Tab to="/ledger">帳本</Tab>
        <Tab to="/worker">我的 worker</Tab>
        <Tab to="/notifications">通知</Tab>
        <span className="spacer" />
        <span className="who">{me.display_name}</span>
        <button className="secondary" onClick={signOut}>
          登出
        </button>
      </nav>
      <Routes>
        <Route path="/" element={<Submit />} />
        <Route path="/jobs" element={<MyJobs />} />
        <Route path="/jobs/:id" element={<JobDetail />} />
        <Route path="/ledger" element={<Ledger />} />
        <Route path="/worker" element={<MyWorker />} />
        <Route path="/notifications" element={<Notifications />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </>
  );
}
