/** 从 response 载荷中取出 thought 列表：优先 `thoughts`，否则从 `content` 中正则回退。 */
export function extractThoughts(payload: Record<string, unknown>): string[] {
  const direct = payload.thoughts;
  if (Array.isArray(direct)) {
    return direct.filter((x): x is string => typeof x === "string" && x.trim().length > 0);
  }
  const content = payload.content;
  if (typeof content !== "string" || !content.trim()) {
    return [];
  }
  const thoughts: string[] = [];
  const re = /<thought>([\s\S]*?)<\/thought>/gi;
  let m: RegExpExecArray | null;
  while ((m = re.exec(content)) !== null) {
    const t = m[1]?.trim();
    if (t) thoughts.push(t);
  }
  if (thoughts.length === 0 && content.trim()) {
    thoughts.push(content.slice(0, 400) + (content.length > 400 ? "…" : ""));
  }
  return thoughts;
}

export function normalizeUsage(payload: Record<string, unknown>): Record<string, unknown> | null {
  const u = payload.usage;
  if (u && typeof u === "object" && !Array.isArray(u)) {
    return u as Record<string, unknown>;
  }
  return null;
}

export function formatUsageLine(usage: Record<string, unknown> | null): string {
  if (!usage) return "Token：—";
  const pt = usage.prompt_tokens;
  const ct = usage.completion_tokens;
  const tt = usage.total_tokens;
  const parts: string[] = [];
  if (typeof pt === "number") parts.push(`prompt ${pt}`);
  if (typeof ct === "number") parts.push(`completion ${ct}`);
  if (typeof tt === "number") parts.push(`total ${tt}`);
  return parts.length ? `Token：${parts.join(" · ")}` : "Token：—";
}
