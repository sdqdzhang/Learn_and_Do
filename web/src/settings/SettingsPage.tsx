import { useMemo, useState, type ReactNode } from "react";

import { SETTINGS_FIELD_TIPS as FT } from "./fieldTips";
import { newLlmPresetId } from "./llmPresetMerge";
import { useSettingsStore } from "./store";

const inp =
  "rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-none focus:border-sky-600";
const secTitle = "mb-3 text-sm font-semibold text-sky-200";

function Lab({
  title,
  tip,
  children,
}: {
  title: string;
  /** 鼠标悬停在本行控件上时浏览器原生提示（简短说明）。 */
  tip?: string;
  children: ReactNode;
}) {
  return (
    <label
      className={`flex flex-col gap-0.5 ${tip ? "cursor-help" : ""}`}
      title={tip}
    >
      <span className="text-[11px] font-medium text-slate-400">{title}</span>
      {children}
    </label>
  );
}

type Props = { onBack: () => void };

export default function SettingsPage({ onBack }: Props) {
  const s = useSettingsStore();
  const [syncMsg, setSyncMsg] = useState("");
  const [newPresetLabel, setNewPresetLabel] = useState("");

  const presetSelectValue = useMemo(() => {
    const id = s.llm.activePresetId;
    if (id && s.llm.llmEndpointPresets.some((p) => p.id === id)) return id;
    return "";
  }, [s.llm.activePresetId, s.llm.llmEndpointPresets]);

  const applyPreset = (pr: (typeof s.llm.llmEndpointPresets)[number]) => {
    s.patchLlm({
      baseUrl: pr.baseUrl,
      model: pr.model,
      apiKey: pr.apiKey,
      activePresetId: pr.id,
    });
  };

  const addPresetFromForm = () => {
    const label = newPresetLabel.trim() || s.llm.model.trim() || "未命名端点";
    const baseUrl = s.llm.baseUrl.trim();
    const model = s.llm.model.trim();
    if (!baseUrl || !model) return;
    const id = newLlmPresetId();
    s.patchLlm({
      llmEndpointPresets: [
        ...s.llm.llmEndpointPresets,
        { id, label, baseUrl, model, apiKey: s.llm.apiKey },
      ],
      activePresetId: id,
    });
    setNewPresetLabel("");
  };

  const updateActivePreset = () => {
    const id = s.llm.activePresetId;
    if (!id) return;
    s.patchLlm({
      llmEndpointPresets: s.llm.llmEndpointPresets.map((p) =>
        p.id === id
          ? { ...p, baseUrl: s.llm.baseUrl.trim(), model: s.llm.model.trim(), apiKey: s.llm.apiKey }
          : p,
      ),
    });
  };

  return (
    <div className="flex h-full min-h-0 flex-col bg-slate-950 text-slate-100">
      <header className="flex shrink-0 flex-wrap items-center gap-2 border-b border-slate-800 bg-slate-900/95 px-4 py-3">
        <button
          type="button"
          onClick={onBack}
          className="rounded border border-slate-600 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800"
        >
          ← 返回轨迹
        </button>
        <h1 className="text-sm font-semibold text-slate-100">会话与运行环境设置</h1>
        <span className="text-[10px] text-slate-500">
          已持久化到浏览器 localStorage；启动任务时随 WebSocket 首帧发送给后端。
        </span>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <button
            type="button"
            className="rounded bg-sky-800 px-3 py-1.5 text-xs font-medium text-white hover:bg-sky-700"
            onClick={async () => {
              const r = await s.syncFromServer();
              setSyncMsg(r.ok ? r.message : r.message);
            }}
          >
            从服务器同步默认值
          </button>
          <button
            type="button"
            className="rounded border border-slate-600 px-3 py-1.5 text-xs text-slate-400 hover:bg-slate-800"
            onClick={() => {
              s.resetFactory();
              setSyncMsg("已恢复本页内置默认（未请求服务器）。");
            }}
          >
            恢复页面默认
          </button>
        </div>
      </header>
      {syncMsg ? (
        <div className="shrink-0 border-b border-slate-800 bg-slate-900/80 px-4 py-1.5 text-[11px] text-slate-400">
          {syncMsg}
        </div>
      ) : null}

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        <div className="mx-auto max-w-4xl space-y-4 pb-16">
          <section className="rounded-lg border border-slate-800 bg-slate-900/40 p-4">
            <h2 className={secTitle}>任务与角色（随 start 根字段发送）</h2>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <Lab title="mode" tip={FT.mode}>
                <select className={inp} value={s.mode} onChange={(e) => s.patch({ mode: e.target.value })}>
                  <option value="development">development</option>
                  <option value="philosophy">philosophy</option>
                </select>
              </Lab>
              <Lab title="role" tip={FT.role}>
                <select className={inp} value={s.role} onChange={(e) => s.patch({ role: e.target.value })}>
                  {[
                    "coder",
                    "reviewer",
                    "philosopher",
                    "investigator",
                    "planner",
                    "reflector",
                    "auditor",
                    "challenger",
                  ].map((r) => (
                    <option key={r} value={r}>
                      {r}
                    </option>
                  ))}
                </select>
              </Lab>
              <Lab title="max_turns" tip={FT.maxTurns}>
                <input
                  className={inp}
                  type="number"
                  min={1}
                  max={500}
                  placeholder="默认"
                  value={s.maxTurns}
                  onChange={(e) => s.patch({ maxTurns: e.target.value })}
                />
              </Lab>
              <Lab title="人类干预等待（秒）根级" tip={FT.interventionRoot}>
                <input
                  className={inp}
                  type="number"
                  min={0}
                  value={s.interventionTimeoutS}
                  onChange={(e) => s.patch({ interventionTimeoutS: Number(e.target.value) })}
                />
              </Lab>
              <Lab title="禁用 web_search" tip={FT.noSearch}>
                <input
                  type="checkbox"
                  className="h-4 w-4 accent-sky-600"
                  checked={s.noSearch}
                  onChange={(e) => s.patch({ noSearch: e.target.checked })}
                />
              </Lab>
              <Lab title="禁用 RAG 工具" tip={FT.noRag}>
                <input
                  type="checkbox"
                  className="h-4 w-4 accent-sky-600"
                  checked={s.noRag}
                  onChange={(e) => s.patch({ noRag: e.target.checked })}
                />
              </Lab>
            </div>
          </section>

          <section className="rounded-lg border border-slate-800 bg-slate-900/40 p-4">
            <h2 className={secTitle}>LLM（主对话与护栏共用同一 OpenAI 兼容端点）</h2>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="sm:col-span-2">
                <Lab title="端点预设（下拉填入 base_url / model / api_key）" tip={FT.llmPresetSelect}>
                  <select
                    className={inp}
                    value={presetSelectValue}
                    onChange={(e) => {
                      const v = e.target.value;
                      if (!v) {
                        s.patchLlm({ activePresetId: null });
                        return;
                      }
                      const pr = s.llm.llmEndpointPresets.find((p) => p.id === v);
                      if (pr) applyPreset(pr);
                    }}
                  >
                    <option value="">— 不使用预设（仅在下方手动编辑）—</option>
                    {s.llm.llmEndpointPresets.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.label}
                      </option>
                    ))}
                  </select>
                </Lab>
              </div>
              <Lab title="OLLAMA_BASE_URL / base_url" tip={FT.llmBaseUrl}>
                <input className={inp} value={s.llm.baseUrl} onChange={(e) => s.patchLlm({ baseUrl: e.target.value })} />
              </Lab>
              <Lab title="OLLAMA_API_KEY" tip={FT.llmApiKey}>
                <input
                  className={inp}
                  type="password"
                  autoComplete="off"
                  value={s.llm.apiKey}
                  onChange={(e) => s.patchLlm({ apiKey: e.target.value })}
                />
              </Lab>
              <Lab title="OLLAMA_MODEL" tip={FT.llmModel}>
                <input className={inp} value={s.llm.model} onChange={(e) => s.patchLlm({ model: e.target.value })} />
              </Lab>
              <div className="sm:col-span-2 flex flex-col gap-2 rounded border border-slate-800/80 bg-slate-950/50 p-3">
                <span className="text-[10px] font-medium text-slate-500" title={FT.llmEndpointPresetsHint}>
                  管理 OpenAI 兼容端点预设（仅本机浏览器；启动时仍只发送上方三项）
                </span>
                <div className="flex flex-wrap items-end gap-2">
                  <label className="flex min-w-[10rem] flex-1 flex-col gap-0.5">
                    <span className="text-[11px] text-slate-400">新预设显示名</span>
                    <input
                      className={inp}
                      placeholder="例如 本机 Ollama / 公司网关"
                      value={newPresetLabel}
                      onChange={(e) => setNewPresetLabel(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          addPresetFromForm();
                        }
                      }}
                    />
                  </label>
                  <button
                    type="button"
                    className="rounded border border-slate-600 px-2 py-1.5 text-[11px] text-slate-300 hover:bg-slate-800"
                    onClick={addPresetFromForm}
                  >
                    将下方三项存为新预设
                  </button>
                  <button
                    type="button"
                    disabled={!s.llm.activePresetId}
                    className="rounded border border-slate-600 px-2 py-1.5 text-[11px] text-slate-300 hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-40"
                    onClick={updateActivePreset}
                  >
                    写回当前选中预设
                  </button>
                </div>
                {s.llm.llmEndpointPresets.length > 0 ? (
                  <ul className="divide-y divide-slate-800/80 overflow-hidden rounded border border-slate-800/80">
                    {s.llm.llmEndpointPresets.map((p) => (
                      <li key={p.id} className="flex flex-wrap items-center gap-2 bg-slate-950/30 px-2 py-1.5 text-[11px]">
                        <span className="font-medium text-slate-200">{p.label}</span>
                        <span className="min-w-0 flex-1 truncate text-slate-500" title={`${p.model} · ${p.baseUrl}`}>
                          {p.model} · {p.baseUrl}
                        </span>
                        <button
                          type="button"
                          className="shrink-0 rounded border border-slate-600 px-2 py-0.5 text-slate-300 hover:bg-slate-800"
                          onClick={() => applyPreset(p)}
                        >
                          应用
                        </button>
                        <button
                          type="button"
                          className="shrink-0 rounded border border-slate-700 px-2 py-0.5 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
                          onClick={() => {
                            const next = s.llm.llmEndpointPresets.filter((x) => x.id !== p.id);
                            const nextActive = s.llm.activePresetId === p.id ? null : s.llm.activePresetId;
                            s.patchLlm({ llmEndpointPresets: next, activePresetId: nextActive });
                          }}
                        >
                          删除
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
              <Lab title="LLM_TIMEOUT_SECONDS" tip={FT.llmTimeout}>
                <input
                  className={inp}
                  type="number"
                  min={5}
                  value={s.llm.timeoutSeconds}
                  onChange={(e) => s.patchLlm({ timeoutSeconds: Number(e.target.value) })}
                />
              </Lab>
              <Lab title="LLM_MAX_RETRIES" tip={FT.llmRetries}>
                <input
                  className={inp}
                  type="number"
                  min={0}
                  max={10}
                  value={s.llm.maxRetries}
                  onChange={(e) => s.patchLlm({ maxRetries: Number(e.target.value) })}
                />
              </Lab>
              <Lab title="LLM_TEMPERATURE" tip={FT.llmTemperature}>
                <input
                  className={inp}
                  type="number"
                  step={0.1}
                  min={0}
                  max={2}
                  value={s.llm.temperature}
                  onChange={(e) => s.patchLlm({ temperature: Number(e.target.value) })}
                />
              </Lab>
              <Lab title="LLM_SUMMARIZER_MODEL" tip={FT.summarizerModel}>
                <input
                  className={inp}
                  value={s.llm.summarizerModel}
                  onChange={(e) => s.patchLlm({ summarizerModel: e.target.value })}
                />
              </Lab>
            </div>
          </section>

          <section className="rounded-lg border border-slate-800 bg-slate-900/40 p-4">
            <h2 className={secTitle}>Workflow（与 WORKFLOW_* 环境变量对应）</h2>
            <div className="grid gap-3 sm:grid-cols-2">
              <Lab title="workflow.intervention_enabled" tip={FT.wfIvEnabled}>
                <input
                  type="checkbox"
                  className="h-4 w-4 accent-sky-600"
                  checked={s.workflow.interventionEnabled}
                  onChange={(e) => s.patchWorkflow({ interventionEnabled: e.target.checked })}
                />
              </Lab>
              <Lab title="workflow.intervention_timeout_s" tip={FT.wfIvTimeout}>
                <input
                  className={inp}
                  type="number"
                  min={0}
                  value={s.workflow.interventionTimeoutS}
                  onChange={(e) => s.patchWorkflow({ interventionTimeoutS: Number(e.target.value) })}
                />
              </Lab>
              <Lab title="WORKFLOW_SENSITIVE_TOOLS" tip={FT.wfSensitive}>
                <input
                  className={inp}
                  value={s.workflow.sensitiveTools}
                  onChange={(e) => s.patchWorkflow({ sensitiveTools: e.target.value })}
                />
              </Lab>
              <Lab title="WORKFLOW_MULTI_REFLECTION_ROLES" tip={FT.wfMultiRoles}>
                <input
                  className={inp}
                  value={s.workflow.multiReflectionRoles}
                  onChange={(e) => s.patchWorkflow({ multiReflectionRoles: e.target.value })}
                />
              </Lab>
              <Lab title="WORKFLOW_GUARD_PREFLIGHT" tip={FT.wfGuardPre}>
                <input
                  type="checkbox"
                  className="h-4 w-4 accent-sky-600"
                  checked={s.workflow.guardPreflightEnabled}
                  onChange={(e) => s.patchWorkflow({ guardPreflightEnabled: e.target.checked })}
                />
              </Lab>
              <Lab title="WORKFLOW_GUARD_OUTBOUND" tip={FT.wfGuardOut}>
                <input
                  type="checkbox"
                  className="h-4 w-4 accent-sky-600"
                  checked={s.workflow.guardOutboundEnabled}
                  onChange={(e) => s.patchWorkflow({ guardOutboundEnabled: e.target.checked })}
                />
              </Lab>
              <Lab title="WORKFLOW_GUARD_MAX_ROUNDS（1–5）" tip={FT.wfGuardRounds}>
                <input
                  className={inp}
                  type="number"
                  min={1}
                  max={5}
                  value={s.workflow.guardMaxRounds}
                  onChange={(e) => s.patchWorkflow({ guardMaxRounds: Number(e.target.value) })}
                />
              </Lab>
              <Lab title="WORKFLOW_GUARD_MODEL" tip={FT.wfGuardModel}>
                <input
                  className={inp}
                  value={s.workflow.guardModel}
                  onChange={(e) => s.patchWorkflow({ guardModel: e.target.value })}
                />
              </Lab>
            </div>
          </section>

          <section className="rounded-lg border border-slate-800 bg-slate-900/40 p-4">
            <h2 className={secTitle}>短期上下文（CONTEXT_*）</h2>
            <div className="grid gap-3 sm:grid-cols-2">
              <Lab title="CONTEXT_MAX_TOKENS" tip={FT.ctxMaxTokens}>
                <input
                  className={inp}
                  type="number"
                  min={512}
                  value={s.context.maxTokens}
                  onChange={(e) => s.patchContext({ maxTokens: Number(e.target.value) })}
                />
              </Lab>
              <Lab title="CONTEXT_PRESERVE_RECENT" tip={FT.ctxPreserve}>
                <input
                  className={inp}
                  type="number"
                  min={1}
                  max={64}
                  value={s.context.preserveRecent}
                  onChange={(e) => s.patchContext({ preserveRecent: Number(e.target.value) })}
                />
              </Lab>
              <Lab title="CONTEXT_TIKTOKEN_ENCODING" tip={FT.ctxEncoding}>
                <input
                  className={inp}
                  value={s.context.tiktokenEncoding}
                  onChange={(e) => s.patchContext({ tiktokenEncoding: e.target.value })}
                />
              </Lab>
            </div>
          </section>

          <section className="rounded-lg border border-slate-800 bg-slate-900/40 p-4">
            <h2 className={secTitle}>Docker 沙箱 · Executor（DOCKER_* / EXECUTOR_*）</h2>
            <div className="grid gap-3 sm:grid-cols-2">
              <Lab title="DOCKER_BASE_IMAGE" tip={FT.exImage}>
                <input className={inp} value={s.executor.image} onChange={(e) => s.patchExecutor({ image: e.target.value })} />
              </Lab>
              <Lab title="容器名前缀（executor.name_prefix）" tip={FT.exNamePrefix}>
                <input
                  className={inp}
                  value={s.executor.namePrefix}
                  onChange={(e) => s.patchExecutor({ namePrefix: e.target.value })}
                />
              </Lab>
              <Lab title="WORKSPACE_DIR" tip={FT.exWorkspace}>
                <input
                  className={inp}
                  value={s.executor.workspaceDir}
                  onChange={(e) => s.patchExecutor({ workspaceDir: e.target.value })}
                />
              </Lab>
              <Lab title="CONTAINER_MEMORY_LIMIT" tip={FT.exMemory}>
                <input
                  className={inp}
                  value={s.executor.memoryLimit}
                  onChange={(e) => s.patchExecutor({ memoryLimit: e.target.value })}
                />
              </Lab>
              <Lab title="CONTAINER_CPU_LIMIT" tip={FT.exCpu}>
                <input
                  className={inp}
                  type="number"
                  step={0.1}
                  min={0.1}
                  value={s.executor.cpuLimit}
                  onChange={(e) => s.patchExecutor({ cpuLimit: Number(e.target.value) })}
                />
              </Lab>
              <Lab title="EXECUTOR_DEFAULT_TIMEOUT（秒）" tip={FT.exTimeout}>
                <input
                  className={inp}
                  type="number"
                  min={5}
                  value={s.executor.defaultTimeout}
                  onChange={(e) => s.patchExecutor({ defaultTimeout: Number(e.target.value) })}
                />
              </Lab>
            </div>
          </section>

          <section className="rounded-lg border border-slate-800 bg-slate-900/40 p-4">
            <h2 className={secTitle}>Session 心跳（CONTAINER_NAME_PREFIX / 空闲与心跳）</h2>
            <div className="grid gap-3 sm:grid-cols-2">
              <Lab title="CONTAINER_NAME_PREFIX（session 名前缀）" tip={FT.sessPrefix}>
                <input
                  className={inp}
                  value={s.session.namePrefix}
                  onChange={(e) => s.patchSession({ namePrefix: e.target.value })}
                />
              </Lab>
              <Lab title="CONTAINER_IDLE_TIMEOUT_SECONDS" tip={FT.sessIdle}>
                <input
                  className={inp}
                  type="number"
                  min={60}
                  value={s.session.idleTimeoutSeconds}
                  onChange={(e) => s.patchSession({ idleTimeoutSeconds: Number(e.target.value) })}
                />
              </Lab>
              <Lab title="CONTAINER_HEARTBEAT_INTERVAL_SECONDS" tip={FT.sessHeartbeat}>
                <input
                  className={inp}
                  type="number"
                  min={10}
                  value={s.session.heartbeatIntervalSeconds}
                  onChange={(e) => s.patchSession({ heartbeatIntervalSeconds: Number(e.target.value) })}
                />
              </Lab>
            </div>
          </section>

          <p className="text-[10px] leading-relaxed text-slate-600">
            未覆盖项（如 VECTOR_*、CORS_ORIGINS、TRACE_DIR）仍由服务端进程环境变量决定。修改 Docker
            镜像或工作区后请确认本机已构建镜像且路径存在。
          </p>
        </div>
      </div>
    </div>
  );
}
