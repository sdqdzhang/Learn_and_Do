import type { OutputNodeData } from "../types/trace";

/** 从 ``tool_result`` 的 payload 抽出画布输出节点所需的 stdout / stderr / 摘要行。 */
export function extractToolOutputParts(
  payload: Record<string, unknown>,
): Pick<OutputNodeData, "stdout" | "stderr" | "summary"> {
  const raw = payload.output;
  if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    const o = raw as Record<string, unknown>;
    let stdout = typeof o.stdout === "string" ? o.stdout : "";
    let stderr = typeof o.stderr === "string" ? o.stderr : "";
    const metrics =
      o.metrics && typeof o.metrics === "object" ? (o.metrics as Record<string, unknown>) : null;
    const elapsed =
      metrics && typeof metrics.elapsed_ms === "number" ? `${metrics.elapsed_ms}ms` : "";
    const code =
      metrics && typeof metrics.exit_code === "number" ? `exit ${metrics.exit_code}` : "";
    let summary =
      [code, elapsed].filter(Boolean).join(" · ") ||
      (stdout.trim() ? stdout.trim().split("\n")[0]!.slice(0, 96) : "");

    const hasProcessStreams = "stdout" in o || "stderr" in o;
    if (!hasProcessStreams && Object.keys(o).length > 0) {
      const json = JSON.stringify(o, null, 2);
      if (!stdout.trim() && !stderr.trim()) {
        stdout = json;
      }
      if (!summary) {
        summary = json.split("\n")[0]!.slice(0, 120);
      }
    }
    return { stdout, stderr, summary };
  }
  if (typeof raw === "string") {
    const t = raw.trim();
    return {
      stdout: raw,
      stderr: "",
      summary: t ? (t.split("\n")[0] ?? "").slice(0, 120) : "",
    };
  }
  if (raw != null) {
    const s = JSON.stringify(raw);
    return { stdout: s, stderr: "", summary: s.slice(0, 120) };
  }
  return { stdout: "", stderr: "", summary: "" };
}
