export type AgentState =
  | "idle"
  | "thinking"
  | "acting"
  | "observing"
  | "reflecting"
  | "intervention"
  | "done"
  | "failed";

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

export type ChainTip = {
  nodeId: string;
  variant: "cognitive" | "execution";
};
