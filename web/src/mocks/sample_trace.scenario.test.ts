import { beforeEach, describe, expect, it } from "vitest";
import { useTraceStore } from "../store/useTraceStore";
import type { ExecutionNodeData } from "../types/trace";
import { loadTraceJsonlDocument, resetTraceView } from "../trace";
import scenario from "./sample_trace.jsonl?raw";

/**
 * 剧本：异步爬虫 OOM + 状态机死锁 —— 见 sample_trace.jsonl 内注释性 phase 标记。
 * 回放后画布应为链式 知→行→输出（4 认知 + 5 执行 + 5 输出 + 13 条边）。
 */
describe("sample_trace 爬虫修复剧本", () => {
  beforeEach(() => {
    resetTraceView();
  });

  it("JSONL 全量解析后节点与边数量符合链式布局", () => {
    loadTraceJsonlDocument(scenario);
    const { nodes, edges } = useTraceStore.getState();

    const cognitive = nodes.filter((n) => n.type === "cognitive");
    const execution = nodes.filter((n) => n.type === "execution");
    const outputs = nodes.filter((n) => n.type === "output");

    expect(cognitive).toHaveLength(4);
    expect(execution).toHaveLength(5);
    expect(outputs).toHaveLength(5);
    expect(nodes).toHaveLength(14);
    expect(edges).toHaveLength(13);
  });

  it("含 OOM 失败与 pytest 全绿", () => {
    loadTraceJsonlDocument(scenario);
    const execution = useTraceStore
      .getState()
      .nodes.filter((n) => n.type === "execution") as Array<{
      id: string;
      data: ExecutionNodeData;
    }>;

    const oom = execution.find((n) => n.data.callId === "tc-repl-stress");
    expect(oom?.data.status).toBe("FAILED");
    expect(String(oom?.data.error ?? "")).toContain("OOM");

    const partial = execution.find((n) => n.data.callId === "tc-repl-retry");
    expect(partial?.data.status).toBe("SUCCESS");
    expect(String(partial?.data.output ?? "")).toContain("死锁");

    const pytest = execution.find((n) => n.data.toolName === "pytest");
    expect(pytest?.data.status).toBe("SUCCESS");
    expect(String(pytest?.data.output ?? "")).toContain("12 tests passed");
  });
});
