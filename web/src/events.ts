// 把 stream-json 的原始事件轉成畫面要顯示的東西。
//
// 顆粒度到「工具名稱 + 檔名」為止，不顯示指令全文（web-spec §4）。
// 理由不是隱私（那是借用者自己的內容），是畫面會被洗版 ——
// 一個 bash 指令可能二十行。

import type { StreamItem } from "./api";

export type Line =
  | { kind: "tool"; name: string; target?: string }
  | { kind: "text"; text: string }
  | { kind: "thinking" }
  | { kind: "end"; status: string };

interface ContentBlock {
  type?: string;
  name?: string;
  text?: string;
  input?: Record<string, unknown>;
}

const TARGET_KEYS = ["file_path", "path", "pattern", "url", "notebook_path"];

function targetOf(input: Record<string, unknown> | undefined): string | undefined {
  if (!input) return undefined;
  for (const key of TARGET_KEYS) {
    const value = input[key];
    if (typeof value === "string") return value.split("/").pop();
  }
  return undefined;
}

export function toLine(item: StreamItem): Line | null {
  const p = item.payload as {
    type?: string;
    status?: string;
    message?: { content?: ContentBlock[] };
  };

  if (p.type === "stream_end") return { kind: "end", status: p.status ?? "done" };
  if (p.type !== "assistant") return null;

  for (const block of p.message?.content ?? []) {
    if (block.type === "tool_use" && block.name)
      return { kind: "tool", name: block.name, target: targetOf(block.input) };
    if (block.type === "text" && block.text?.trim())
      return { kind: "text", text: block.text };
    if (block.type === "thinking") return { kind: "thinking" };
  }
  return null;
}

const VERB: Record<string, string> = {
  Read: "正在讀取",
  Write: "正在寫入",
  Edit: "正在修改",
  Bash: "正在執行指令",
  Glob: "正在尋找檔案",
  Grep: "正在搜尋",
};

export function describe(line: Extract<Line, { kind: "tool" }>): string {
  const verb = VERB[line.name] ?? `正在使用 ${line.name}`;
  return line.target ? `${verb} ${line.target}` : verb;
}
