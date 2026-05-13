import type { SessionSettingsData } from "./types";

/** 与仓库 .env.example 大致对齐的初始值（无后端时仍可编辑）。 */
export function createDefaultSessionSettings(): SessionSettingsData {
  return {
    mode: "development",
    role: "coder",
    maxTurns: "",
    interventionTimeoutS: 0,
    noSearch: true,
    noRag: true,
    llm: {
      baseUrl: "http://localhost:11434/v1",
      apiKey: "ollama",
      model: "your-model-name",
      timeoutSeconds: 120,
      maxRetries: 2,
      temperature: 0.2,
      summarizerModel: "",
    },
    workflow: {
      interventionEnabled: false,
      interventionTimeoutS: 0,
      sensitiveTools: "",
      multiReflectionRoles: "",
      guardPreflightEnabled: false,
      guardOutboundEnabled: false,
      guardMaxRounds: 2,
      guardModel: "",
    },
    session: {
      namePrefix: "tiny-devin",
      idleTimeoutSeconds: 3600,
      heartbeatIntervalSeconds: 600,
    },
    executor: {
      image: "tiny-devin-base:latest",
      namePrefix: "tiny-devin",
      workspaceDir: "./runtime/workspace",
      memoryLimit: "2g",
      cpuLimit: 2,
      defaultTimeout: 60,
    },
    context: {
      maxTokens: 8000,
      preserveRecent: 4,
      tiktokenEncoding: "cl100k_base",
    },
  };
}
