"""状态机编排层。

Workflow 驱动一个 Agent session 走完下面这个循环：

    IDLE -> THINKING -> ACTING -> OBSERVING -> REFLECTING -> ...

最终落到 ``DONE`` 或 ``FAILED``。它把代码库里的所有其它层串到一起：

* ``LLMClient``           -> 产出 assistant 一轮回复
* ``SessionContext``      -> 记住目前为止的对话
* ``parse_response``      -> 把 assistant 回复结构化
* ``ToolRegistry``        -> 分发工具调用
* ``Executor``            -> 通过工具间接驱动代码 / 脚本沙箱
* ``TraceLogger``         -> 记录每一次状态切换
* ``SystemPromptBuilder`` -> 按 (role, mode) 注入 System 消息

冲突检测与反思阶段都遵循规范中的分工：任何 :class:`RetryableError` 都会回到
THINKING 并把错误作为反馈附加进上下文；任何 :class:`FatalError` 都会直接把
状态切到 FAILED 并先把轨迹刷盘。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from core.audit import (
    KIND_ERROR,
    KIND_PROMPT,
    KIND_REFLECTION,
    KIND_RESPONSE,
    KIND_STATE_CHANGE,
    KIND_TOOL_CALL,
    KIND_TOOL_RESULT,
    TraceLogger,
)
from core.exceptions import (
    CodeFormatError,
    EvidenceConflict,
    FatalError,
    LLMTimeoutError,
    RetryableError,
    TinyDevinError,
)
from core.schema import (
    AgentRole,
    AgentState,
    ChatMessage,
    MessageRole,
    ParsedOutput,
    TaskMode,
    ToolStatus,
    WorkflowResult,
)
from memory.session_context import SessionContext
from prompt_templates import SystemPromptBuilder
from tools.registry import ToolRegistry
from utils.llm_client import LLMClient
from utils.parser import parse_response

logger = logging.getLogger(__name__)


# Assistant 可以输出的"我已经做完了"哨兵子串。
# 同时接受中英文标记，让同一套 workflow 既能驱动中文模型也能驱动英文模型。
_DONE_MARKERS = (
    "[done]",
    "<done/>",
    "<done></done>",
    "[完成]",
    "<完成/>",
    "<完成></完成>",
)


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class WorkflowConfig:
    max_turns: int = 10
    role: AgentRole = AgentRole.CODER

    @classmethod
    def from_env(cls, *, role: Optional[AgentRole] = None) -> "WorkflowConfig":
        return cls(
            max_turns=int(os.getenv("WORKFLOW_MAX_TURNS", "10")),
            role=role or AgentRole.CODER,
        )


# --------------------------------------------------------------------------- #
# Workflow
# --------------------------------------------------------------------------- #

class Workflow:
    def __init__(
        self,
        *,
        llm: LLMClient,
        tools: ToolRegistry,
        context: SessionContext,
        trace: TraceLogger,
        mode: TaskMode,
        config: Optional[WorkflowConfig] = None,
    ) -> None:
        self._llm = llm
        self._tools = tools
        self._context = context
        self._trace = trace
        self._mode = mode
        self._config = config or WorkflowConfig.from_env()
        self._state = AgentState.IDLE
        self._turn = 0

    # ------------------- 公共 API ------------------- #

    @property
    def state(self) -> AgentState:
        return self._state

    @property
    def turn(self) -> int:
        return self._turn

    def run(self, user_prompt: str) -> WorkflowResult:
        """跑完整个循环直到终止，返回 :class:`WorkflowResult`。"""
        try:
            self._seed_context(user_prompt)
        except FatalError as exc:
            return self._finalize_failed(str(exc))

        while True:
            if self._turn >= self._config.max_turns:
                return self._finalize_failed(
                    f"已达到 max_turns={self._config.max_turns} 但仍未结束",
                )

            self._turn += 1

            try:
                reply = self._think()
                parsed = self._parse_reply(reply)

                acted = self._maybe_act(parsed)

                if self._is_terminal_reply(reply, parsed) and not acted:
                    return self._finalize_done(reply)

                self._reflect(parsed, acted)

            except RetryableError as exc:
                self._handle_retryable(exc)
                continue
            except FatalError as exc:
                return self._finalize_failed(str(exc))
            except Exception as exc:  # noqa: BLE001 -- 保证 session 不会无声崩溃
                logger.exception("workflow 发生未预期错误")
                return self._finalize_failed(f"{type(exc).__name__}: {exc}")

    # ------------------- 状态机各阶段 ------------------- #

    def _seed_context(self, user_prompt: str) -> None:
        # 仅当调用方还没有装载过 System prompt 时才注入。
        if not any(m.role is MessageRole.SYSTEM for m in self._context.messages()):
            system = SystemPromptBuilder(
                role=self._config.role,
                mode=self._mode,
                tools=self._tools.list_specs(),
            ).render()
            self._context.add(ChatMessage(role=MessageRole.SYSTEM, content=system))

        self._context.add(ChatMessage(role=MessageRole.USER, content=user_prompt))
        self._transition(AgentState.IDLE)
        self._trace.log(
            KIND_PROMPT,
            {"user": user_prompt, "role": self._config.role.value, "mode": self._mode.value},
            state=self._state,
            turn=self._turn,
        )

    def _think(self) -> str:
        self._transition(AgentState.THINKING)
        try:
            reply = self._llm.chat(self._context.to_openai())
        except LLMTimeoutError:
            raise
        self._trace.log(
            KIND_RESPONSE, {"content": reply}, state=self._state, turn=self._turn
        )
        self._context.add(ChatMessage(role=MessageRole.ASSISTANT, content=reply))
        return reply

    def _parse_reply(self, reply: str) -> ParsedOutput:
        # 顶层保持宽容：assistant 这一轮可能纯粹是叙述（"我先来规划一下..."），
        # 没有任何结构化块。parser 仍会强制 PHILOSOPHY 模式必须出现 <thought>。
        try:
            return parse_response(reply, mode=self._mode, require_block=False)
        except CodeFormatError:
            raise

    def _maybe_act(self, parsed: ParsedOutput) -> bool:
        if not parsed.tool_calls:
            return False

        self._transition(AgentState.ACTING)
        results = []
        for call in parsed.tool_calls:
            self._trace.log(
                KIND_TOOL_CALL,
                {"name": call.name, "args": call.args, "id": call.id},
                state=self._state,
                turn=self._turn,
            )
            result = self._tools.invoke(call)
            self._trace.log(
                KIND_TOOL_RESULT,
                {
                    "name": result.name,
                    "status": result.status.value,
                    "output": _safe_truncate(result.output),
                    "error": result.error,
                    "id": result.call_id,
                },
                state=self._state,
                turn=self._turn,
            )
            results.append(result)

        self._transition(AgentState.OBSERVING)
        for result in results:
            tool_message = ChatMessage(
                role=MessageRole.TOOL,
                tool_call_id=result.call_id,
                content=_format_tool_result_for_llm(result),
            )
            self._context.add(tool_message)

        # 任何工具失败时记录一条日志，REFLECTING 阶段会把它当成冲突处理。
        for result in results:
            if result.status is ToolStatus.FAILED:
                logger.info("工具 %s 失败，将进入反思阶段", result.name)
        return True

    def _reflect(self, parsed: ParsedOutput, acted: bool) -> None:
        self._transition(AgentState.REFLECTING)

        # 在 assistant 输出里找符合 Reflection 形状的 JSON 块；如果有，
        # 按 `next_action` 决定流向。否则默认行为：
        #   - acted=True  -> 继续下一轮
        #   - acted=False -> 继续下一轮（LLM 可能还需要更多轮次）
        decision = None
        for block in parsed.json_blocks:
            if "next_action" in block:
                decision = str(block.get("next_action", "")).lower().strip()
                self._trace.log(
                    KIND_REFLECTION, block, state=self._state, turn=self._turn
                )
                break

        if decision == "done":
            self._state = AgentState.DONE
            return
        if decision == "revise":
            self._context.add(
                ChatMessage(
                    role=MessageRole.SYSTEM,
                    content="[反思器] 已请求修订计划；请重新评估策略后再继续。",
                )
            )
            return
        if decision == "retry":
            # 保持当前计划，进入下一轮。
            return

        # 未显式给出反思决定时，不改变状态，正常继续循环。
        return

    # ------------------- 错误处理 ------------------- #

    def _handle_retryable(self, exc: TinyDevinError) -> None:
        kind = type(exc).__name__
        self._trace.log(
            KIND_ERROR,
            {"kind": kind, "message": str(exc), "details": getattr(exc, "details", None)},
            state=self._state,
            turn=self._turn,
        )
        feedback = self._format_feedback(exc)
        self._context.add(ChatMessage(role=MessageRole.SYSTEM, content=feedback))

    def _format_feedback(self, exc: TinyDevinError) -> str:
        kind = type(exc).__name__
        details = getattr(exc, "details", None)
        if isinstance(exc, EvidenceConflict):
            return (
                f"[反馈] 检测到冲突：{exc}。"
                "请基于新证据，重新审视先前的主张并调整下一步行动。"
            )
        if isinstance(exc, CodeFormatError):
            return (
                f"[反馈] 你上一轮的响应无法被解析（{exc}）。"
                "请严格按照系统提示中规定的结构化标签格式（<file> / "
                "<thought> / <tool> / ```json``` 代码块）重新作答。"
            )
        suffix = f" 细节：{details}" if details else ""
        return f"[反馈] 可重试失败（{kind}）：{exc}。{suffix}"

    # ------------------- 终止处理 ------------------- #

    def _is_terminal_reply(self, reply: str, parsed: ParsedOutput) -> bool:
        lowered = reply.lower()
        if any(marker in lowered for marker in _DONE_MARKERS):
            return True
        for block in parsed.json_blocks:
            if str(block.get("next_action", "")).lower() == "done":
                return True
        return False

    def _finalize_done(self, last_reply: str) -> WorkflowResult:
        self._transition(AgentState.DONE)
        return WorkflowResult(
            session_id=self._trace.session_id,
            final_state=self._state,
            turns=self._turn,
            last_message=last_reply,
        )

    def _finalize_failed(self, error: str) -> WorkflowResult:
        self._transition(AgentState.FAILED)
        return WorkflowResult(
            session_id=self._trace.session_id,
            final_state=self._state,
            turns=self._turn,
            error=error,
        )

    # ------------------- 辅助函数 ------------------- #

    def _transition(self, new_state: AgentState) -> None:
        if new_state is self._state:
            return
        old = self._state
        self._state = new_state
        self._trace.log(
            KIND_STATE_CHANGE,
            {"from": old.value, "to": new_state.value},
            state=new_state,
            turn=self._turn,
        )


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #

def _safe_truncate(value, limit: int = 2000):
    """把过大的载荷截断后再写入审计日志。"""
    try:
        text = repr(value)
    except Exception:  # noqa: BLE001
        return "<repr 失败>"
    if len(text) <= limit:
        return value
    return text[:limit] + f" ...[已截断 {len(text) - limit} 个字符]"


def _format_tool_result_for_llm(result) -> str:
    if result.status is ToolStatus.SUCCESS:
        return f"[工具 {result.name} 成功]\n{result.output}"
    return f"[工具 {result.name} 失败]\n{result.error}"


__all__ = ["Workflow", "WorkflowConfig"]
