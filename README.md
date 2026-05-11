# Tiny-Devin

一个**双形态 Agent 底座**：同一套基础设施，两种用法。

- **DEVELOPMENT（开发）**：在 Docker 沙箱里生成 / 修复代码并跑测试。
- **PHILOSOPHY（思辨）**：在同一沙箱里做论证、爬数据、跑分析脚本，把 Docker 当作「实证实验室」。

执行器、解析器、数据模型、会话管理与 LLM 客户端都**与任务类型解耦**；区分两种模式主要靠 **Prompt** 与 **工作流策略**。完整设计见 [`项目规范.md`](./项目规范.md)（V1.2）。

---

## 目录结构

```text
core/                # 异常等核心原语（schema / workflow 等按规范后续补齐）
utils/               # 通用工具（当前含 LLM 客户端；parser 等后续补齐）
runtime/             # 沙箱执行与工作区（executor 等待实现）
tests/               # pytest（按需要添加）
prompt_templates.py  # 规划中：按 TaskMode 构建系统提示
main.py              # 规划中：闭环入口
api.md               # 基础层 API 说明（环境变量、重试、异常约定等）
```

---

## 环境要求

- Python 3.12+
- 本机已安装并运行 [Docker](https://docs.docker.com/engine/)（构建沙箱镜像用）
- 本机已安装并运行 [Ollama](https://ollama.com/)，且已 `pull` 至少一个可用的对话模型

---

## 快速开始

```bash
# 1. 创建虚拟环境并安装依赖
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux / macOS:
# source .venv/bin/activate

pip install -r requirements.txt

# 2. 配置环境变量（至少把 OLLAMA_MODEL 改成你已拉取的模型名）
copy .env.example .env
# Linux / macOS: cp .env.example .env

# 3. 构建沙箱基础镜像（与 .env 中 DOCKER_BASE_IMAGE 默认一致）
docker build -t tiny-devin-base:latest .
```

当前仓库以**基础层**为主；解析器、执行器、`main.py` 闭环等按 [`项目规范.md`](./项目规范.md) 逐步实现。基础层联调 Ollama 的最小示例见下文「最小验证」与 [`api.md`](./api.md) 文末。

---

## 基础层调用方式

以下均在项目根目录执行，且已 `pip install -r requirements.txt`。若从子目录运行脚本，请保证 `PYTHONPATH` 包含项目根（与 `pytest.ini` 中 `pythonpath = .` 一致），例如在根目录执行 `python your_script.py`。

### 从包入口导入（推荐）

`core` 与 `utils` 已在各自 `__init__.py` 中汇总常用符号，可直接：

```python
from utils import LLMClient, LLMConfig, ChatMessage
from core import (
    RetryableError,
    FatalError,
    LLMTimeoutError,
    CodeFormatError,
    MissingPathError,
    EvidenceConflict,
    SandboxViolation,
    ContainerImageError,
    ResourceExhausted,
    TinyDevinError,
)
```

等价于 `from utils.llm_client import ...`、`from core.exceptions import ...`。

### `LLMClient`：同步对话

```python
from utils import LLMClient

client = LLMClient()  # 使用 LLMConfig.from_env()，会尝试加载当前目录下的 .env

text = client.chat(
    [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "用一句话介绍 Python。"},
    ]
)
print(text)
```

单轮临时改模型、温度、最大 token 或透传底层参数：

```python
text = client.chat(
    messages,
    model="qwen2.5-coder:7b",
    temperature=0.0,
    max_tokens=512,
    extra={"top_p": 0.9},
)
```

### `LLMClient`：流式输出

```python
from utils import LLMClient

client = LLMClient()
messages = [{"role": "user", "content": "数到 5，每次一个词。"}]

for chunk in client.stream_chat(messages):
    print(chunk, end="", flush=True)
```

### 显式配置（测试或固定参数）

```python
from utils import LLMClient, LLMConfig

cfg = LLMConfig(
    base_url="http://localhost:11434/v1",
    api_key="ollama",
    model="qwen2.5-coder:7b",
    timeout_seconds=120.0,
    max_retries=2,
    temperature=0.2,
)
client = LLMClient(cfg)
```

也可用 `LLMConfig.from_env()` 从环境变量构造（默认会尝试 `load_dotenv()`）。

### 异常与上层处理

自定义异常分为 **`RetryableError`**（可在同一会话内重试）与 **`FatalError`**（应终止会话）。`LLMClient` 在超时 / 连接 / 限流重试用尽后会抛出 **`LLMTimeoutError`**（属于 `RetryableError`）。简要范式：

```python
from core import RetryableError, FatalError
from utils import LLMClient

def run_turn(client: LLMClient, messages: list) -> str:
    try:
        return client.chat(messages)
    except RetryableError:
        raise
    # 业务层也可在这里区分 EvidenceConflict 等


try:
    reply = run_turn(LLMClient(), [{"role": "user", "content": "hi"}])
except RetryableError as exc:
    # 将 exc / exc.details 写入下一轮反馈 prompt
    ...
except FatalError:
    # 关闭容器、记录日志、向用户报错
    ...
```

更完整的重试表、`details` 约定、环境变量列表见 [**api.md**](./api.md)。

### 最小验证（需 Ollama 已启动且模型可用）

在项目根目录新建 `smoke.py`：

```python
from utils import LLMClient

if __name__ == "__main__":
    print(LLMClient().chat([{"role": "user", "content": "用不超过五个词打个招呼。"}]))
```

```bash
python smoke.py
```

能打印模型回复即表示环境变量、网络与客户端封装基本正常。

---

## 运行测试

```bash
pytest
```

（测试目录与用例可按 [`api.md`](./api.md) 中测试基建说明自行补充。）

---

## 当前范围说明

本阶段聚焦**稳定基础层**；下列能力不在当前迭代范围内，待底座稳定后再扩展：

- 多 Agent 并发与消息总线
- 长期 / 向量记忆
- Web 前端
- 多容器调度 / Kubernetes
