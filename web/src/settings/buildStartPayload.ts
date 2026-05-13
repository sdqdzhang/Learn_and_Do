import type { SessionSettingsData } from "./types";

/** 组装 WebSocket 首帧 ``start`` JSON（嵌套键与 ``server/start_payload.py`` 一致）。 */
export function buildStartPayload(prompt: string, s: SessionSettingsData): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    type: "start",
    prompt: prompt.trim(),
    mode: s.mode,
    role: s.role,
    intervention_timeout_s: s.interventionTimeoutS,
    no_search: s.noSearch,
    no_rag: s.noRag,
    llm: {
      base_url: s.llm.baseUrl.trim(),
      api_key: s.llm.apiKey,
      model: s.llm.model.trim(),
      timeout_seconds: s.llm.timeoutSeconds,
      max_retries: s.llm.maxRetries,
      temperature: s.llm.temperature,
      summarizer_model: s.llm.summarizerModel.trim() || undefined,
    },
    workflow: {
      intervention_enabled: s.workflow.interventionEnabled,
      intervention_timeout_s: s.workflow.interventionTimeoutS,
      sensitive_tools: s.workflow.sensitiveTools.trim(),
      multi_reflection_roles: s.workflow.multiReflectionRoles.trim(),
      guard_preflight_enabled: s.workflow.guardPreflightEnabled,
      guard_outbound_enabled: s.workflow.guardOutboundEnabled,
      guard_max_rounds: s.workflow.guardMaxRounds,
      guard_model: s.workflow.guardModel.trim() || undefined,
    },
    session: {
      name_prefix: s.session.namePrefix.trim(),
      idle_timeout_seconds: s.session.idleTimeoutSeconds,
      heartbeat_interval_seconds: s.session.heartbeatIntervalSeconds,
    },
    executor: {
      image: s.executor.image.trim(),
      name_prefix: s.executor.namePrefix.trim(),
      workspace_dir: s.executor.workspaceDir.trim(),
      memory_limit: s.executor.memoryLimit.trim(),
      cpu_limit: s.executor.cpuLimit,
      default_timeout: s.executor.defaultTimeout,
    },
    context: {
      max_tokens: s.context.maxTokens,
      preserve_recent: s.context.preserveRecent,
      tiktoken_encoding: s.context.tiktokenEncoding.trim(),
    },
  };

  const mt = s.maxTurns.trim() === "" ? NaN : Number(s.maxTurns);
  if (Number.isFinite(mt) && mt >= 1) {
    payload.max_turns = Math.floor(mt);
  }

  return payload;
}
