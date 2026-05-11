"""Pydantic v2 protocol definitions.

This module is the single source of truth for every payload that crosses
module boundaries (LLM messages, tool calls, execution results, trace
events, plans, reflections). It must remain **mode-agnostic**: the same
schema serves both DEVELOPMENT (code agent) and PHILOSOPHY (research
agent) workloads.
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


# --------------------------------------------------------------------------- #
# Enums
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
    DONE = "done"
    FAILED = "failed"


class AgentRole(str, Enum):
    CODER = "coder"
    REVIEWER = "reviewer"
    PHILOSOPHER = "philosopher"
    INVESTIGATOR = "investigator"
    PLANNER = "planner"
    REFLECTOR = "reflector"


# --------------------------------------------------------------------------- #
# Conversation payloads
# --------------------------------------------------------------------------- #

class ChatMessage(BaseModel):
    """A single turn in the conversation log.

    ``thought`` is required when the workflow is in PHILOSOPHY mode and the
    role is ASSISTANT; enforcement happens at the parser layer rather than
    here so this model stays reusable in unit tests.
    """

    role: MessageRole
    content: str
    thought: Optional[str] = None
    # Only required when role == TOOL: which tool_call this message answers.
    tool_call_id: Optional[str] = None
    # Free-form metadata (timestamps, model name, etc.).
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def to_openai(self) -> Dict[str, Any]:
        """Render to the dict shape openai's SDK expects."""
        out: Dict[str, Any] = {"role": self.role.value, "content": self.content}
        if self.tool_call_id is not None:
            out["tool_call_id"] = self.tool_call_id
        return out


# --------------------------------------------------------------------------- #
# File operations (used by both <file> blocks and the file_io tool)
# --------------------------------------------------------------------------- #

class FileOperation(BaseModel):
    file_path: str = Field(..., description="Workspace-relative path")
    content: Optional[str] = None
    action: FileAction = FileAction.WRITE

    @field_validator("file_path")
    @classmethod
    def _no_escape(cls, v: str) -> str:
        # Defensive: block absolute paths and parent-traversal so a malicious
        # LLM cannot write outside the workspace. Executor performs its own
        # check too; this is belt-and-suspenders.
        v_clean = v.strip()
        if not v_clean:
            raise ValueError("file_path must be non-empty")
        if v_clean.startswith(("/", "\\")):
            raise ValueError("file_path must be relative")
        if ".." in v_clean.replace("\\", "/").split("/"):
            raise ValueError("file_path must not contain '..'")
        return v_clean


# --------------------------------------------------------------------------- #
# Evidence (mode-agnostic data point supporting/refuting a claim)
# --------------------------------------------------------------------------- #

class Evidence(BaseModel):
    type: EvidenceType
    source: str = Field(..., description="URL, relative path or script name")
    summary: str = Field(..., description="1-3 sentences interpreting the evidence")
    payload: Optional[Dict[str, Any]] = None


# --------------------------------------------------------------------------- #
# Execution results
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
# Tools
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
    """Declarative description of a tool, surfaced to the LLM in prompts."""

    name: str
    description: str
    # JSON Schema for the args dict. We don't enforce strict schema parsing
    # in this base layer; tools validate themselves.
    args_schema: Dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Planning & Reflection (the cognitive loop's intermediate artifacts)
# --------------------------------------------------------------------------- #

class Plan(BaseModel):
    goal: str
    steps: List[str]
    notes: Optional[str] = None


ReflectionDecision = Literal["retry", "revise", "done"]


class Reflection(BaseModel):
    plan_ref: Optional[str] = Field(
        default=None, description="A short tag pointing at the plan being reviewed"
    )
    observations: str = Field(..., description="What actually happened this turn")
    conflicts: List[Evidence] = Field(default_factory=list)
    next_action: ReflectionDecision


# --------------------------------------------------------------------------- #
# Trace event (audit log line)
# --------------------------------------------------------------------------- #

class TraceEvent(BaseModel):
    ts: float = Field(default_factory=time.time)
    session_id: str
    turn: int
    state: AgentState
    kind: str
    payload: Dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Parser output (returned by utils.parser)
# --------------------------------------------------------------------------- #

class ParsedOutput(BaseModel):
    files: List[FileOperation] = Field(default_factory=list)
    thoughts: List[str] = Field(default_factory=list)
    tool_calls: List[ToolCall] = Field(default_factory=list)
    # Free-form structured payloads (Plan / Reflection / etc.).
    json_blocks: List[Dict[str, Any]] = Field(default_factory=list)
    # Whatever didn't match any structured block — kept for fallback logging.
    raw_text: str = ""

    def is_empty(self) -> bool:
        return not (self.files or self.thoughts or self.tool_calls or self.json_blocks)


# --------------------------------------------------------------------------- #
# Workflow result (returned to main.py)
# --------------------------------------------------------------------------- #

class WorkflowResult(BaseModel):
    session_id: str
    final_state: AgentState
    turns: int
    last_message: Optional[str] = None
    artifacts: List[str] = Field(default_factory=list)
    error: Optional[str] = None


__all__ = [
    # Enums
    "MessageRole", "FileAction", "TaskMode", "EvidenceType",
    "ToolStatus", "AgentState", "AgentRole",
    # Models
    "ChatMessage", "FileOperation", "Evidence", "ExecutionResult",
    "ToolCall", "ToolResult", "ToolSpec",
    "Plan", "Reflection", "TraceEvent",
    "ParsedOutput", "WorkflowResult",
    # Types
    "ReflectionDecision",
]
