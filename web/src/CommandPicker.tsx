// 指令選單。提交頁與「接著問」共用 —— 續問時同樣需要它，
// 一個對話往往是先問清楚、再請它產出檔案。
//
// 指令是「選一個」，不是「一直插入」：worker 把整個文字框當成一個 prompt 送給
// `claude -p`（worker/run-job.sh），而 Claude Code 只認 prompt 開頭的那一個
// slash command —— 後面再出現的 `/xxx` 會變成第一個指令的參數，不會各自執行。
// 所以疊四個指令不是「做四件事」，是「做第一件事，並把另外三個名字當參數餵給它」。
//
// 這個元件因此擁有「選一個指令」的完整語意（讀出目前是哪一個、換掉、取消），
// 呼叫端只要把文字框的值交出來。

import { useEffect, useMemo, useState } from "react";
import { api, type CommandCatalog } from "./api";

// 指令清單由 CommandPicker 與 CommandChips 共用，而兩者會同時掛載 ——
// 各自 fetch 就是同一頁打兩次同一支 API。這裡存的是 Promise 不是結果，
// 所以兩個元件在同一個 tick 掛載也只會有一個請求在飛。
let catalogPromise: Promise<CommandCatalog> | null = null;

function useCatalog(): CommandCatalog | null {
  const [catalog, setCatalog] = useState<CommandCatalog | null>(null);
  useEffect(() => {
    let alive = true;
    catalogPromise ??= api.commands();
    catalogPromise.then(
      (c) => alive && setCatalog(c),
      () => {
        // 失敗就不要留著一個永遠 reject 的 promise，下次掛載還有機會。
        catalogPromise = null;
        if (alive) setCatalog(null);
      },
    );
    return () => {
      alive = false;
    };
  }, []);
  return catalog;
}

// 清單裡所有指令的名字。`setCommand` 要靠它分辨「使用者自己打的 /tmp/foo」
// 與「真的是一個指令」。
function allNames(catalog: CommandCatalog): string[] {
  return catalog.groups.flatMap((g) => g.commands.map((c) => c.name));
}

// 文字框空著時露在框內的那幾個。
//
// 為什麼要有這個東西：`/` 已經在輸入列上了，但**知道 `/` 是什麼的人不需要被告知，
// 不知道的人永遠不會去點它** —— 而後者正是這個平台的價值所在（他的 Claude app
// 產不出 pptx，這裡可以）。所以不能只靠那顆按鈕。
//
// 為什麼是「空的時候才出現」而不是常駐：這一頁的使用情境是「額度爆了，很急」，
// 最短路徑優先。使用者一開始打字就表示他知道自己要什麼，這時四顆按鈕只是雜訊。
//
// 露哪幾個由 hub 的 commands.json 標 `chip` 決定，不寫死在這裡 ——
// 那是編輯判斷（要向 BD/PM 講什麼），會改，而且改它不該要動前端。
export function CommandChips({
  value,
  onChange,
}: {
  value: string;
  onChange: (next: string) => void;
}) {
  const catalog = useCatalog();
  if (!catalog) return null;
  if (value.trim() !== "") return null;

  const names = allNames(catalog);
  const chips = catalog.groups.flatMap((g) => g.commands.filter((c) => c.chip));
  if (chips.length === 0) return null;

  return (
    <div className="composer-chips">
      {chips.map((c) => (
        <button
          type="button"
          key={c.name}
          className="chip-btn"
          title={`${c.name} — ${c.desc}`}
          onClick={() => onChange(setCommand(value, c.name, names))}
        >
          {c.label}
        </button>
      ))}
    </div>
  );
}

// 開頭的那一個 token。後面要對照目錄才算數 —— 不然使用者自己打的
// 「/tmp/foo.log 這個檔案」會被當成指令砍掉。
const LEADING = /^\s*(\/[A-Za-z0-9_:.-]+)(\s|$)/;

export function leadingCommand(text: string, known: string[]): string | null {
  const m = text.match(LEADING);
  return m && known.includes(m[1]) ? m[1] : null;
}

// 換掉開頭的指令；再點一次目前這個就是取消。使用者打的內容一律保留。
export function setCommand(text: string, name: string, known: string[]): string {
  const current = leadingCommand(text, known);
  const rest = (current ? text.replace(LEADING, "") : text).trimStart();
  if (current === name) return rest;
  return rest ? `${name} ${rest}` : `${name} `;
}

export function CommandPicker({
  value,
  onChange,
  label = "可以用的指令（點一下選用）",
  bare = false,
}: {
  value: string;
  onChange: (next: string) => void;
  label?: string;
  // bare：不要自帶 <details>，只吐內容。給輸入列用 —— 那裡的開關是工具列上
  // 那顆 `/`，自己再包一層 <details> 會變成兩個開關管同一件事。
  bare?: boolean;
}) {
  const catalog = useCatalog();

  const names = useMemo(() => (catalog ? allNames(catalog) : []), [catalog]);

  if (!catalog) return null;

  const active = leadingCommand(value, names);
  const activeLabel = catalog.groups
    .flatMap((g) => g.commands)
    .find((c) => c.name === active)?.label;

  const groups = catalog.groups.map((g) => (
        <div key={g.id} className="cmd-group">
          <div className="cmd-head">
            {g.title} <span className="muted">· {g.audience}</span>
          </div>
          <p className="muted">{g.hint}</p>
          <div className="chips">
            {g.commands.map((c) => (
              <button
                type="button"
                key={c.name}
                className={c.name === active ? "chip-btn on" : "chip-btn"}
                aria-pressed={c.name === active}
                title={`${c.name} — ${c.desc}`}
                onClick={() => onChange(setCommand(value, c.name, names))}
              >
                {c.label}
              </button>
            ))}
          </div>
        </div>
  ));

  if (bare) return <div className="cmds-bare">{groups}</div>;
  return (
    <details className="cmds">
      <summary>{activeLabel ? `指令：${activeLabel}（再點一下取消）` : label}</summary>
      {groups}
    </details>
  );
}
