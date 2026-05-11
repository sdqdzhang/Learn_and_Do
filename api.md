# Tiny-Devin 基础层 API 手册 (V1.3)

本手册面向写应用代码（接入新工具、新角色、新流程）的开发者，覆盖 V1.3 已经稳定的四层基础层：

- 认知层（`utils.llm_client`、`utils.parser`、`prompt_templates`）
- 执行层（`runtime.executor`、`tools.*`）
- 记忆层（`memory.session_context`、`memory.vector_store`）
- 编排层（`core.workflow`、`core.session_manager`、`core.audit`）

横切的协议层与异常层（`core.schema` / `core.exceptions`）贯穿所有层。

---

## 目录

- [0. 60 秒上手](#0-60-秒上手)
- [1. 配置约定](#1-配置约定)
- [2. 协议层 `core.schema`](#2-协议层-coreschema)
- [3. 异常层 `core.exceptions`](#3-异常层-coreexceptions)
- [4. 认知层](#4-认知层)
  - [4.1 `utils.llm_client`](#41-utilsllm_client)
  - [4.2 `utils.parser`](#42-utilsparser)
  - [4.3 `prompt_templates`](#43-prompt_templates)
- [5. 执行层](#5-执行层)
  - [5.1 `runtime.executor`](#51-runtimeexecutor)
  - [5.2 `tools.*` 与 `ToolRegistry`](#52-tools-与-toolregistry)
  - [5.3 写一个新工具](#53-写一个新工具)
- [6. 记忆层](#6-记忆层)
  - [6.1 `memory.session_context`](#61-memorysession_context)
  - [6.2 `memory.vector_store`](#62-memoryvector_store)
- [7. 编排层](#7-编排层)
  - [7.1 `core.session_manager`](#71-coresession_manager)
  - [7.2 `core.audit`](#72-coreaudit)
  - [7.3 `core.workflow`](#73-coreworkflow)
- [8. 端到端组装范例](#8-端到端组装范例)
- [9. 测试基建](#9-测试基建)

---

## 0. 60 秒上手

```bash
pip install -r requirements.txt
cp .env.example .env             # 编辑 OLLAMA_MODEL
docker build -t tiny-devin-base:latest .

# 代码模式
python main.py --mode development --role coder \
  --prompt "Write a Python function that reverses a string."

# 哲学模式
python main.py --mode philosophy --role philosopher \
  --prompt "Does technology increase human happiness?"
```

`main.py` 干的事就是把本手册讲到的所有层装到一起。

---

## 1. 配置约定

- 所有配置通过环境变量读取。每个组件自带一个 `XxxConfig.from_env()` 工厂方法；不要在业务代码里散落 `os.getenv()`。
- `.env` 由 `LLMConfig.from_env()` 在第一次实例化时自动加载（idempotent，不覆盖已有 env）。
- 全量变量见 `.env.example`；新模块若引入新变量，**先加 `.env.example` 再写代码**。

---

## 2. 协议层 `core.schema`

所有跨模块传递的数据都在这里。Pydantic v2 模型。

| 类别 | 类型 | 说明 |
|---|---|---|
| 枚举 | `MessageRole`, `FileAction`, `TaskMode`, `EvidenceType`, `ToolStatus`, `AgentState`, `AgentRole` | — |
| 对话 | `ChatMessage` | `role`/`content`/可选 `thought`/`tool_call_id`/`metadata` |
| 文件 | `FileOperation` | 内置防越权校验（拒绝绝对路径与 `..`） |
| 证据 | `Evidence` | 三种 `EvidenceType`：`CODE_RESULT` / `WEB_SOURCE` / `DATA_FILE` |
| 执行 | `ExecutionResult` | 含 `artifacts` 字段（本轮新生成的文件） |
| 工具 | `ToolCall`、`ToolResult`、`ToolSpec` | `ToolCall.id` 自动生成 |
| 推理 | `Plan`、`Reflection` | `Reflection.next_action: "retry"/"revise"/"done"` |
| 审计 | `TraceEvent` | JSON Lines 写入 `runtime/traces/{session_id}.jsonl` |
| 解析 | `ParsedOutput` | `parse_response` 的返回结构 |
| 结果 | `WorkflowResult` | `Workflow.run()` 的返回 |

**关键约定**：
- 业务层失败信号（测试不通过 / 假设被证伪）**不走异常**，靠 `ExecutionResult` 与 `Evidence` 承载，再由 workflow 决定是否升格成 `EvidenceConflict`。
- `ChatMessage.thought` 在 PHILOSOPHY 模式由 `utils.parser` 强校验，schema 本身不强制。

---

## 3. 异常层 `core.exceptions`

```
TinyDevinError
├── RetryableError                ← workflow 同 session 内重试
│   ├── CodeFormatError           解析失败（含缺 <thought>）
│   ├── MissingPathError          <file> 缺路径
│   ├── LLMTimeoutError           LLM 重试预算耗尽
│   ├── EvidenceConflict          数据 / 单测与论点冲突
│   ├── ToolError                 工具调用软失败
│   └── MemoryError               记忆层瞬态故障
└── FatalError                    ← workflow 直接 FAILED
    ├── SandboxViolation          越权指令
    ├── ContainerImageError       基础镜像缺失 / docker 不可用
    ├── ResourceExhausted         超时 / OOM
    └── ConfigurationError        必需配置缺失
```

上层统一两个 except 分支即可（参见 §8）。

---

## 4. 认知层

### 4.1 `utils.llm_client`

| 接口 | 用途 |
|---|---|
| `LLMConfig.from_env()` | 从 `.env` / 环境变量读配置 |
| `LLMClient(config=None)` | 客户端实例（默认走 Ollama OpenAI 兼容接口） |
| `client.chat(messages, *, model, temperature, max_tokens, extra)` | 同步对话，自带重试，返回 `str` |
| `client.stream_chat(...)` | 流式对话，返回 `Iterator[str]` |

错误模型：
- 超时 / 连接 / 限流 → 指数退避后重试，预算耗尽抛 `LLMTimeoutError`。
- 其他 `APIError`（4xx 等）→ 立即抛，不重试。

### 4.2 `utils.parser`

```python
from utils.parser import parse_response
from core.schema import TaskMode

parsed = parse_response(text, mode=TaskMode.PHILOSOPHY, require_block=True)

parsed.files       # List[FileOperation]
parsed.thoughts    # List[str]   PHILOSOPHY 必须 ≥ 1，否则抛 CodeFormatError
parsed.tool_calls  # List[ToolCall]，args 为 JSON dict
parsed.json_blocks # List[dict]  形如 Plan/Reflection 的 ```json``` 块
```

支持的块：

| 块 | 语法 | 落点 |
|---|---|---|
| 文件 | `<file path="a.py" action="write">...</file>` | `files` |
| 文件兼容形式 | ```` ```python\n# file: a.py\n...\n``` ```` | `files` |
| 思辨 | `<thought>...</thought>` | `thoughts` |
| 工具 | `<tool name="x">{json args}</tool>`（或 `<![CDATA[...]]>` 包裹） | `tool_calls` |
| JSON 结构 | ```` ```json\n{...}\n``` ```` | `json_blocks` |

### 4.3 `prompt_templates`

Jinja2 驱动。

```python
from prompt_templates import SystemPromptBuilder
from core.schema import AgentRole, TaskMode

system_prompt = SystemPromptBuilder(
    role=AgentRole.PHILOSOPHER,
    mode=TaskMode.PHILOSOPHY,
    tools=tool_registry.list_specs(),
    extra_ctx={"topic": "epistemology"},
).render()
```

支持的角色矩阵：

| Role / Mode | DEVELOPMENT | PHILOSOPHY |
|---|---|---|
| `CODER` | 写 / 修代码 | （可用，但语气会偏代码） |
| `REVIEWER` | 查语法、单测 | 查逻辑谬误、数据证伪 |
| `PHILOSOPHER` | （可用） | 构建论证 |
| `INVESTIGATOR` | （可用） | 把假设翻成实证脚本 |
| `PLANNER` | 输出 `Plan` JSON | 同左 |
| `REFLECTOR` | 输出 `Reflection` JSON | 同左 |

`.with_tools(tools)` / `.with_context(**kv)` 返回新实例（不可变）。

---

## 5. 执行层

### 5.1 `runtime.executor`

```python
from runtime.executor import Executor
from core.schema import FileOperation

executor = Executor()  # 一次性容器模式
result = executor.run(
    files=[FileOperation(file_path="main.py", content="print('hi')")],
    command=["python", "main.py"],
    extra_pip=["loguru"],
    timeout=30,
)
result.is_success      # bool
result.stdout          # str
result.artifacts       # List[str]  本轮新生成文件
result.metrics         # {"elapsed_ms": ..., "exit_code": ...}
```

- 单次模式：每次 `run()` 起一个新容器并自动删除。
- 长会话模式：被 `SessionManager` 持有一个 `container_name` 时，复用容器。
- `_apply_files` 写到 `WORKSPACE_DIR`，与容器 `/workspace` volume 双向同步。
- 命令安全：硬编码黑名单（`rm -rf /` / fork bomb / `mkfs` 等）→ 抛 `SandboxViolation`。

### 5.2 `tools.*` 与 `ToolRegistry`

内置工具：

| 名称 | 类 | 必需参数 | 说明 |
|---|---|---|---|
| `file_read` | `FileReadTool` | `path` | 沙箱内读文件，UTF-8 |
| `file_write` | `FileWriteTool` | `path`, `content` | 沙箱内写文件 |
| `file_list` | `FileListTool` | — | 列出 workspace 一级目录 |
| `python_repl` | `PythonReplTool` | `code` | 通过 Executor 运行 Python（需 `.bind_executor()`） |
| `web_search` | `WebSearchTool` | `query` | DuckDuckGo HTML，无 API key |
| `rag_query` | `RagQueryTool` | `query` | 长期记忆检索 |
| `rag_upsert` | `RagUpsertTool` | `texts` | 长期记忆写入 |

注册与调用：

```python
from tools.registry import ToolRegistry
from tools.file_io import FileReadTool, FileWriteTool, FileListTool
from tools.repl import PythonReplTool
from core.schema import ToolCall

registry = ToolRegistry()
registry.register(FileReadTool())
registry.register(FileWriteTool())
repl = PythonReplTool()
repl.bind_executor(executor)
registry.register(repl)

# 一次调用
result = registry.invoke(ToolCall(name="file_read", args={"path": "main.py"}))
result.status   # ToolStatus.SUCCESS / FAILED
result.output   # 工具自定义结构
```

### 5.3 写一个新工具

```python
from typing import Any, Dict
from core.exceptions import ToolError
from core.schema import ToolSpec
from tools.base import Tool

class WordCountTool(Tool):
    spec = ToolSpec(
        name="word_count",
        description="Count words in a string.",
        args_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    )

    def call(self, args: Dict[str, Any]) -> Dict[str, Any]:
        text = args["text"]
        if not isinstance(text, str):
            raise ToolError("text must be a string")
        return {"count": len(text.split())}

registry.register(WordCountTool())
```

要点：
- 必须类级别声明 `spec: ToolSpec`（构造函数声明 `__init_subclass__` 会校验）。
- 业务失败抛 `ToolError`（→ Retryable），越权抛 `SandboxViolation`（→ Fatal）。
- 框架自动量耗时、捕获异常、装进 `ToolResult`。

---

## 6. 记忆层

### 6.1 `memory.session_context`

短期工作记忆。

```python
from memory.session_context import SessionContext, ContextConfig
from core.schema import ChatMessage, MessageRole

ctx = SessionContext(ContextConfig.from_env())
ctx.set_summarizer(lambda msgs: client.chat([...]))  # 由 workflow 注入
ctx.add(ChatMessage(role=MessageRole.USER, content="..."))
ctx.compress_if_needed()   # 自动调用，超 budget 时压缩中段
openai_messages = ctx.to_openai()
```

- Token 计数优先 `tiktoken`，未安装时降级 `len/4`。
- 压缩策略：保留首条 SYSTEM + 最末 `preserve_recent` 条；中段合并为一条 SYSTEM 摘要。
- `summarizer` 是 `Callable[[List[ChatMessage]], str]`，由 `main.py` 注入。

### 6.2 `memory.vector_store`

长期经验记忆，ChromaDB 懒加载。

```python
from memory.vector_store import VectorStore

store = VectorStore()
ids = store.upsert(
    texts=["nietzsche on technology", "MVC pattern in Django"],
    metadatas=[{"topic": "philosophy"}, {"topic": "code"}],
)
hits = store.query("post-structuralist views on AI", k=3)
for h in hits:
    print(h.id, h.distance, h.text[:80])
```

- 首次 `upsert` / `query` 才真正 `import chromadb`，导入 `memory.vector_store` 本身零代价。
- 持久化目录由 `VECTOR_PERSIST_DIR` 控制，默认 `./runtime/vector_store/`（已加入 `.gitignore`）。
- 任何 chromadb 异常会被规范为 `MemoryError`（Retryable）。

---

## 7. 编排层

### 7.1 `core.session_manager`

```python
from core.session_manager import SessionManager

with SessionManager() as session:
    session_id = session.session_id
    executor = session.executor
    # ... use executor ...
    session.heartbeat()       # 每个 turn 调一次，阻止空闲熔断
# 退出 with 自动 stop（关容器 / 终止心跳线程）
```

- 单容器单会话（MVP），`session_id = "{prefix}-{uuid8}"`。
- 后台心跳线程，超 `CONTAINER_IDLE_TIMEOUT_SECONDS` 自动强停。

### 7.2 `core.audit`

JSON Lines 轨迹日志。

```python
from core.audit import TraceLogger, KIND_PROMPT
from core.schema import AgentState

trace = TraceLogger(session_id="demo-001")
trace.log(KIND_PROMPT, {"user": "say hi"}, state=AgentState.IDLE, turn=0)
trace.close()

# 离线读取
from core.audit import read_trace
events = read_trace("runtime/traces/demo-001.jsonl")
```

字段：`ts / session_id / turn / state / kind / payload`。

`kind` 常量：`KIND_PROMPT`, `KIND_RESPONSE`, `KIND_TOOL_CALL`, `KIND_TOOL_RESULT`, `KIND_EXEC_RESULT`, `KIND_STATE_CHANGE`, `KIND_ERROR`, `KIND_REFLECTION`, `KIND_PLAN`。

### 7.3 `core.workflow`

状态机驱动整个 session。

```python
from core.workflow import Workflow, WorkflowConfig
from core.schema import AgentRole, TaskMode

workflow = Workflow(
    llm=llm_client,
    tools=tool_registry,
    context=session_context,
    trace=trace_logger,
    mode=TaskMode.DEVELOPMENT,
    config=WorkflowConfig(max_turns=10, role=AgentRole.CODER),
)
result = workflow.run("Implement quick sort and add a unit test.")
```

状态流：

```
IDLE → THINKING → ACTING → OBSERVING → REFLECTING → ...
                                                ↘ DONE / FAILED
```

行为：
- 每次状态切换写一条 `KIND_STATE_CHANGE` trace。
- `THINKING` 调 LLM；`ACTING` 派发工具；`OBSERVING` 把 `ToolResult` 注入上下文；`REFLECTING` 看 LLM 是否给了 `Reflection` JSON（含 `next_action`）。
- 遇 `RetryableError`：写 trace、构造反馈消息（`[feedback] ...`）、回到 `THINKING`。
- 遇 `FatalError`：状态 → `FAILED`，trace 关闭，返回 `WorkflowResult(error=...)`。
- 终止条件三选一：assistant 显式 `[done]` / max_turns / FAILED。

---

## 8. 端到端组装范例

下面是 `main.py` 的内核精简版，可作为自定义 entrypoint 的起点：

```python
from core.audit import TraceLogger
from core.schema import AgentRole, TaskMode
from core.session_manager import SessionManager
from core.workflow import Workflow, WorkflowConfig
from memory.session_context import SessionContext
from tools.file_io import FileReadTool, FileWriteTool, FileListTool
from tools.repl import PythonReplTool
from tools.registry import ToolRegistry
from utils.llm_client import LLMClient

llm = LLMClient()

with SessionManager() as session:
    trace = TraceLogger(session_id=session.session_id)

    tools = ToolRegistry()
    tools.register(FileReadTool())
    tools.register(FileWriteTool())
    tools.register(FileListTool())
    repl = PythonReplTool()
    repl.bind_executor(session.executor)
    tools.register(repl)

    context = SessionContext()
    context.set_summarizer(
        lambda msgs: llm.chat(
            [
                {"role": "system", "content": "Condense the conversation."},
                {"role": "user", "content": "\n\n".join(m.content for m in msgs)},
            ]
        )
    )

    workflow = Workflow(
        llm=llm, tools=tools, context=context, trace=trace,
        mode=TaskMode.DEVELOPMENT,
        config=WorkflowConfig(max_turns=8, role=AgentRole.CODER),
    )
    result = workflow.run("Write quick_sort.py and unit-test it with pytest.")
    print(result.model_dump_json(indent=2))
    trace.close()
```

切换模式：把 `mode=TaskMode.PHILOSOPHY`、`role=AgentRole.PHILOSOPHER`，工具集换上 `WebSearchTool` 与 `RagQueryTool` 即可，**workflow 本身不动**。

---

## 9. 测试基建

- `pytest.ini`：`pythonpath = .`、`testpaths = tests`、`--strict-markers`。
- 跑 `pytest`。
- 写 `LLMClient` 测试时 mock `openai.OpenAI`；写 `Executor` 测试时 mock `docker.from_env`。
- `core.audit.read_trace(path)` 帮你在测试里回读 JSONL 断言行为。

---

## 附 A：模块依赖图（人脑友好版）

```
main.py
  ├── core.session_manager  ── runtime.executor
  ├── core.audit            ── core.schema
  ├── core.workflow         ── utils.llm_client
  │                         ── utils.parser
  │                         ── prompt_templates ── core.schema
  │                         ── memory.session_context
  │                         ── tools.registry   ── tools.base
  │                                              ├── tools.file_io
  │                                              ├── tools.search
  │                                              ├── tools.repl    ── runtime.executor
  │                                              └── tools.rag     ── memory.vector_store
  └── tools.* / memory.*

core.exceptions  ← 被所有层 import
core.schema      ← 被所有层 import
```
