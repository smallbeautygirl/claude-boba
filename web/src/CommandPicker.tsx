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
}: {
  value: string;
  onChange: (next: string) => void;
  label?: string;
}) {
  const [catalog, setCatalog] = useState<CommandCatalog | null>(null);

  useEffect(() => {
    api.commands().then(setCatalog).catch(() => setCatalog(null));
  }, []);

  const names = useMemo(
    () => catalog?.groups.flatMap((g) => g.commands.map((c) => c.name)) ?? [],
    [catalog],
  );

  if (!catalog) return null;

  const active = leadingCommand(value, names);
  const activeLabel = catalog.groups
    .flatMap((g) => g.commands)
    .find((c) => c.name === active)?.label;

  return (
    <details className="cmds">
      <summary>{activeLabel ? `指令：${activeLabel}（再點一下取消）` : label}</summary>
      {catalog.groups.map((g) => (
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
      ))}
    </details>
  );
}
