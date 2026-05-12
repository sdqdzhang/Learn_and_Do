import { useCallback, useEffect, type MouseEvent } from "react";
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
import { ingestTraceJsonlLine, resetTraceView, selectTraceNode } from "./trace";

const nodeTypes = { cognitive: CognitiveNode, execution: ExecutionNode };
const edgeTypes = { dataFlow: DataFlowEdge };

export default function App() {
  const nodes = useTraceStore((s) => s.nodes);
  const edges = useTraceStore((s) => s.edges);
  const selectedEvent = useTraceStore((s) => s.selectedEvent);

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    useTraceStore.setState((s) => ({ nodes: applyNodeChanges(changes, s.nodes) }));
  }, []);

  const onEdgesChange = useCallback((changes: EdgeChange[]) => {
    useTraceStore.setState((s) => ({ edges: applyEdgeChanges(changes, s.edges) }));
  }, []);

  useEffect(() => {
    resetTraceView();
    const lines = sampleTrace.split(/\r?\n/).filter((l) => l.trim());
    let i = 0;
    const tick = window.setInterval(() => {
      if (i >= lines.length) {
        clearInterval(tick);
        return;
      }
      const line = lines[i++];
      try {
        ingestTraceJsonlLine(line);
      } catch (err) {
        console.error("trace line parse failed", err);
      }
    }, 420);
    return () => clearInterval(tick);
  }, []);

  /** 预留：接入后端 WebSocket 后，`ws.onmessage` → `ingestTraceJsonlLine(ev.data)` 或 `ingestTraceEvent(JSON.parse(...))`。 */
  useEffect(() => {
    return () => {};
  }, []);

  const onNodeClick = useCallback(
    (_event: MouseEvent, node: Node) => {
      selectTraceNode(node.id);
    },
    [],
  );

  const onPaneClick = useCallback(() => {
    selectTraceNode(null);
  }, []);

  return (
    <div className="flex h-full w-full bg-slate-950 text-slate-100">
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
            Mock：爬虫 OOM / 死锁剧本 · 贝塞尔边 + 分色箭头
          </div>
        </header>
      </div>

      <aside className="w-[380px] shrink-0 border-l border-slate-800 bg-slate-900/95 p-4 text-sm shadow-xl">
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
          节点载荷
        </h2>
        {selectedEvent ? (
          <pre className="max-h-[calc(100vh-6rem)] overflow-auto rounded border border-slate-800 bg-black/50 p-3 text-[11px] leading-relaxed text-slate-300">
            {JSON.stringify(selectedEvent, null, 2)}
          </pre>
        ) : (
          <p className="text-xs leading-relaxed text-slate-500">
            点击画布上的认知或执行节点，查看该步完整 TraceEvent（含 payload）。
          </p>
        )}
      </aside>
    </div>
  );
}
