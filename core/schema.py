"""Pydantic v2 协议定义。

本模块是项目里所有跨模块传递数据（LLM 消息、工具调用、执行结果、轨迹事件、
计划、反思）的唯一真源。它必须保持 **任务模式无关**：DEVELOPMENT（代码 Agent）
与 PHILOSOPHY（哲学研究 Agent）共用同一套 Schema。
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# --------------------------------------------------------------------------- #
# 枚举
# --------------------------------------------------------------------------- #

class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class FileAction(str, Enum):
    WRITE = "write"
    DELETE = "delete"
    READ = "read"


class TaskMode(str, Enum):
    DEVELOPMENT = "development"
    PHILOSOPHY = "philosophy"


class EvidenceType(str, Enum):
    CODE_RESULT = "code_result"
    WEB_SOURCE = "web_source"
    DATA_FILE = "data_file"


class ToolStatus(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


class AgentState(str, Enum):
    IDLE = "idle"
    THINKING = "thinking"
    ACTING = "acting"
    OBSERVING = "observing"
    REFLECTING = "reflecting"
    INTERVENTION = "intervention"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentRole(str, Enum):
    CODER = "coder"
    REVIEWER = "reviewer"
    PHILOSOPHER = "philosopher"
    INVESTIGATOR = "investigator"
    PLANNER = "planner"
    REFLECTOR = "reflector"
    AUDITOR = "auditor"
    CHALLENGER = "challenger"


# --------------------------------------------------------------------------- #
# 对话相关
# --------------------------------------------------------------------------- #

class ChatMessage(BaseModel):
    """对话日志里的一轮消息。

    当 workflow 处于 PHILOSOPHY 模式且 ``role`` 为 ASSISTANT 时，``thought``
    必须有值；该约束由 parser 层执行，模型本身不强制（这样在单测里这个模型
    仍能复用）。
    """

    role: MessageRole
    content: str
    thought: Optional[str] = None
    # 仅当 role == TOOL 时必填：标识本条消息回应的是哪个 tool_call。
    tool_call_id: Optional[str] = None
    # 自由元数据（时间戳、模型名等）。
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def to_openai(self) -> Dict[str, Any]:
        """渲染成 openai SDK 期望的 dict 形态。"""
        out: Dict[str, Any] = {"role": self.role.value, "content": self.content}
        if self.tool_call_id is not None:
            out["tool_call_id"] = self.tool_call_id
        return out


# --------------------------------------------------------------------------- #
# 文件操作（同时被 <file> 块与 file_io 工具使用）
# --------------------------------------------------------------------------- #

class FileOperation(BaseModel):
    file_path: str = Field(..., description="相对于工作空间的相对路径")
    content: Optional[str] = None
    action: FileAction = FileAction.WRITE

    @field_validator("file_path")
    @classmethod
    def _no_escape(cls, v: str) -> str:
        # 防御性校验：拒绝绝对路径与父级穿越，避免恶意 LLM 写到工作空间之外。
        # Executor 还会再校验一次；这里属于双保险。
        v_clean = v.strip()
        if not v_clean:
            raise ValueError("file_path 不能为空")
        if v_clean.startswith(("/", "\\")):
            raise ValueError("file_path 必须是相对路径")
        if ".." in v_clean.replace("\\", "/").split("/"):
            raise ValueError("file_path 不允许出现 '..'")
        return v_clean


# --------------------------------------------------------------------------- #
# 证据（模式无关的数据点，可支持或反驳某个主张）
# --------------------------------------------------------------------------- #

class Evidence(BaseModel):
    type: EvidenceType
    source: str = Field(..., description="URL、相对路径或脚本名")
    summary: str = Field(..., description="1-3 句话解读这条证据")
    payload: Optional[Dict[str, Any]] = None


# --------------------------------------------------------------------------- #
# 执行结果
# --------------------------------------------------------------------------- #

class ExecutionResult(BaseModel):
    is_success: bool
    stdout: str = ""
    stderr: str = ""
    active_files: List[str] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    artifacts: List[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #

class ToolCall(BaseModel):
    id: str = Field(default_factory=lambda: f"tc-{uuid.uuid4().hex[:8]}")
    name: str
    args: Dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    call_id: str
    name: str
    status: ToolStatus
    output: Any = None
    error: Optional[str] = None
    metrics: Dict[str, Any] = Field(default_factory=dict)


class ToolSpec(BaseModel):
    """工具的声明式描述，会被注入到 system prompt 让 LLM 看到。"""

    name: str
    description: str
    # args dict 的 JSON Schema。基础层不强制全量 schema 校验，工具自己负责校验。
    args_schema: Dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# 规划与反思（认知循环的中间产物）
# --------------------------------------------------------------------------- #

class Plan(BaseModel):
    goal: str
    steps: List[str]
    notes: Optional[str] = None


ReflectionDecision = Literal["retry", "revise", "done"]


class Reflection(BaseModel):
    plan_ref: Optional[str] = Field(
        default=None, description="指向当前被复核的计划的短标识"
    )
    observations: str = Field(..., description="本轮实际发生了什么")
    conflicts: List[Evidence] = Field(default_factory=list)
    next_action: ReflectionDecision


class RoleReflection(BaseModel):
    """单角色在一次多视角反思中的结论（用于 Multi-Agent Reflection）。"""

    role: AgentRole
    observations: str = ""
    next_action: ReflectionDecision
    raw_block: Dict[str, Any] = Field(default_factory=dict)


class MultiReflectionBundle(BaseModel):
    """同一 Turn 内多角色反思结果的合并载体。"""

    items: List[RoleReflection] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# 轨迹事件（写入审计日志的一行）
# --------------------------------------------------------------------------- #

class TraceEvent(BaseModel):
    """一条可审计、可回放的状态化轨迹事件。

    ``event_id`` 用于时间旅行与前端锚点；``runtime_state_id`` 单调标识
    「运行时拍快照」次序；``context_snapshot`` 可选地嵌入
    :class:`memory.session_context.SessionContext` 的可逆快照。
    """

    ts: float = Field(default_factory=time.time)
    event_id: Optional[str] = None
    runtime_state_id: Optional[str] = None
    session_id: str
    turn: int
    state: AgentState
    kind: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    context_snapshot: Optional[Dict[str, Any]] = Field(
        default=None,
        description="SessionContext 快照；用于按 event_id 冷恢复内存",
    )

    @model_validator(mode="before")
    @classmethod
    def _legacy_trace_ids(cls, data: Any) -> Any:
        """兼容旧版 JSONL：无 event_id / runtime_state_id 时生成稳定占位。"""
        if not isinstance(data, dict):
            return data
        out = dict(data)
        if not out.get("event_id"):
            blob = json.dumps(out, sort_keys=True, default=str)
            out["event_id"] = "ev-" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]
        if not out.get("runtime_state_id"):
            out["runtime_state_id"] = "rs-legacy"
        return out


# --------------------------------------------------------------------------- #
# 解析输出（utils.parser 的返回结构）
# --------------------------------------------------------------------------- #

class ParsedOutput(BaseModel):
    files: List[FileOperation] = Field(default_factory=list)
    thoughts: List[str] = Field(default_factory=list)
    tool_calls: List[ToolCall] = Field(default_factory=list)
    # 自由形式的结构化载荷（Plan / Reflection 等）。
    json_blocks: List[Dict[str, Any]] = Field(default_factory=list)
    # 未匹配到任何结构化块的原文，保留供回退日志使用。
    raw_text: str = ""

    def is_empty(self) -> bool:
        return not (self.files or self.thoughts or self.tool_calls or self.json_blocks)


# --------------------------------------------------------------------------- #
# Workflow 最终结果（返回给 main.py）
# --------------------------------------------------------------------------- #

class WorkflowResult(BaseModel):
    session_id: str
    final_state: AgentState
    turns: int
    last_message: Optional[str] = None
    artifacts: List[str] = Field(default_factory=list)
    error: Optional[str] = None


__all__ = [
    # 枚举
    "MessageRole", "FileAction", "TaskMode", "EvidenceType",
    "ToolStatus", "AgentState", "AgentRole",
    # 模型
    "ChatMessage", "FileOperation", "Evidence", "ExecutionResult",
    "ToolCall", "ToolResult", "ToolSpec",
    "Plan", "Reflection", "RoleReflection", "MultiReflectionBundle", "TraceEvent",
    "ParsedOutput", "WorkflowResult",
    # 类型别名
    "ReflectionDecision",
]
