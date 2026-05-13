export type AgentState =
  | "idle"
  | "thinking"
  | "acting"
  | "observing"
  | "reflecting"
  | "intervention"
  | "done"
  | "failed"
  | "cancelled";

export type TraceEvent = {
  ts: number;
  event_id?: string;
  runtime_state_id?: string;
  session_id: string;
  turn: number;
  state: AgentState;
  kind: string;
  payload: Record<string, unknown>;
  context_snapshot?: Record<string, unknown> | null;
};

export type CognitiveNodeData = {
  thoughts: string[];
  usage: Record<string, unknown> | null;
  contentFallback: string;
  /** 后端在 response payload 中写入的状态说明（出站校验、空 completion 等），画布在正文为空时优先展示。 */
  traceHints: string[];
  rawEvent: TraceEvent;
};

export type ExecutionNodeData = {
  toolName: string;
  args: Record<string, unknown>;
  callId: string;
  status: "PENDING" | "SUCCESS" | "FAILED";
  output?: unknown;
  error?: string | null;
  rawEvent: TraceEvent;
};

/** 工具 stdout / stderr 或与后端对齐的结构化 output 摘要（画布专用）。 */
export type OutputNodeData = {
  callId: string;
  toolName: string;
  stdout: string;
  stderr: string;
  summary: string;
  rawEvent: TraceEvent;
};

export type ChainTip = {
  nodeId: string;
  variant: "cognitive" | "execution" | "output";
};
