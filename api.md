# Tiny-Devin 基础层 API 手册

本文档面向**基础层之上的开发者**（写 `parser.py` / `executor.py` / `workflow.py` / `prompt_templates.py` / `main.py` 的人），总结当前已稳定的"基础设施"如何被调用。

> 范围：仅覆盖与业务无关、可复用的库层 —— 异常体系、LLM 客户端、配置约定、测试基建。
> 业务模块（`schema` / `parser` / `executor` / `workflow` / `session_manager` / `prompt_templates`）尚未实现，本文档不涉及。

---

## 目录

- [1. 配置约定](#1-配置约定)
- [2. `utils.llm_client`](#2-utilsllm_client)
  - [2.1 `LLMConfig`](#21-llmconfig)
  - [2.2 `LLMClient`](#22-llmclient)
  - [2.3 调用示例](#23-调用示例)
- [3. `core.exceptions`](#3-coreexceptions)
  - [3.1 异常树](#31-异常树)
  - [3.2 上层处理范式](#32-上层处理范式)
- [4. 跨模块组合范式](#4-跨模块组合范式)
- [5. 测试基建](#5-测试基建)
- [6. 沙箱基础镜像](#6-沙箱基础镜像)
- [7. 待业务层补全的接口位](#7-待业务层补全的接口位)

---

## 1. 配置约定

所有配置统一通过 **环境变量** 读取，模板见 `.env.example`。开发时复制一份：

```bash
cp .env.example .env
```

然后改 `.env` 里的 `OLLAMA_MODEL` 等字段。

### 关键约定

- 任何模块需要配置时，**自定义 `XxxConfig` 数据类 + `from_env()` 工厂方法**，而不是直接 `os.getenv()` 散落各处。`LLMConfig` 是范本。
- `from_env()` 默认会尝试 `load_dotenv()`，因此**模块独立可用**，不依赖 `main.py` 必须先调一次 dotenv。
- 已存在的环境变量**不会被 `.env` 覆盖**（python-dotenv 默认行为），允许 CI / 临时 export 优先生效。

### 现有环境变量速查

| 类别 | 变量 | 默认值 | 说明 |
|---|---|---|---|
| LLM | `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | OpenAI 兼容端点 |
| LLM | `OLLAMA_API_KEY` | `ollama` | Ollama 忽略，但 SDK 必填 |
| LLM | `OLLAMA_MODEL` | `your-model-name` | 必须改成本地实际拉取的模型 |
| LLM | `LLM_TIMEOUT_SECONDS` | `120` | 单次请求超时 |
| LLM | `LLM_MAX_RETRIES` | `2` | 瞬态失败后**额外**尝试次数（不含首次请求）；总次数 = `1 + LLM_MAX_RETRIES`（默认 `2` → 最多 3 次） |
| LLM | `LLM_TEMPERATURE` | `0.2` | 默认采样温度 |
| Docker | `DOCKER_BASE_IMAGE` | `tiny-devin-base:latest` | 沙箱镜像 |
| Docker | `CONTAINER_NAME_PREFIX` | `tiny-devin` | 容器名前缀 |
| Docker | `CONTAINER_MEMORY_LIMIT` | `2g` | 容器内存配额 |
| Docker | `CONTAINER_CPU_LIMIT` | `2.0` | 容器 CPU 配额 |
| Docker | `CONTAINER_IDLE_TIMEOUT_SECONDS` | `3600` | 空闲强停时间 |
| Docker | `CONTAINER_HEARTBEAT_INTERVAL_SECONDS` | `600` | session_manager 心跳 |
| Workspace | `WORKSPACE_DIR` | `./runtime/workspace` | 双向同步目录 |
| Logging | `LOG_LEVEL` | `INFO` | stdlib logging 级别 |

---

## 2. `utils.llm_client`

任何 Agent 角色（Coder / Reviewer / Philosopher / Investigator）都通过这一层调模型。它**不感知 TaskMode**，只负责"发消息、收文本、出错时按策略重试"。

### 2.1 `LLMConfig`

不可变数据类，集中所有 LLM 相关参数。

```python
from utils.llm_client import LLMConfig

# 从环境变量构造（默认行为）
cfg = LLMConfig.from_env()

# 也可手动指定（测试 / 多模型路由场景常用）
cfg = LLMConfig(
    base_url="http://localhost:11434/v1",
    api_key="ollama",
    model="qwen2.5-coder:7b",
    timeout_seconds=60.0,
    max_retries=3,
    temperature=0.0,
)
```

### 2.2 `LLMClient`

| 方法 | 签名 | 用途 |
|---|---|---|
| `__init__(config=None)` | 不传则 `LLMConfig.from_env()` | 创建客户端 |
| `chat(messages, **kw)` | 返回 `str` | 同步对话，自带重试 |
| `stream_chat(messages, **kw)` | 返回 `Iterator[str]` | 流式对话，逐 chunk yield |
| `model` (property) | `str` | 当前默认模型名 |
| `config` (property) | `LLMConfig` | 当前配置 |

**`messages` 格式**：标准 OpenAI 格式 —— `List[{"role": "system"|"user"|"assistant", "content": "..."}]`。
为减少耦合，参数类型只声明为 `List[Mapping[str, Any]]`；模块内另有类型别名 **`ChatMessage`**（等价于 `Mapping[str, Any]`），可按需 `from utils.llm_client import ChatMessage`。调用方既可传裸 `dict`，也可传未来 `core.schema` 里 Pydantic 模型 `model_dump()` 出来的字典。

**关键字参数（`chat` / `stream_chat` 共用）**：

| 参数 | 类型 | 说明 |
|---|---|---|
| `model` | `Optional[str]` | 临时覆盖模型名（多模型路由用） |
| `temperature` | `Optional[float]` | 不传则用 `LLMConfig.temperature` |
| `max_tokens` | `Optional[int]` | 直传给底层 SDK |
| `extra` | `Optional[dict]` | 透传给 `chat.completions.create`（如 `top_p`、`stop`） |

### 2.3 调用示例

#### 最简同步调用

```python
from utils.llm_client import LLMClient

client = LLMClient()                    # 从 .env 读配置
reply = client.chat(
    [
        {"role": "system", "content": "You are a careful Python coder."},
        {"role": "user", "content": "Write a function that reverses a string."},
    ]
)
print(reply)
```

#### 流式输出（未来给 UI 做打字机效果）

```python
client = LLMClient()
for chunk in client.stream_chat(messages):
    print(chunk, end="", flush=True)
```

#### 单轮覆盖参数

```python
reply = client.chat(
    messages,
    model="qwen2.5-coder:14b",   # 这一轮临时换模型
    temperature=0.0,             # 这一轮要确定性
    max_tokens=1024,
    extra={"top_p": 0.9, "stop": ["</file>"]},
)
```

#### 自定义配置（测试场景）

```python
from utils.llm_client import LLMClient, LLMConfig

cfg = LLMConfig(
    base_url="http://test-host:11434/v1",
    api_key="ollama",
    model="tinyllama",
    timeout_seconds=5.0,
    max_retries=0,
    temperature=0.0,
)
client = LLMClient(cfg)
```

### 2.4 重试与错误约定

| 场景 | 行为 |
|---|---|
| `APITimeoutError` | 重试，指数退避（1s, 2s, 4s ... 上限 10s） |
| `APIConnectionError` | 同上 |
| `RateLimitError` | 同上 |
| 其他 `APIError`（4xx 等） | **立即抛出，不重试**（无意义） |
| 超出重试预算 | 抛 `core.exceptions.LLMTimeoutError`，原始异常挂在 `__cause__` 上 |

> 调用方只需 `try / except LLMTimeoutError`，不必关心底层 `openai.*Error`。

---

## 3. `core.exceptions`

整个项目所有自定义异常的根。**业务侧的失败信号（如测试不通过、假设被证伪）不走异常**，而是数据回流（`ExecutionResult` / `Evidence`）。异常只表达两类信号：**可重试** 和 **必须熔断**。

### 3.1 异常树

```
TinyDevinError
├── RetryableError              ← workflow 可在同一 session 内重试
│   ├── CodeFormatError         ← 解析不到 <file> / <thought> 块
│   ├── MissingPathError        ← 代码块缺 path 属性
│   ├── LLMTimeoutError         ← LLM 重试预算耗尽
│   └── EvidenceConflict        ← Executor 数据与当前论点冲突
└── FatalError                  ← workflow 必须立即终止 session
    ├── SandboxViolation        ← Agent 试图越权（rm -rf / 等）
    ├── ContainerImageError     ← 基础镜像缺失或构建失败
    └── ResourceExhausted       ← 容器内存 / GPU / 磁盘超限
```

### 3.2 上层处理范式

`workflow.py` / `main.py` 推荐统一两个 except 分支：

```python
from core.exceptions import RetryableError, FatalError

try:
    do_one_turn()
except RetryableError as exc:
    # 把 exc 反馈给下一轮 prompt（"上次解析失败，请按 <file> 标签输出"）
    feedback_for_next_turn = build_feedback(exc)
except FatalError as exc:
    # 关容器、写日志、告知用户
    session_manager.shutdown()
    raise
```

### 3.3 抛异常时的写法约定

所有自定义异常都接受 `details` 参数承载结构化上下文：

```python
from core.exceptions import CodeFormatError

raise CodeFormatError(
    "no <file> block found",
    details={"raw_output": llm_output[:200], "turn": current_turn},
)
```

`workflow` 把 `details` 序列化到反馈 prompt 里，比纯字符串消息有用得多。

**`LLMTimeoutError`（由 `LLMClient` 抛出）**：`details` 在实现里通常是**最后一次失败的简短字符串**，原始 `openai` 异常放在 **`__cause__`** 上（见 §2.4）。需要结构化上下文时仍以业务侧自定义异常 + dict `details` 为主。

---

## 4. 跨模块组合范式

下面是**业务层**未来一定会重复出现的最小调用骨架，可作为 `executor.py` / `workflow.py` 实现时的参考。

```python
import logging
from utils.llm_client import LLMClient
from core.exceptions import (
    CodeFormatError,
    EvidenceConflict,
    FatalError,
    LLMTimeoutError,
    RetryableError,
)

logger = logging.getLogger(__name__)


def run_one_turn(client: LLMClient, messages: list) -> str:
    """单轮：发 prompt -> 拿文本。失败语义已被 LLMClient 收敛。"""
    try:
        return client.chat(messages)
    except LLMTimeoutError:
        # 已经是 RetryableError 子类，由 workflow 决定是否再来一轮
        raise


def run_loop(client: LLMClient, initial_messages: list, max_turns: int = 10):
    messages = list(initial_messages)
    for turn in range(max_turns):
        try:
            reply = run_one_turn(client, messages)
            # parsed = parser.parse(reply)         # 业务层
            # result = executor.run(parsed.files)  # 业务层
            # workflow.detect_conflict(result)     # 业务层
            return reply
        except RetryableError as exc:
            logger.warning("turn %d retryable: %s", turn, exc)
            messages.append({"role": "user", "content": f"[feedback] {exc}"})
            continue
        except FatalError:
            logger.exception("fatal error, abort session")
            raise
    raise LLMTimeoutError(f"exceeded max_turns={max_turns}")
```

---

## 5. 测试基建

`pytest.ini` 已配置：

- `pythonpath = .` — 测试可直接 `from utils.llm_client import LLMClient`，无需安装包
- `testpaths = tests`
- `--strict-markers` — 用未声明的 marker 直接报错，避免拼错

跑测试：

```bash
pytest                    # 全量
pytest tests/test_llm_client.py -q
pytest -k "timeout" -v    # 关键字过滤
```

写 `LLMClient` 的单测时，**不要真的连 Ollama**，应 mock `openai.OpenAI` 或注入一个伪造的 `LLMConfig`。示例：

```python
from unittest.mock import MagicMock, patch
from utils.llm_client import LLMClient, LLMConfig

def make_client():
    cfg = LLMConfig(
        base_url="http://x", api_key="x", model="x",
        timeout_seconds=1, max_retries=0, temperature=0,
    )
    with patch("utils.llm_client.OpenAI") as fake:
        client = LLMClient(cfg)
        client._client = MagicMock()
    return client
```

---

## 6. 沙箱基础镜像

`Dockerfile` 构建 `tiny-devin-base:latest`，这是**所有 TaskMode 共享的最小可用镜像**：

```bash
docker build -t tiny-devin-base:latest .
```

预装库：`requests / httpx / beautifulsoup4 / lxml / numpy / pandas / matplotlib / scipy / pytest`。
两类典型脚本（修 bug 用单测、跑分析用统计）开箱即用，业务上若缺包再 `pip install` 动态补漏（参见规范 §5）。

`executor.py` 启动容器时应：

1. 通过 `WORKSPACE_DIR` 环境变量找到宿主机目录
2. 以 volume 形式挂载到容器 `/workspace`（双向同步约定）
3. 应用 `CONTAINER_MEMORY_LIMIT` / `CONTAINER_CPU_LIMIT` 配额
4. 失败时按 §3 的异常分类抛 `ContainerImageError` / `ResourceExhausted` / `SandboxViolation`

---

## 7. 待业务层补全的接口位

下列函数 / 类还**没有**实现，但已被 `api.md` 中的范式预设了形状。后续实现时请尽量保持签名稳定：

| 模块 | 接口 | 形状预期 |
|---|---|---|
| `core.schema` | `TaskMode`, `Evidence`, `ChatMessage`, `FileOperation`, `ExecutionResult` | Pydantic v2 模型，详见 `项目规范.md` §2 |
| `utils.parser` | `parse(text: str, *, mode: TaskMode) -> ParsedOutput` | 失败抛 `CodeFormatError` / `MissingPathError` |
| `runtime.executor` | `Executor.run(files: List[FileOperation]) -> ExecutionResult` | 失败抛 `ContainerImageError` / `ResourceExhausted` / `SandboxViolation` |
| `core.session_manager` | `SessionManager` | 心跳 + 超时清理，单容器单会话 |
| `core.workflow` | `Workflow.run_until_done(initial_prompt, mode) -> ...` | 遇 **`FatalError`**：立即终止 session；遇 **`EvidenceConflict`**（属 `RetryableError`）：进入反思/修订循环；其它 **`RetryableError`**：带反馈同 session 重试 |
| `prompt_templates` | `SystemPromptBuilder(role, mode).render() -> str` | 角色 × 模式查表（参见规范 §4.1） |

---

## 附：当前可独立运行的"最小验证"

只用基础层就能跑通的 smoke check（前提：本地 Ollama 已起，对应模型已 pull）：

```python
# smoke.py
from utils.llm_client import LLMClient

client = LLMClient()
print(client.chat([{"role": "user", "content": "say hi in 5 words"}]))
```

```bash
python smoke.py
```

如果能打印一段文本，说明配置 / 网络 / 异常封装一切正常，可以放心进入业务层开发。
