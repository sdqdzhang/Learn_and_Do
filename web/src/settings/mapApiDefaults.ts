import { createDefaultSessionSettings } from "./defaults";
import type { ApiSettingsResponse, SessionSettingsData } from "./types";

/** 将 ``GET /api/settings`` 的 ``defaults`` 合并进当前 UI 形状（不覆盖 apiKey）。 */
export function mergeApiDefaultsIntoSettings(api: ApiSettingsResponse["defaults"]): SessionSettingsData {
  const base = createDefaultSessionSettings();
  const { llm, workflow, session, executor, context } = api;
  return {
    ...base,
    maxTurns: String(workflow.max_turns ?? ""),
    interventionTimeoutS: workflow.intervention_timeout_s ?? 0,
    workflow: {
      ...base.workflow,
      interventionEnabled: workflow.intervention_enabled,
      interventionTimeoutS: workflow.intervention_timeout_s,
      sensitiveTools: workflow.sensitive_tools ?? "",
      multiReflectionRoles: workflow.multi_reflection_roles ?? "",
      guardPreflightEnabled: workflow.guard_preflight_enabled,
      guardOutboundEnabled: workflow.guard_outbound_enabled,
      guardMaxRounds: workflow.guard_max_rounds,
      guardModel: workflow.guard_model ?? "",
    },
    llm: {
      ...base.llm,
      baseUrl: llm.base_url,
      model: llm.model,
      timeoutSeconds: llm.timeout_seconds,
      maxRetries: llm.max_retries,
      temperature: llm.temperature,
      summarizerModel: llm.summarizer_model ?? "",
    },
    session: {
      namePrefix: session.name_prefix,
      idleTimeoutSeconds: session.idle_timeout_seconds,
      heartbeatIntervalSeconds: session.heartbeat_interval_seconds,
    },
    executor: {
      image: executor.image,
      namePrefix: executor.name_prefix,
      workspaceDir: executor.workspace_dir,
      memoryLimit: executor.memory_limit,
      cpuLimit: executor.cpu_limit,
      defaultTimeout: executor.default_timeout,
    },
    context: {
      maxTokens: context.max_tokens,
      preserveRecent: context.preserve_recent,
      tiktokenEncoding: context.tiktoken_encoding,
    },
  };
}
