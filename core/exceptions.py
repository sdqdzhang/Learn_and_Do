"""Tiny-Devin 统一异常体系。

整棵异常树有两个顶层分支，对应 workflow 的控制流分流逻辑：

    TinyDevinError
    ├── RetryableError   --> workflow 可以在同一个 session 内重试
    └── FatalError       --> workflow 必须立刻终止 session

业务侧的失败信号（单元测试失败、假设被证伪）**不**建模成异常，而是承载在
``ExecutionResult`` / ``Evidence`` 里，由 workflow 决定是否升格成
``EvidenceConflict``。这样异常层保持任务模式无关，DEVELOPMENT 与 PHILOSOPHY
共用同一套 except 分支。
"""

from __future__ import annotations

from typing import Any, Optional


class TinyDevinError(Exception):
    """项目中所有自定义异常的根类。"""

    def __init__(self, message: str = "", *, details: Optional[Any] = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def __str__(self) -> str:  # pragma: no cover - 简单字符串拼接
        if self.details is None:
            return self.message
        return f"{self.message} | details={self.details!r}"


class RetryableError(TinyDevinError):
    """可被 workflow 在不中止 session 的前提下重试的错误。"""


class FatalError(TinyDevinError):
    """必须立即终止当前 session 的错误。"""


# --------------------------------------------------------------------------- #
# 可重试 (Retryable)
# --------------------------------------------------------------------------- #

class CodeFormatError(RetryableError):
    """LLM 输出无法解析为 <file> / <thought> / <tool> / json 块。"""


class EmptyAssistantReplyError(RetryableError):
    """LLM 返回的 assistant 正文在重试预算内仍为空或仅空白。"""


class MissingPathError(RetryableError):
    """找到了代码块但缺少 ``path`` 属性。"""


class LLMTimeoutError(RetryableError):
    """LLM 在配置的超时 / 重试预算内没有响应。"""


class EvidenceConflict(RetryableError):
    """Executor 返回的数据与当前主张冲突。

    - DEVELOPMENT 模式：单元测试失败，让 Coder 修。
    - PHILOSOPHY  模式：数据反驳了假设，让 Philosopher 修订模型。
    """


class ToolError(RetryableError):
    """工具调用失败但可重试。

    与 SandboxViolation 不同：工具实际跑起来了，只是产出了错误结果
    （网络抖动、文件不存在、参数错误等）。
    """


class MemoryError(RetryableError):
    """记忆层瞬态故障（如向量库磁盘抖动）。"""


# --------------------------------------------------------------------------- #
# 熔断 (Fatal)
# --------------------------------------------------------------------------- #

class SandboxViolation(FatalError):
    """Agent 试图执行违规操作（例如 rm -rf /）。"""


class ContainerImageError(FatalError):
    """基础 Docker 镜像缺失或无法构建。"""


class ResourceExhausted(FatalError):
    """容器超过了内存 / GPU / 磁盘配额。"""


class ConfigurationError(FatalError):
    """必需的配置缺失或非法（例如 Ollama 不可达）。"""


__all__ = [
    "TinyDevinError",
    "RetryableError",
    "FatalError",
    "CodeFormatError",
    "EmptyAssistantReplyError",
    "MissingPathError",
    "LLMTimeoutError",
    "EvidenceConflict",
    "ToolError",
    "MemoryError",
    "SandboxViolation",
    "ContainerImageError",
    "ResourceExhausted",
    "ConfigurationError",
]
