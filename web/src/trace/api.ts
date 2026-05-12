import { TraceParser } from "../parser/TraceParser";
import type { TraceEvent } from "../types/trace";
import { useTraceStore } from "../store/useTraceStore";

/**
 * 统一「输入」：单条结构化审计事件（与 Python `core.schema.TraceEvent` 对齐）。
 * 实时场景：WebSocket / SSE / Worker 每收到一条 JSON 反序列化后调用即可增量渲染。
 */
export function ingestTraceEvent(event: TraceEvent): void {
  useTraceStore.getState().addEvent(event);
}

/**
 * 统一「输入」：一行 JSONL（整行即一个 JSON 对象，不含未转义换行）。
 * 与 `runtime/traces/*.jsonl` 文件格式一致。
 */
export function ingestTraceJsonlLine(line: string): void {
  const trimmed = line.trim();
  if (!trimmed) return;
  ingestTraceEvent(JSON.parse(trimmed) as TraceEvent);
}

/** 清空节点、边与链指针；不卸载 React。 */
export function resetTraceView(): void {
  useTraceStore.getState().reset();
}

/** 高亮侧栏载荷；传 `null` 清除。 */
export function selectTraceNode(nodeId: string | null): void {
  useTraceStore.getState().setSelectedFromNodeId(nodeId);
}

/**
 * 批量加载完整 JSONL 文本（会先 `resetTraceView`）。
 * 适合文件上传、一次性拉取历史会话。
 */
export function loadTraceJsonlDocument(raw: string): void {
  resetTraceView();
  new TraceParser(ingestTraceEvent).parseJsonlString(raw);
}

export type TraceStoreSnapshot = ReturnType<typeof useTraceStore.getState>;

/**
 * 订阅画布 store 全量变化（Zustand 原生语义）。
 * @returns 取消订阅函数。
 */
export function subscribeTraceStore(
  listener: (state: TraceStoreSnapshot, prev: TraceStoreSnapshot) => void,
): () => void {
  return useTraceStore.subscribe(listener);
}
