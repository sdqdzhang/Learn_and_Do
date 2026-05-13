/** 与 WebSocket ``start`` 载荷及 ``GET /api/settings`` 对齐的 UI 状态。 */

export type LlmSettings = {
  baseUrl: string;
  apiKey: string;
  model: string;
  timeoutSeconds: number;
  maxRetries: number;
  temperature: number;
  summarizerModel: string;
};

export type WorkflowSettings = {
  interventionEnabled: boolean;
  interventionTimeoutS: number;
  sensitiveTools: string;
  multiReflectionRoles: string;
  guardPreflightEnabled: boolean;
  guardOutboundEnabled: boolean;
  guardMaxRounds: number;
  guardModel: string;
};

export type SessionSandboxSettings = {
  namePrefix: string;
  idleTimeoutSeconds: number;
  heartbeatIntervalSeconds: number;
};

export type ExecutorSettings = {
  image: string;
  namePrefix: string;
  workspaceDir: string;
  memoryLimit: string;
  cpuLimit: number;
  defaultTimeout: number;
};

export type ContextSettings = {
  maxTokens: number;
  preserveRecent: number;
  tiktokenEncoding: string;
};

export type SessionSettingsData = {
  mode: string;
  role: string;
  maxTurns: string;
  interventionTimeoutS: number;
  noSearch: boolean;
  noRag: boolean;
  llm: LlmSettings;
  workflow: WorkflowSettings;
  session: SessionSandboxSettings;
  executor: ExecutorSettings;
  context: ContextSettings;
};

export type ApiSettingsResponse = {
  defaults: {
    llm: {
      base_url: string;
      api_key_display: string;
      model: string;
      timeout_seconds: number;
      max_retries: number;
      temperature: number;
      summarizer_model: string | null;
    };
    workflow: {
      max_turns: number;
      intervention_enabled: boolean;
      intervention_timeout_s: number;
      sensitive_tools: string;
      multi_reflection_roles: string;
      guard_preflight_enabled: boolean;
      guard_outbound_enabled: boolean;
      guard_max_rounds: number;
      guard_model: string | null;
    };
    session: {
      name_prefix: string;
      idle_timeout_seconds: number;
      heartbeat_interval_seconds: number;
    };
    executor: {
      image: string;
      name_prefix: string;
      workspace_dir: string;
      memory_limit: string;
      cpu_limit: number;
      default_timeout: number;
    };
    context: {
      max_tokens: number;
      preserve_recent: number;
      tiktoken_encoding: string;
    };
  };
};
