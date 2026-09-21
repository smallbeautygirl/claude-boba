// 指令選單。提交頁與「接著問」共用 —— 續問時同樣需要它，
// 一個對話往往是先問清楚、再請它產出檔案。

import { useEffect, useState } from "react";
import { api, type CommandCatalog } from "./api";

export function CommandPicker({
  onPick,
  label = "可以用的指令（點一下插入）",
}: {
  onPick: (name: string) => void;
  label?: string;
}) {
  const [catalog, setCatalog] = useState<CommandCatalog | null>(null);

  useEffect(() => {
    api.commands().then(setCatalog).catch(() => setCatalog(null));
  }, []);

  if (!catalog) return null;
  return (
    <details className="cmds">
      <summary>{label}</summary>
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
                className="chip-btn"
                title={`${c.name} — ${c.desc}`}
                onClick={() => onPick(c.name)}
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

// 插在最前面，游標留在後面讓人接著打。不直接送出 ——
// 多數指令要搭配內容才有意義。
export function prependCommand(prev: string, name: string): string {
  return prev.startsWith(name) ? prev : `${name} ${prev}`.trimEnd() + " ";
}
