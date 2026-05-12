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
from dataclasses import dataclass, field
from typing import FrozenSet, Optional, Tuple, cast

from core.audit import (
    KIND_ERROR,
    KIND_HUMAN_OVERRIDE,
    KIND_INTERVENTION_SUSPEND,
    KIND_MULTI_REFLECTION,
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
from core.intervention import InterventionChannel
from core.schema import (
    AgentRole,
    AgentState,
    ChatMessage,
    MessageRole,
    MultiReflectionBundle,
    ParsedOutput,
    ReflectionDecision,
    RoleReflection,
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
    intervention_enabled: bool = False
    intervention_timeout_s: float = 0.0
    sensitive_tool_names: FrozenSet[str] = field(default_factory=frozenset)
    multi_reflection_roles: Tuple[AgentRole, ...] = field(default_factory=tuple)

    @classmethod
    def from_env(cls, *, role: Optional[AgentRole] = None) -> "WorkflowConfig":
        raw_multi = os.getenv("WORKFLOW_MULTI_REFLECTION_ROLES", "").strip()
        multi: list[AgentRole] = []
        if raw_multi:
            for part in raw_multi.split(","):
                p = part.strip()
                if not p:
                    continue
                try:
                    multi.append(AgentRole(p))
                except ValueError:
                    continue
        sens_raw = os.getenv("WORKFLOW_SENSITIVE_TOOLS", "").strip()
        sensitive = frozenset(t.strip() for t in sens_raw.split(",") if t.strip())
        iv = os.getenv("WORKFLOW_INTERVENTION_ENABLED", "").lower() in ("1", "true", "yes")
        iv_to = float(os.getenv("WORKFLOW_INTERVENTION_TIMEOUT_S", "0"))
        return cls(
            max_turns=int(os.getenv("WORKFLOW_MAX_TURNS", "10")),
            role=role or AgentRole.CODER,
            intervention_enabled=iv,
            intervention_timeout_s=iv_to,
            sensitive_tool_names=sensitive,
            multi_reflection_roles=tuple(multi),
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
        intervention: Optional[InterventionChannel] = None,
    ) -> None:
        self._llm = llm
        self._tools = tools
        self._context = context
        self._trace = trace
        self._mode = mode
        self._config = config or WorkflowConfig.from_env()
        self._intervention = intervention
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
            context=self._context,
        )

    def _think(self) -> str:
        self._transition(AgentState.THINKING)
        try:
            reply, usage = self._llm.chat_with_usage(self._context.to_openai())
        except LLMTimeoutError:
            raise
        parsed = parse_response(reply, mode=self._mode, require_block=False)
        self._trace.log(
            KIND_RESPONSE,
            {
                "content": reply,
                "thoughts": parsed.thoughts,
                "usage": usage,
            },
            state=self._state,
            turn=self._turn,
            context=self._context,
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
            if call.name in self._config.sensitive_tool_names:
                self._maybe_human_intervention(phase=f"pre_tool:{call.name}")
            self._trace.log(
                KIND_TOOL_CALL,
                {"name": call.name, "args": call.args, "id": call.id},
                state=self._state,
                turn=self._turn,
                context=self._context,
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
                context=self._context,
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

        decision: Optional[str] = None
        reflection_block: Optional[dict] = None
        for block in parsed.json_blocks:
            if "next_action" in block:
                decision = str(block.get("next_action", "")).lower().strip()
                reflection_block = block
                self._trace.log(
                    KIND_REFLECTION,
                    block,
                    state=self._state,
                    turn=self._turn,
                    context=self._context,
                )
                break

        if self._config.multi_reflection_roles:
            decision = self._merge_multi_reflection(decision, reflection_block, parsed, acted)

        override = self._maybe_human_intervention(phase="post_reflect")
        if override is not None:
            decision = override

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
            return
        return

    def _merge_multi_reflection(
        self,
        primary_decision: Optional[str],
        reflection_block: Optional[dict],
        parsed: ParsedOutput,
        acted: bool,
    ) -> Optional[str]:
        """多角色反思；``next_action`` 不一致时抛出 :class:`EvidenceConflict`。"""
        items: list[RoleReflection] = []
        if primary_decision and primary_decision in ("retry", "revise", "done"):
            rb = reflection_block or {}
            items.append(
                RoleReflection(
                    role=self._config.role,
                    observations=str(rb.get("observations", "")),
                    next_action=cast(ReflectionDecision, primary_decision),
                    raw_block=dict(rb),
                )
            )
        for role in self._config.multi_reflection_roles:
            na = self._reflect_as_role(role, parsed, acted)
            items.append(
                RoleReflection(
                    role=role,
                    observations="",
                    next_action=cast(ReflectionDecision, na),
                    raw_block={},
                )
            )

        bundle = MultiReflectionBundle(items=items)
        self._trace.log(
            KIND_MULTI_REFLECTION,
            bundle.model_dump(mode="json"),
            state=self._state,
            turn=self._turn,
            context=self._context,
        )

        actions = [it.next_action for it in items if it.next_action in ("retry", "revise", "done")]
        if not actions:
            return primary_decision
        unique = set(actions)
        if len(unique) > 1:
            raise EvidenceConflict(
                "多角色反思对 next_action 结论不一致",
                details={"bundle": bundle.model_dump(mode="json")},
            )
        return actions[0]

    def _reflect_as_role(self, role: AgentRole, parsed: ParsedOutput, acted: bool) -> str:
        """为附加角色单独发起一次短反思 LLM 调用。"""
        system = SystemPromptBuilder(role, self._mode, self._tools.list_specs()).render()
        digest = (parsed.raw_text or "")[:1200]
        user_msg = (
            f"本轮 acted={acted}。\n"
            f"主助手输出摘录（含结构化块）：\n{digest}\n\n"
            "请只输出一个 ```json``` 反思块，必须包含 next_action，取值 retry|revise|done。"
        )
        reply = self._llm.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ]
        )
        pr = parse_response(reply, mode=self._mode, require_block=False)
        for block in pr.json_blocks:
            if "next_action" in block:
                v = str(block.get("next_action", "")).lower().strip()
                if v in ("retry", "revise", "done"):
                    return v
        return "retry"

    def _maybe_human_intervention(self, *, phase: str) -> Optional[str]:
        """若启用干预且 ``timeout>0``，在 ``INTERVENTION`` 状态等待人类。

        若收到人类文本，写入 USER 消息并返回 ``\"retry\"`` 以覆盖主反思结论
        （上帝指令优先）；否则返回 ``None``。
        """
        if not (self._intervention and self._config.intervention_enabled):
            return None
        timeout = float(self._config.intervention_timeout_s)
        if timeout <= 0:
            return None
        self._transition(AgentState.INTERVENTION)
        self._trace.log(
            KIND_INTERVENTION_SUSPEND,
            {"phase": phase, "timeout_s": timeout},
            state=self._state,
            turn=self._turn,
            context=self._context,
        )
        msg = self._intervention.wait(timeout=timeout)
        if msg:
            self._trace.log(
                KIND_HUMAN_OVERRIDE,
                {"text": msg, "phase": phase},
                state=self._state,
                turn=self._turn,
                context=self._context,
            )
            self._context.add(
                ChatMessage(role=MessageRole.USER, content=f"[上帝指令] {msg}")
            )
            if self._state is AgentState.INTERVENTION:
                self._transition(AgentState.REFLECTING)
            return "retry"
        if self._state is AgentState.INTERVENTION:
            self._transition(AgentState.REFLECTING)
        return None

    # ------------------- 错误处理 ------------------- #

    def _handle_retryable(self, exc: TinyDevinError) -> None:
        kind = type(exc).__name__
        self._trace.log(
            KIND_ERROR,
            {"kind": kind, "message": str(exc), "details": getattr(exc, "details", None)},
            state=self._state,
            turn=self._turn,
            context=self._context,
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
            context=self._context,
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
