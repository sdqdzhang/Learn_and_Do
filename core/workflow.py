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
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Optional, Tuple, cast

from core.audit import (
    KIND_ERROR,
    KIND_GUARD_OUTBOUND,
    KIND_GUARD_PREFLIGHT,
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
from core.repair_guard import (
    OUTBOUND_SYSTEM,
    PREFLIGHT_SYSTEM,
    build_outbound_user,
    build_preflight_user,
    extract_json_verdict,
    summarize_tool_calls,
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
    # 独立 LLM 会话：工具执行前修复结构化输出；出站「假完成」校验。
    guard_preflight_enabled: bool = False
    guard_outbound_enabled: bool = False
    guard_max_rounds: int = 2
    guard_model: Optional[str] = None

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
        gf = os.getenv("WORKFLOW_GUARD_PREFLIGHT", "0").lower() in ("1", "true", "yes")
        go = os.getenv("WORKFLOW_GUARD_OUTBOUND", "0").lower() in ("1", "true", "yes")
        gmr = int(os.getenv("WORKFLOW_GUARD_MAX_ROUNDS", "2"))
        gmr = max(1, min(gmr, 5))
        gmodel = os.getenv("WORKFLOW_GUARD_MODEL", "").strip() or None
        return cls(
            max_turns=int(os.getenv("WORKFLOW_MAX_TURNS", "10")),
            role=role or AgentRole.CODER,
            intervention_enabled=iv,
            intervention_timeout_s=iv_to,
            sensitive_tool_names=sensitive,
            multi_reflection_roles=tuple(multi),
            guard_preflight_enabled=gf,
            guard_outbound_enabled=go,
            guard_max_rounds=gmr,
            guard_model=gmodel,
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
        cancel_event: Optional[threading.Event] = None,
    ) -> None:
        self._llm = llm
        self._tools = tools
        self._context = context
        self._trace = trace
        self._mode = mode
        self._config = config or WorkflowConfig.from_env()
        self._intervention = intervention
        self._cancel_event = cancel_event
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
            if self._cancel_event is not None and self._cancel_event.is_set():
                return self._finalize_cancelled()

            if self._turn >= self._config.max_turns:
                return self._finalize_failed(
                    f"已达到 max_turns={self._config.max_turns} 但仍未结束",
                )

            self._turn += 1

            try:
                reply = self._think()
                parsed = self._parse_reply(reply)
                reply, parsed = self._maybe_preflight_guard(reply, parsed)

                acted = self._maybe_act(parsed)

                if self._is_terminal_reply(reply, parsed) and not acted:
                    proceed, reply, parsed = self._maybe_outbound_guard(reply, parsed, acted)
                    if not proceed:
                        continue
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
            self._response_trace_payload(reply, parsed, usage),
            state=self._state,
            turn=self._turn,
            context=self._context,
        )
        self._context.add(ChatMessage(role=MessageRole.ASSISTANT, content=reply))
        return reply

    def _response_trace_payload(
        self,
        reply: str,
        parsed: ParsedOutput,
        usage: Dict[str, Any],
    ) -> Dict[str, Any]:
        """写入轨迹的 response 载荷；附加上下文状态说明便于前端画布展示。"""
        payload: Dict[str, Any] = {
            "content": reply,
            "thoughts": parsed.thoughts,
            "usage": usage,
        }
        msgs = self._context.messages()
        if msgs and msgs[-1].role is MessageRole.SYSTEM:
            if (msgs[-1].content or "").startswith("[出站校验]"):
                payload["session_context_note"] = (
                    "上一条为出站校验：此前「完成」被驳回或校验 JSON 无效，本轮须继续作答。"
                )
            elif (msgs[-1].content or "").startswith("[反馈]"):
                payload["session_context_note"] = (
                    "上一条为系统纠错反馈：解析失败、工具预检失败等之后的重试轮。"
                )
            elif (msgs[-1].content or "").startswith("[上帝指令]"):
                payload["session_context_note"] = "上一条为人类上帝指令，本轮接续执行。"
            elif (msgs[-1].content or "").startswith("[反思器]"):
                payload["session_context_note"] = "上一条为反思器请求修订计划。"

        if not (reply or "").strip():
            ct = usage.get("completion_tokens")
            payload["display_hint"] = (
                "本节点记录的 assistant 正文为空。"
                f" completion_tokens={ct!r}。"
                "常见原因：API 异常、仅空白/不可见 token、上游截断或模型异常短答。"
            )
        return payload

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

    # ------------------- 模型护栏（独立会话） ------------------- #

    def _last_user_goal(self) -> str:
        for m in reversed(self._context.messages()):
            if m.role is not MessageRole.USER:
                continue
            c = m.content
            if c.startswith("[上帝指令]") or c.startswith("[反馈]") or c.startswith("[出站校验]"):
                continue
            return c
        return ""

    def _guard_chat(self, system: str, user: str) -> str:
        messages = [
            ChatMessage(role=MessageRole.SYSTEM, content=system),
            ChatMessage(role=MessageRole.USER, content=user),
        ]
        return self._llm.chat(messages, model=self._config.guard_model)

    def _maybe_preflight_guard(self, reply: str, parsed: ParsedOutput) -> Tuple[str, ParsedOutput]:
        """工具执行前：独立模型会话尝试把 assistant 正文修到可解析且工具名合法。"""
        if not self._config.guard_preflight_enabled or not parsed.tool_calls:
            return reply, parsed

        allowed = {t.name for t in self._tools.list_specs()}
        last_err: Optional[Exception] = None
        for attempt in range(1, self._config.guard_max_rounds + 1):
            user = build_preflight_user(
                assistant_reply=reply,
                mode=self._mode,
                allowed_tool_names=sorted(allowed),
            )
            try:
                fixed = self._guard_chat(PREFLIGHT_SYSTEM, user)
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                logger.warning("guard preflight LLM 调用失败：%s", exc)
                continue
            try:
                new_p = parse_response(fixed, mode=self._mode, require_block=False)
            except CodeFormatError as exc:
                last_err = exc
                continue
            if not new_p.tool_calls:
                last_err = CodeFormatError("预检模型未产出任何 <tool>")
                continue
            unknown = [c.name for c in new_p.tool_calls if c.name not in allowed]
            if unknown:
                last_err = CodeFormatError(f"预检模型产出未知工具：{unknown!r}")
                continue
            if not self._context.replace_last_assistant_content(fixed):
                last_err = CodeFormatError("上下文中找不到 assistant，无法写回预检稿")
                continue
            self._trace.log(
                KIND_GUARD_PREFLIGHT,
                {
                    "ok": True,
                    "attempt": attempt,
                    "tool_summary": summarize_tool_calls(new_p),
                    "repaired_preview": fixed[:800],
                },
                state=self._state,
                turn=self._turn,
                context=self._context,
            )
            return fixed, new_p

        self._trace.log(
            KIND_GUARD_PREFLIGHT,
            {
                "ok": False,
                "attempts": self._config.guard_max_rounds,
                "error": repr(last_err) if last_err else None,
            },
            state=self._state,
            turn=self._turn,
            context=self._context,
        )
        raise CodeFormatError(
            "工具预检在限次内仍无法产出可执行且合规的工具调用",
            details={"last_error": str(last_err) if last_err else None, "snippet": reply[:400]},
        )

    def _maybe_outbound_guard(
        self,
        reply: str,
        parsed: ParsedOutput,
        acted: bool,
    ) -> Tuple[bool, str, ParsedOutput]:
        """在「终止哨兵且本轮未执行工具」时：独立模型判断是否假完成；不通过则注入 SYSTEM 并继续主循环。"""
        if not self._config.guard_outbound_enabled:
            return True, reply, parsed
        if not (self._is_terminal_reply(reply, parsed) and not acted):
            return True, reply, parsed

        user_goal = self._last_user_goal()
        for attempt in range(1, self._config.guard_max_rounds + 1):
            user = build_outbound_user(
                assistant_reply=reply,
                mode=self._mode,
                user_goal=user_goal,
                acted=acted,
            )
            try:
                raw = self._guard_chat(OUTBOUND_SYSTEM, user)
            except Exception as exc:  # noqa: BLE001
                logger.warning("guard outbound LLM 调用失败：%s", exc)
                self._trace.log(
                    KIND_GUARD_OUTBOUND,
                    {"ok": False, "attempt": attempt, "error": str(exc)},
                    state=self._state,
                    turn=self._turn,
                    context=self._context,
                )
                break
            verdict = extract_json_verdict(raw)
            if verdict is None:
                self._trace.log(
                    KIND_GUARD_OUTBOUND,
                    {"ok": False, "attempt": attempt, "error": "no_json_verdict"},
                    state=self._state,
                    turn=self._turn,
                    context=self._context,
                )
                continue

            allow = bool(verdict.get("allow_session_done"))
            replacement = str(verdict.get("assistant_replacement", "") or "").strip()
            feedback = str(verdict.get("feedback", "") or "").strip()

            if replacement:
                if self._context.replace_last_assistant_content(replacement):
                    reply = replacement
                    try:
                        parsed = parse_response(replacement, mode=self._mode, require_block=False)
                    except CodeFormatError as exc:
                        self._context.add(
                            ChatMessage(
                                role=MessageRole.SYSTEM,
                                content=f"[出站校验] 替换稿仍无法解析：{exc}",
                            )
                        )
                        self._trace.log(
                            KIND_GUARD_OUTBOUND,
                            {
                                "ok": False,
                                "attempt": attempt,
                                "error": "replacement_parse_failed",
                            },
                            state=self._state,
                            turn=self._turn,
                            context=self._context,
                        )
                        return False, reply, parsed

            self._trace.log(
                KIND_GUARD_OUTBOUND,
                {
                    "ok": True,
                    "attempt": attempt,
                    "allow_session_done": allow,
                    "feedback": feedback[:500],
                },
                state=self._state,
                turn=self._turn,
                context=self._context,
            )
            if allow:
                return True, reply, parsed

            note = feedback or (
                "出站校验未通过：请继续完成任务，仅在真正完成后使用 [完成] 标记。"
            )
            self._context.add(ChatMessage(role=MessageRole.SYSTEM, content=f"[出站校验]\n{note}"))
            return False, reply, parsed

        self._context.add(
            ChatMessage(
                role=MessageRole.SYSTEM,
                content="[出站校验]\n模型未返回合法 JSON 结论，本轮不允结束；请继续。",
            )
        )
        self._trace.log(
            KIND_GUARD_OUTBOUND,
            {"ok": False, "fallback": True},
            state=self._state,
            turn=self._turn,
            context=self._context,
        )
        return False, reply, parsed

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

    def _finalize_cancelled(self) -> WorkflowResult:
        self._transition(AgentState.CANCELLED)
        return WorkflowResult(
            session_id=self._trace.session_id,
            final_state=self._state,
            turns=self._turn,
            last_message="用户已停止本轮 workflow。",
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
