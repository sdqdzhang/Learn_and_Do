import type { Edge, Node } from "reactflow";
import { create } from "zustand";
import type {
  ChainTip,
  CognitiveNodeData,
  ExecutionNodeData,
  OutputNodeData,
  TraceEvent,
} from "../types/trace";
import { edgePaint } from "../utils/edgeAppearance";
import { extractThoughts, normalizeUsage } from "../utils/thoughtAdapter";
import { extractToolOutputParts } from "../utils/toolOutput";

const COG_X = 48;
const EXEC_X = 560;
const OUTPUT_X = 920;
const ROW_GAP = 168;

function mapToolStatus(s: unknown): ExecutionNodeData["status"] {
  const v = String(s ?? "").toLowerCase();
  if (v === "success") return "SUCCESS";
  if (v === "failed") return "FAILED";
  return "PENDING";
}

function edgeHandles(
  prev: ChainTip["variant"],
  next: ChainTip["variant"],
): { sourceHandle: string; targetHandle: string } {
  if (prev === "cognitive" && next === "execution") {
    return { sourceHandle: "out-r", targetHandle: "in-l" };
  }
  if (prev === "execution" && next === "output") {
    return { sourceHandle: "out-r", targetHandle: "in-l" };
  }
  if (prev === "output" && next === "cognitive") {
    return { sourceHandle: "out-l", targetHandle: "in-r" };
  }
  if (prev === "execution" && next === "cognitive") {
    return { sourceHandle: "out-l", targetHandle: "in-r" };
  }
  if (prev === "cognitive" && next === "cognitive") {
    return { sourceHandle: "out-r", targetHandle: "in-r" };
  }
  return { sourceHandle: "out-r", targetHandle: "in-l" };
}

function attachOutputAfterExec(
  nodes: Node<CognitiveNodeData | ExecutionNodeData | OutputNodeData>[],
  edges: Edge[],
  edgeSeq: number,
  execId: string,
  event: TraceEvent,
): {
  nodes: Node<CognitiveNodeData | ExecutionNodeData | OutputNodeData>[];
  edges: Edge[];
  edgeSeq: number;
  lastChain: ChainTip;
} {
  const callId = String(event.payload.id ?? "");
  const outId = `out-${callId}`;
  const execNode = nodes.find((n) => n.id === execId);
  if (!execNode) {
    return {
      nodes,
      edges,
      edgeSeq,
      lastChain: { nodeId: execId, variant: "execution" },
    };
  }

  const parts = extractToolOutputParts(event.payload);
  const toolName = String(event.payload.name ?? "unknown_tool");
  const outData: OutputNodeData = {
    callId,
    toolName,
    ...parts,
    rawEvent: event,
  };

  const existing = nodes.find((n) => n.id === outId);
  if (existing && existing.type === "output") {
    const nextNodes = nodes.map((n) =>
      n.id === outId ? { ...n, data: outData } : n,
    );
    return {
      nodes: nextNodes,
      edges,
      edgeSeq,
      lastChain: { nodeId: outId, variant: "output" },
    };
  }

  const outNode: Node<OutputNodeData> = {
    id: outId,
    type: "output",
    position: { x: OUTPUT_X, y: execNode.position.y },
    data: outData,
  };

  let nextEdges = [...edges];
  let nextSeq = edgeSeq;
  const hasEdge = nextEdges.some((e) => e.source === execId && e.target === outId);
  if (!hasEdge) {
    nextSeq += 1;
    const { sourceHandle, targetHandle } = edgeHandles("execution", "output");
    const paint = edgePaint(edges.length);
    nextEdges.push({
      id: `e-${nextSeq}`,
      source: execId,
      target: outId,
      type: "dataFlow",
      animated: false,
      sourceHandle,
      targetHandle,
      style: { stroke: paint.stroke },
      data: { curvature: paint.curvature },
    });
  }

  return {
    nodes: [...nodes, outNode],
    edges: nextEdges,
    edgeSeq: nextSeq,
    lastChain: { nodeId: outId, variant: "output" },
  };
}

type TraceStore = {
  nodes: Node<CognitiveNodeData | ExecutionNodeData | OutputNodeData>[];
  edges: Edge[];
  lastChain: ChainTip | null;
  cognitiveRow: number;
  executionRow: number;
  edgeSeq: number;
  selectedEvent: TraceEvent | null;
  addEvent: (event: TraceEvent) => void;
  setSelectedFromNodeId: (nodeId: string | null) => void;
  reset: () => void;
};

const initialLayout = {
  cognitiveRow: 0,
  executionRow: 0,
  edgeSeq: 0,
  lastChain: null as ChainTip | null,
};

export const useTraceStore = create<TraceStore>((set, get) => ({
  nodes: [],
  edges: [],
  selectedEvent: null,
  ...initialLayout,

  reset: () =>
    set({
      nodes: [],
      edges: [],
      selectedEvent: null,
      ...initialLayout,
    }),

  setSelectedFromNodeId: (nodeId) => {
    if (!nodeId) {
      set({ selectedEvent: null });
      return;
    }
    const n = get().nodes.find((x) => x.id === nodeId);
    if (!n) {
      set({ selectedEvent: null });
      return;
    }
    const raw =
      n.type === "cognitive"
        ? (n.data as CognitiveNodeData).rawEvent
        : n.type === "output"
          ? (n.data as OutputNodeData).rawEvent
          : (n.data as ExecutionNodeData).rawEvent;
    set({ selectedEvent: raw });
  },

  addEvent: (event) => {
    const kind = event.kind;

    if (kind === "response") {
      set((s) => {
        const row = s.cognitiveRow;
        const id = `cog-${event.turn}-${Math.floor(event.ts * 1000) % 1_000_000}`;
        const thoughts = extractThoughts(event.payload);
        const usage = normalizeUsage(event.payload);
        const content =
          typeof event.payload.content === "string" ? event.payload.content : "";
        const node: Node<CognitiveNodeData> = {
          id,
          type: "cognitive",
          position: { x: COG_X, y: 32 + row * ROW_GAP },
          data: {
            thoughts,
            usage,
            contentFallback: content,
            rawEvent: event,
          },
        };
        const edges = [...s.edges];
        let edgeSeq = s.edgeSeq;
        if (s.lastChain) {
          edgeSeq += 1;
          const { sourceHandle, targetHandle } = edgeHandles(s.lastChain.variant, "cognitive");
          const paint = edgePaint(s.edges.length);
          edges.push({
            id: `e-${edgeSeq}`,
            source: s.lastChain.nodeId,
            target: id,
            type: "dataFlow",
            animated: false,
            sourceHandle,
            targetHandle,
            style: { stroke: paint.stroke },
            data: { curvature: paint.curvature },
          });
        }
        return {
          nodes: [...s.nodes, node],
          edges,
          edgeSeq,
          lastChain: { nodeId: id, variant: "cognitive" },
          cognitiveRow: row + 1,
        };
      });
      return;
    }

    if (kind === "tool_call") {
      set((s) => {
        const callId = String(event.payload.id ?? `anon-${s.edgeSeq + 1}`);
        const id = `exec-${callId}`;
        const row = s.executionRow;
        const toolName = String(event.payload.name ?? "unknown_tool");
        const args =
          event.payload.args && typeof event.payload.args === "object" && !Array.isArray(event.payload.args)
            ? (event.payload.args as Record<string, unknown>)
            : {};
        const node: Node<ExecutionNodeData> = {
          id,
          type: "execution",
          position: { x: EXEC_X, y: 32 + row * ROW_GAP },
          data: {
            toolName,
            args,
            callId,
            status: "PENDING",
            rawEvent: event,
          },
        };
        const edges = [...s.edges];
        let edgeSeq = s.edgeSeq;
        if (s.lastChain) {
          edgeSeq += 1;
          const { sourceHandle, targetHandle } = edgeHandles(s.lastChain.variant, "execution");
          const paint = edgePaint(s.edges.length);
          edges.push({
            id: `e-${edgeSeq}`,
            source: s.lastChain.nodeId,
            target: id,
            type: "dataFlow",
            animated: false,
            sourceHandle,
            targetHandle,
            style: { stroke: paint.stroke },
            data: { curvature: paint.curvature },
          });
        }
        return {
          nodes: [...s.nodes, node],
          edges,
          edgeSeq,
          lastChain: { nodeId: id, variant: "execution" },
          executionRow: row + 1,
        };
      });
      return;
    }

    if (kind === "tool_result") {
      set((s) => {
        const callId = String(event.payload.id ?? "");
        const id = `exec-${callId}`;
        const idx = s.nodes.findIndex((n) => n.id === id);
        if (idx === -1) {
          const row = s.executionRow;
          const toolName = String(event.payload.name ?? "unknown_tool");
          const node: Node<ExecutionNodeData> = {
            id,
            type: "execution",
            position: { x: EXEC_X, y: 32 + row * ROW_GAP },
            data: {
              toolName,
              args: {},
              callId,
              status: mapToolStatus(event.payload.status),
              output: event.payload.output,
              error: (event.payload.error as string | null | undefined) ?? null,
              rawEvent: event,
            },
          };
          const edges = [...s.edges];
          let edgeSeq = s.edgeSeq;
          if (s.lastChain) {
            edgeSeq += 1;
            const { sourceHandle, targetHandle } = edgeHandles(s.lastChain.variant, "execution");
            const paint = edgePaint(s.edges.length);
            edges.push({
              id: `e-${edgeSeq}`,
              source: s.lastChain.nodeId,
              target: id,
              type: "dataFlow",
              animated: false,
              sourceHandle,
              targetHandle,
              style: { stroke: paint.stroke },
              data: { curvature: paint.curvature },
            });
          }
          const baseNodes = [...s.nodes, node];
          const attached = attachOutputAfterExec(baseNodes, edges, edgeSeq, id, event);
          return {
            nodes: attached.nodes,
            edges: attached.edges,
            edgeSeq: attached.edgeSeq,
            lastChain: attached.lastChain,
            executionRow: row + 1,
          };
        }
        const nextNodes = s.nodes.map((n) => {
          if (n.id !== id || n.type !== "execution") return n;
          const prev = n.data as ExecutionNodeData;
          return {
            ...n,
            data: {
              ...prev,
              status: mapToolStatus(event.payload.status),
              output: event.payload.output,
              error: (event.payload.error as string | null | undefined) ?? null,
              rawEvent: event,
            } satisfies ExecutionNodeData,
          };
        });
        const attached = attachOutputAfterExec(nextNodes, s.edges, s.edgeSeq, id, event);
        return {
          nodes: attached.nodes,
          edges: attached.edges,
          edgeSeq: attached.edgeSeq,
          lastChain: attached.lastChain,
        };
      });
    }
  },
}));
