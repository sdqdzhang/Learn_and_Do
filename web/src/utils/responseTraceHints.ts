/** 从 ``response`` 事件的 payload 取出供画布展示的「状态说明」行（与后端 workflow 写入的键对齐）。 */
export function extractResponseTraceHints(payload: Record<string, unknown>): string[] {
  const out: string[] = [];
  for (const k of ["session_context_note", "display_hint"] as const) {
    const v = payload[k];
    if (typeof v === "string" && v.trim()) {
      out.push(v.trim());
    }
  }
  return out;
}
