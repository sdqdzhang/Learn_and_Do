import { create } from "zustand";
import { persist } from "zustand/middleware";

import { buildStartPayload } from "./buildStartPayload";
import { createDefaultSessionSettings } from "./defaults";
import { mergeApiDefaultsIntoSettings } from "./mapApiDefaults";
import type { ApiSettingsResponse, SessionSettingsData } from "./types";

type SettingsStore = SessionSettingsData & {
  patch: (partial: Partial<SessionSettingsData>) => void;
  patchLlm: (p: Partial<SessionSettingsData["llm"]>) => void;
  patchWorkflow: (p: Partial<SessionSettingsData["workflow"]>) => void;
  patchSession: (p: Partial<SessionSettingsData["session"]>) => void;
  patchExecutor: (p: Partial<SessionSettingsData["executor"]>) => void;
  patchContext: (p: Partial<SessionSettingsData["context"]>) => void;
  resetFactory: () => void;
  syncFromServer: () => Promise<{ ok: boolean; message: string }>;
  buildStart: (prompt: string) => Record<string, unknown>;
};

function stripActions(state: SettingsStore): SessionSettingsData {
  const {
    patch,
    patchLlm,
    patchWorkflow,
    patchSession,
    patchExecutor,
    patchContext,
    resetFactory,
    syncFromServer,
    buildStart,
    ...data
  } = state;
  return data;
}

function pickActions(s: SettingsStore): Pick<
  SettingsStore,
  | "patch"
  | "patchLlm"
  | "patchWorkflow"
  | "patchSession"
  | "patchExecutor"
  | "patchContext"
  | "resetFactory"
  | "syncFromServer"
  | "buildStart"
> {
  return {
    patch: s.patch,
    patchLlm: s.patchLlm,
    patchWorkflow: s.patchWorkflow,
    patchSession: s.patchSession,
    patchExecutor: s.patchExecutor,
    patchContext: s.patchContext,
    resetFactory: s.resetFactory,
    syncFromServer: s.syncFromServer,
    buildStart: s.buildStart,
  };
}

export const useSettingsStore = create<SettingsStore>()(
  persist(
    (set, get) => ({
      ...createDefaultSessionSettings(),
      patch: (partial) => set((s) => ({ ...s, ...partial })),
      patchLlm: (p) => set((s) => ({ llm: { ...s.llm, ...p } })),
      patchWorkflow: (p) => set((s) => ({ workflow: { ...s.workflow, ...p } })),
      patchSession: (p) => set((s) => ({ session: { ...s.session, ...p } })),
      patchExecutor: (p) => set((s) => ({ executor: { ...s.executor, ...p } })),
      patchContext: (p) => set((s) => ({ context: { ...s.context, ...p } })),
      resetFactory: () =>
        set((s) => ({
          ...createDefaultSessionSettings(),
          ...pickActions(s),
        })),
      syncFromServer: async () => {
        try {
          const r = await fetch("/api/settings");
          if (!r.ok) {
            return { ok: false, message: `HTTP ${r.status}` };
          }
          const body = (await r.json()) as ApiSettingsResponse;
          const merged = mergeApiDefaultsIntoSettings(body.defaults, stripActions(get()));
          set((s) => ({
            ...merged,
            ...pickActions(s),
          }));
          return {
            ok: true,
            message:
              "已与服务器环境默认值对齐；端点预设与 LLM_ENDPOINT_PRESETS 合并（同 URL+模型时保留本地已填的 Key）。",
          };
        } catch {
          return { ok: false, message: "无法连接 /api/settings" };
        }
      },
      buildStart: (prompt: string) => buildStartPayload(prompt, stripActions(get())),
    }),
    {
      name: "tiny-devin-session-settings-v1",
      partialize: (s) => stripActions(s),
      merge: (persisted, current) => {
        const p = persisted as Partial<SessionSettingsData> | undefined;
        if (!p || typeof p !== "object") return current;
        return {
          ...current,
          ...p,
          llm: (() => {
            const layer = { ...current.llm, ...(p.llm ?? {}) };
            let presets = Array.isArray(layer.llmEndpointPresets)
              ? layer.llmEndpointPresets
              : current.llm.llmEndpointPresets;
            const legacy = (p.llm as { modelPresets?: string[] } | undefined)?.modelPresets;
            if (
              (!presets || presets.length === 0) &&
              Array.isArray(legacy) &&
              legacy.some((x) => String(x).trim())
            ) {
              const bu = (layer.baseUrl ?? current.llm.baseUrl).trim();
              const ak = layer.apiKey ?? current.llm.apiKey;
              presets = legacy
                .map((name) => String(name).trim())
                .filter(Boolean)
                .map((model, idx) => ({
                  id: `mig-${idx}-${model.replace(/[^\w.-]+/g, "_")}`,
                  label: model,
                  baseUrl: bu,
                  model,
                  apiKey: ak,
                }));
            }
            if (!Array.isArray(presets)) presets = current.llm.llmEndpointPresets;
            const activePresetId =
              p.llm && "activePresetId" in p.llm
                ? (layer.activePresetId as string | null)
                : current.llm.activePresetId;
            const out = { ...layer, llmEndpointPresets: presets, activePresetId } as Record<string, unknown>;
            delete out.modelPresets;
            return out as SessionSettingsData["llm"];
          })(),
          workflow: { ...current.workflow, ...(p.workflow ?? {}) },
          session: { ...current.session, ...(p.session ?? {}) },
          executor: { ...current.executor, ...(p.executor ?? {}) },
          context: { ...current.context, ...(p.context ?? {}) },
        };
      },
    },
  ),
);
