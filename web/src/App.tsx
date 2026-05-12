import { useCallback, useEffect, useRef, useState, type MouseEvent } from "react";
import ReactFlow, {
  applyEdgeChanges,
  applyNodeChanges,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  type EdgeChange,
  type Node,
  type NodeChange,
} from "reactflow";
import "reactflow/dist/style.css";

import { DataFlowEdge } from "./components/edges/DataFlowEdge";
import CognitiveNode from "./components/nodes/CognitiveNode";
import ExecutionNode from "./components/nodes/ExecutionNode";
import sampleTrace from "./mocks/sample_trace.jsonl?raw";
import { useTraceStore } from "./store/useTraceStore";
import { ingestTraceEvent, ingestTraceJsonlLine, resetTraceView, selectTraceNode } from "./trace";
import type { TraceEvent } from "./types/trace";

const nodeTypes = { cognitive: CognitiveNode, execution: ExecutionNode };
const edgeTypes = { dataFlow: DataFlowEdge };

function wsSessionUrl(): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws/session`;
}

export default function App() {
  const nodes = useTraceStore((s) => s.nodes);
  const edges = useTraceStore((s) => s.edges);
  const selectedEvent = useTraceStore((s) => s.selectedEvent);

  const [prompt, setPrompt] = useState("");
  const [mode, setMode] = useState("development");
  const [role, setRole] = useState("coder");
  const [interventionSec, setInterventionSec] = useState(120);
  const [sessionStatus, setSessionStatus] = useState<"idle" | "running" | "done" | "error">("idle");
  const [statusNote, setStatusNote] = useState("");
  const [intervention, setIntervention] = useState<{ open: boolean; phase: string }>({
    open: false,
    phase: "",
  });
  const [humanDraft, setHumanDraft] = useState("");
  const wsRef = useRef<WebSocket | null>(null);
  const mockIntervalRef = useRef<number | null>(null);

  const clearMockInterval = useCallback(() => {
    if (mockIntervalRef.current != null) {
      window.clearInterval(mockIntervalRef.current);
      mockIntervalRef.current = null;
    }
  }, []);

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    useTraceStore.setState((s) => ({ nodes: applyNodeChanges(changes, s.nodes) }));
  }, []);

  const onEdgesChange = useCallback((changes: EdgeChange[]) => {
    useTraceStore.setState((s) => ({ edges: applyEdgeChanges(changes, s.edges) }));
  }, []);

  const playMock = useCallback(() => {
    clearMockInterval();
    resetTraceView();
    setSessionStatus("running");
    setStatusNote("Mock 回放中…");
    const lines = sampleTrace.split(/\r?\n/).filter((l) => l.trim());
    let i = 0;
    mockIntervalRef.current = window.setInterval(() => {
      if (i >= lines.length) {
        clearMockInterval();
        setSessionStatus("done");
        setStatusNote("Mock 回放结束");
        return;
      }
      const line = lines[i++];
      try {
        ingestTraceJsonlLine(line);
      } catch (err) {
        console.error(err);
      }
    }, 380);
  }, [clearMockInterval]);

  const stopLive = useCallback(() => {
    wsRef.current?.close();
    wsRef.current = null;
  }, []);

  const startLive = useCallback(() => {
    const p = prompt.trim();
    if (!p) {
      setStatusNote("请先填写任务描述");
      return;
    }
    clearMockInterval();
    stopLive();
    resetTraceView();
    setSessionStatus("running");
    setStatusNote("已连接后端，等待轨迹…");
    setIntervention({ open: false, phase: "" });

    const ws = new WebSocket(wsSessionUrl());
    wsRef.current = ws;

    ws.onopen = () => {
      ws.send(
        JSON.stringify({
          type: "start",
          prompt: p,
          mode,
          role,
          no_search: true,
          no_rag: true,
          intervention_timeout_s: interventionSec > 0 ? interventionSec : undefined,
        }),
      );
    };

    ws.onmessage = (ev) => {
      const raw = ev.data as string;
      let o: Record<string, unknown>;
      try {
        o = JSON.parse(raw) as Record<string, unknown>;
      } catch {
        return;
      }
      if (o.type === "done") {
        setSessionStatus("done");
        setStatusNote(`完成：${String(o.final_state ?? "")} · ${String(o.turns ?? "")} 轮`);
        ws.close();
        return;
      }
      if (o.type === "error") {
        setSessionStatus("error");
        setStatusNote(String(o.message ?? "error"));
        ws.close();
        return;
      }
      try {
        const te = o as unknown as TraceEvent;
        ingestTraceEvent(te);
        if (te.kind === "intervention_suspend") {
          const ph = (te.payload as { phase?: string } | undefined)?.phase ?? "";
          setIntervention({ open: true, phase: ph });
        }
      } catch (e) {
        console.error("ingest failed", e);
      }
    };

    ws.onerror = () => {
      setSessionStatus("error");
      setStatusNote("WebSocket 错误（请确认已启动：uvicorn server.app:app --port 8765）");
    };

    ws.onclose = () => {
      wsRef.current = null;
    };
  }, [prompt, mode, role, interventionSec, stopLive, clearMockInterval]);

  useEffect(
    () => () => {
      clearMockInterval();
      stopLive();
    },
    [clearMockInterval, stopLive],
  );

  const sendHuman = useCallback(() => {
    const w = wsRef.current;
    if (!w || w.readyState !== WebSocket.OPEN) return;
    const text = humanDraft.trim();
    if (!text) return;
    w.send(JSON.stringify({ type: "human", text }));
    setHumanDraft("");
    setIntervention({ open: false, phase: "" });
  }, [humanDraft]);

  const onNodeClick = useCallback((_event: MouseEvent, node: Node) => {
    selectTraceNode(node.id);
  }, []);

  const onPaneClick = useCallback(() => {
    selectTraceNode(null);
  }, []);

  return (
    <div className="flex h-full w-full flex-col bg-slate-950 text-slate-100">
      <div className="z-20 flex shrink-0 flex-wrap items-end gap-2 border-b border-slate-800 bg-slate-900/95 px-3 py-2">
        <label className="flex flex-col text-[10px] text-slate-500">
          任务
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={2}
            className="w-64 resize-none rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200"
            placeholder="用自然语言描述要做的任务…"
          />
        </label>
        <label className="flex flex-col text-[10px] text-slate-500">
          mode
          <select
            value={mode}
            onChange={(e) => setMode(e.target.value)}
            className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs"
          >
            <option value="development">development</option>
            <option value="philosophy">philosophy</option>
          </select>
        </label>
        <label className="flex flex-col text-[10px] text-slate-500">
          role
          <select
            value={role}
            onChange={(e) => setRole(e.target.value)}
            className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs"
          >
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
        </label>
        <label className="flex flex-col text-[10px] text-slate-500">
          人类干预等待(秒)
          <input
            type="number"
            min={0}
            value={interventionSec}
            onChange={(e) => setInterventionSec(Number(e.target.value))}
            className="w-20 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs"
          />
        </label>
        <button
          type="button"
          onClick={startLive}
          disabled={sessionStatus === "running"}
          className="rounded bg-emerald-700 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-600 disabled:opacity-40"
        >
          启动实时任务
        </button>
        <button
          type="button"
          onClick={playMock}
          className="rounded border border-slate-600 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800"
        >
          仅 Mock 回放
        </button>
        <button
          type="button"
          onClick={() => {
            clearMockInterval();
            stopLive();
            resetTraceView();
            setSessionStatus("idle");
            setStatusNote("");
          }}
          className="rounded border border-slate-600 px-3 py-1.5 text-xs text-slate-400 hover:bg-slate-800"
        >
          清空画布
        </button>
        <span className="ml-auto text-[10px] text-slate-500">
          状态：{sessionStatus}
          {statusNote ? ` · ${statusNote}` : ""}
        </span>
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="relative min-w-0 flex-1">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            defaultEdgeOptions={{ type: "dataFlow", animated: false }}
            fitView
            minZoom={0.2}
            maxZoom={1.4}
            onNodeClick={onNodeClick}
            onPaneClick={onPaneClick}
            proOptions={{ hideAttribution: true }}
          >
            <Background variant={BackgroundVariant.Dots} gap={14} size={1} color="#334155" />
            <Controls className="!bg-slate-900/90 !border-slate-700 !shadow-lg" />
            <MiniMap
              nodeStrokeWidth={3}
              zoomable
              pannable
              className="!bg-slate-900/95 !border !border-slate-700"
              maskColor="rgba(15,23,42,0.85)"
              nodeColor={(n) => (n.type === "cognitive" ? "#475569" : "#0f766e")}
            />
          </ReactFlow>
          <header className="pointer-events-none absolute left-4 top-3 z-10 rounded-md border border-slate-700/80 bg-slate-900/80 px-3 py-2 text-xs text-slate-300 backdrop-blur">
            <div className="font-semibold text-slate-100">Agent 轨迹 · 知 / 行</div>
            <div className="mt-0.5 text-[10px] text-slate-500">
              实时：WebSocket 推 TraceEvent · 干预点弹窗回复
            </div>
          </header>
        </div>

        <aside className="w-[380px] shrink-0 border-l border-slate-800 bg-slate-900/95 p-4 text-sm shadow-xl">
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
            节点载荷
          </h2>
          {selectedEvent ? (
            <pre className="max-h-[calc(100vh-10rem)] overflow-auto rounded border border-slate-800 bg-black/50 p-3 text-[11px] leading-relaxed text-slate-300">
              {JSON.stringify(selectedEvent, null, 2)}
            </pre>
          ) : (
            <p className="text-xs leading-relaxed text-slate-500">
              点击画布节点查看 JSON。先启动后端：<code className="text-slate-400">uvicorn server.app:app --port 8765</code>
              ，再点「启动实时任务」。
            </p>
          )}
        </aside>
      </div>

      {intervention.open ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="max-w-md rounded-lg border border-amber-600/50 bg-slate-900 p-4 shadow-xl">
            <h3 className="text-sm font-semibold text-amber-200">Agent 请求人类输入</h3>
            <p className="mt-1 text-xs text-slate-400">阶段：{intervention.phase || "（未标注）"}</p>
            <textarea
              value={humanDraft}
              onChange={(e) => setHumanDraft(e.target.value)}
              rows={4}
              className="mt-3 w-full rounded border border-slate-600 bg-black/40 px-2 py-1 text-sm text-slate-100"
              placeholder="在此输入指令或澄清…"
            />
            <div className="mt-3 flex justify-end gap-2">
              <button
                type="button"
                className="rounded px-3 py-1 text-xs text-slate-400 hover:bg-slate-800"
                onClick={() => setIntervention({ open: false, phase: "" })}
              >
                稍后
              </button>
              <button
                type="button"
                onClick={sendHuman}
                className="rounded bg-amber-600 px-3 py-1 text-xs font-semibold text-black hover:bg-amber-500"
              >
                发送给 Agent
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
