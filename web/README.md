# Agent 轨迹看板（`web/`）

Vite + React 18 + React Flow，消费与 Python `core.audit` / `core.schema.TraceEvent` 对齐的 JSONL 事件流，可视化「知 / 行」链路与侧栏载荷。

## 能否实时输出？

**可以。** 画布由 Zustand `addEvent` 增量驱动，任意传输层只要能把数据变成 `TraceEvent`（或一行 JSONL）并调用统一入口即可：

- WebSocket：`socket.onmessage` → `JSON.parse` → `ingestTraceEvent(...)`
- SSE / `fetch` 流：按行 `split("\n")` → `ingestTraceJsonlLine(line)`
- 轮询：拉取新片段后逐行调用同上

无需整页刷新；多条高频事件时注意在接收端做轻量节流（可选）。

## 统一输入 / 输出接口（给接手与对接方）

| 方向 | 模块路径 | 说明 |
|------|-----------|------|
| **输入** | `src/trace/index.ts` | 应用与测试应优先从这里 import，避免直接散落调用 store。 |
| `ingestTraceEvent(event)` | 单条 `TraceEvent` |
| `ingestTraceJsonlLine(line)` | 一行 JSON 字符串 |
| `loadTraceJsonlDocument(raw)` | 整块 JSONL，会先 `reset` |
| `resetTraceView()` | 清空画布 |
| `selectTraceNode(id)` | 侧栏选中，`null` 清除 |
| **输出 / 观测** | `subscribeTraceStore(listener)` | Zustand 订阅，参数为 `(state, prev)`，可读取 `state.nodes` / `state.edges` / `state.selectedEvent` 等做埋点、导出、联动其它面板。 |
| **React 内** | `useTraceStore`（`src/store/useTraceStore.ts`） | 组件内 `useTraceStore(selector)` 与常规 Zustand 用法一致。 |

### `TraceEvent` 形状（与后端对齐）

与仓库内 Python `TraceEvent` 一致：

| 字段 | 类型 | 说明 |
|------|------|------|
| `ts` | `number` | Unix 时间戳（秒，可小数） |
| `session_id` | `string` | 会话 id |
| `turn` | `number` | 轮次 |
| `state` | `AgentState` | 状态机枚举字符串 |
| `kind` | `string` | 事件类型，见下表 |
| `payload` | `Record<string, unknown>` | 载荷 |

当前画布**会建节点/边**的 `kind`：

- `response`：左侧认知节点（`payload.thoughts` 优先，缺省时见 `utils/thoughtAdapter` 回退）
- `tool_call` / `tool_result`：右侧执行节点（以 `payload.id` 关联）

其它 `kind`（如 `prompt`、`state_change`）会被忽略，不改变图结构。

Python 侧常量名见项目根目录 `core/audit.py`（`KIND_*`）。

### 实时接入示例（伪代码）

```ts
import { ingestTraceEvent, ingestTraceJsonlLine, resetTraceView } from "./trace";

resetTraceView();
const ws = new WebSocket(import.meta.env.VITE_TRACE_WS_URL);
ws.onmessage = (ev) => {
  ingestTraceJsonlLine(ev.data as string);
};
```

或对象帧：

```ts
ws.onmessage = (ev) => {
  ingestTraceEvent(JSON.parse(ev.data as string));
};
```

## 互动与「修改」能力

| 能力 | 现状 | 扩展方向 |
|------|------|----------|
| 平移 / 缩放 / MiniMap | 已支持（React Flow） | — |
| 拖拽节点 | 已支持（`onNodesChange` 写回 store） | 若需「布局锁定」可关 `nodesDraggable` |
| 点击节点看 JSON | 已支持 | 侧栏可改为可编辑 textarea +「应用」回写自定义 store（需新 action，当前**不**写回审计文件） |
| 暂停 / 单步回放 | 未做 | 用定时器 + 队列包一层即可 |
| 删除边 / 节点 | 未做 | `useTraceStore` 增加 `removeNode` 或在 `onEdgesChange` 里处理 `remove` |

结论：**实时与互动在架构上都可行**；「修改」若指改画布布局已有，若指改业务数据需在后端或新 API 层定义写回语义。

## 脚本

```bash
npm install
npm run dev      # 开发
npm run build    # 构建
npm run test     # Vitest（含 sample_trace 剧本断言）
```

## 与 Python 仓库的关系

- 真源 schema：`core/schema.py` 中 `TraceEvent`
- 日志格式：`core/audit.py` JSONL 一行一条
- 本前端类型：`web/src/types/trace.ts`（需与后端保持字段一致，变更时请双端同步）

更细的字段约定（如 `response` 的 `thoughts` / `usage`）以当前 `workflow` 写入轨迹为准。
