import type { LlmEndpointPreset } from "./types";

function endpointKey(baseUrl: string, model: string): string {
  return `${baseUrl.trim()}\0${model.trim()}`;
}

export function newLlmPresetId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `p-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

/** 服务端返回的预设行（snake_case）；api_key 通常为空（不在 HTTP 中暴露密钥）。 */
export type ApiLlmEndpointPresetRow = {
  id?: string | null;
  label?: string | null;
  base_url?: string | null;
  model?: string | null;
  api_key?: string | null;
};

/** 将 ``GET /api/settings`` 的端点预设与本地列表合并：同 base_url+model 时保留本地已填的 api_key。 */
export function mergeLlmEndpointPresetsFromApi(
  local: LlmEndpointPreset[],
  rows: ApiLlmEndpointPresetRow[] | null | undefined,
): LlmEndpointPreset[] {
  if (!Array.isArray(rows) || rows.length === 0) {
    return local;
  }
  const localByKey = new Map(local.map((p) => [endpointKey(p.baseUrl, p.model), p]));
  const out: LlmEndpointPreset[] = [];
  let i = 0;
  for (const row of rows) {
    const baseUrl = String(row.base_url ?? "").trim();
    const model = String(row.model ?? "").trim();
    if (!baseUrl || !model) continue;
    const k = endpointKey(baseUrl, model);
    const existing = localByKey.get(k);
    const id =
      existing?.id ??
      (typeof row.id === "string" && row.id.trim() ? row.id.trim() : `srv-${i}`);
    i += 1;
    const fromApiKey = typeof row.api_key === "string" ? row.api_key : "";
    out.push({
      id,
      label: String(row.label ?? model).trim() || model,
      baseUrl,
      model,
      apiKey: existing?.apiKey ?? fromApiKey,
    });
  }
  for (const p of local) {
    if (!out.some((o) => endpointKey(o.baseUrl, o.model) === endpointKey(p.baseUrl, p.model))) {
      out.push(p);
    }
  }
  return out;
}
