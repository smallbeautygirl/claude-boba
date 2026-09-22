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
import { Admin } from "./pages/Admin";
import { JobDetail } from "./pages/JobDetail";
import { Leaderboard } from "./pages/Leaderboard";
import { Ledger } from "./pages/Ledger";
import { Login } from "./pages/Login";
import { MyJobs } from "./pages/MyJobs";
import { Lending } from "./pages/Lending";
import { Notifications } from "./pages/Notifications";
import { Submit } from "./pages/Submit";
import { Wishes } from "./pages/Wishes";
import { THEMES, useTheme } from "./theme";

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
  "/worker": "我來代跑",
  "/notifications": "通知",
  "/leaderboard": "排行榜",
  "/wishes": "許願板",
  "/admin": "管理",
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

/* 三格分段控制，不是循環按鈕。循環按鈕在按下去之前看不出下一個是什麼，
   三態尤其糟 —— 想從「暗」回到「跟隨系統」得先猜要按幾次。
   三格則是三個選項同時看得見，而且看得出現在是哪一個。 */
function ThemeToggle() {
  const [theme, choose] = useTheme();
  return (
    <div className="theme-seg" role="group" aria-label="主題">
      {THEMES.map((t) => (
        <button
          key={t.value}
          type="button"
          className={t.value === theme ? "theme-opt on" : "theme-opt"}
          aria-pressed={t.value === theme}
          title={t.label}
          onClick={() => choose(t.value)}
        >
          <span aria-hidden="true">{t.short}</span>
          <span className="sr-only">{t.label}</span>
        </button>
      ))}
    </div>
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
        {/* 只留圖示。站台名稱的文字拿掉是為了把 header 壓回一行（省 ~100px），
            但名字不能真的消失：.sr-only 那份是這個連結的可及名稱，
            title 是給滑鼠的 —— 沒有可見文字時，這兩個是唯一說得出
            「這顆按下去會回首頁」的東西。 */}
        <NavLink className="brand" to="/" title="claude-boba · 回首頁">
          <span aria-hidden="true">🧋</span>
          <span className="sr-only">claude-boba 首頁</span>
        </NavLink>
        <span className="sep" />
        {/* 首頁是提交頁，不是儀表板 —— 使用情境是「我額度爆了，很急」，
            那個當下最不需要的就是儀表板（web-spec §3）。 */}
        <Tab to="/">丟 job</Tab>
        <Tab to="/jobs">我的 job</Tab>
        <Tab to="/ledger">帳本</Tab>
        <Tab to="/worker">我來代跑</Tab>
        <Tab to="/notifications">通知</Tab>
        <Tab to="/leaderboard">排行榜</Tab>
        {/* 導覽列這一項不能省：job 詳情頁那個入口只服務剛跑完 job 的人，
            而代跑者可能好幾天不跑 job，新願望的頻道廣播點進來也要有落點。
            鷹架 —— 許願板下架時這一行跟著刪（web-spec §12）。 */}
        <Tab to="/wishes">許願板</Tab>
        {/* 只對管理者渲染。後端每一支也都自己擋 403 —— 不渲染不等於不能呼叫。 */}
        {me.is_admin && <Tab to="/admin">管理</Tab>}
        {/* 名字、主題、登出是同一組「我」。包成一個元素才不會在換行時被拆散
            —— 導覽列的可用寬度是 968px（比內容寬，見 index.css 的 .nav），
            八個分頁加主題切換離塞滿只剩幾十 px，
            不包的話登出會單獨掉到下一行的左邊，離名字十萬八千里。 */}
        <div className="me">
          <span className="who">{me.display_name}</span>
          <ThemeToggle />
          <button className="secondary" onClick={signOut}>
            登出
          </button>
        </div>
      </nav>
      <Routes>
        <Route path="/" element={<Submit />} />
        <Route path="/jobs" element={<MyJobs />} />
        <Route path="/jobs/:id" element={<JobDetail />} />
        <Route path="/ledger" element={<Ledger />} />
        <Route path="/worker" element={<Lending />} />
        <Route path="/notifications" element={<Notifications />} />
        <Route path="/leaderboard" element={<Leaderboard />} />
        <Route path="/wishes" element={<Wishes />} />
        <Route path="/admin" element={<Admin />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </>
  );
}
