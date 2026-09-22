import { useCallback, useEffect, useState } from "react";

/* 三態，不是兩態。兩態（亮／暗）少一個選項，代價是使用者按過一次就永遠脫離
   系統設定而且回不去 —— 沒有任何操作能表達「還是跟著系統走」。 */
export type Theme = "system" | "light" | "dark";

/* 格子裡是字不是 emoji。emoji 要賭使用者的機器有那個字型（這裡的無頭瀏覽器
   就沒有，🖥 與 🌙 都是豆腐框），而「太陽 vs 月亮」還得讓人自己推論哪個是
   哪個；「系統／亮／暗」直接就是答案。短標題留給 title 與讀螢幕。 */
export const THEMES: { value: Theme; label: string; short: string }[] = [
  { value: "system", label: "跟隨系統", short: "系統" },
  { value: "light", label: "亮色", short: "亮" },
  { value: "dark", label: "暗色", short: "暗" },
];

/* index.html 的行內腳本也讀這個 key —— 那段是為了在 React 掛載前就把屬性補上，
   不然選了暗色的人每次載入都會先閃一下系統的亮色。改這裡要一起改那裡。 */
export const THEME_KEY = "boba-theme";

function isTheme(v: unknown): v is Theme {
  return v === "system" || v === "light" || v === "dark";
}

/* localStorage 在無痕視窗與封鎖 cookie 時是「存取就丟例外」，不是「回 null」。
   讀不到就當成跟隨系統 —— 那是預設值，不是錯誤狀態。 */
export function readTheme(): Theme {
  try {
    const v = localStorage.getItem(THEME_KEY);
    return isTheme(v) ? v : "system";
  } catch {
    return "system";
  }
}

/* 跟隨系統 = 沒有 data-theme，讓 :root 的 `color-scheme: light dark` 自己決定。
   寫 data-theme="system" 的話 CSS 還得多一條規則去抵銷它。 */
function apply(theme: Theme) {
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
}

export function useTheme(): [Theme, (next: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(readTheme);

  useEffect(() => {
    apply(theme);
  }, [theme]);

  /* 存不起來（無痕）時仍然套用 —— 這個分頁會是對的，只是關掉就忘了。
     比整個切換器不能按好。 */
  const choose = useCallback((next: Theme) => {
    setTheme(next);
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      /* 存不了就算了 */
    }
  }, []);

  return [theme, choose];
}
