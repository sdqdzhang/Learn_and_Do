"""State-machine orchestrator.

The Workflow drives one Agent session through the loop

    IDLE -> THINKING -> ACTING -> OBSERVING -> REFLECTING -> ...

and finally lands in ``DONE`` or ``FAILED``. It glues together every
other layer in the codebase:

* ``LLMClient``        -> generates assistant turns
* ``SessionContext``   -> remembers the conversation so far
* ``parse_response``   -> structures the assistant turn
* ``ToolRegistry``     -> dispatches tool calls
* ``Executor``         -> sandbox for code / scripts (via tools)
* ``TraceLogger``      -> records every state change
* ``SystemPromptBuilder`` -> seeds the System message per (role, mode)

Conflict detection and the reflection step both honour the spec's
distinction: any :class:`RetryableError` re-enters THINKING with the
error appended as feedback; any :class:`FatalError` short-circuits to
FAILED with the trace flushed first.
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


# Sentinel substrings the assistant may emit to declare "I'm done".
# Kept conservative — workflow termination is also driven by max_turns
# and the Reflector's structured decision.
_DONE_MARKERS = ("[done]", "<done/>", "<done></done>")


# --------------------------------------------------------------------------- #
# Config
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

    # ------------------- public API ------------------- #

    @property
    def state(self) -> AgentState:
        return self._state

    @property
    def turn(self) -> int:
        return self._turn

    def run(self, user_prompt: str) -> WorkflowResult:
        """Execute the loop until termination. Returns a :class:`WorkflowResult`."""
        try:
            self._seed_context(user_prompt)
        except FatalError as exc:
            return self._finalize_failed(str(exc))

        while True:
            if self._turn >= self._config.max_turns:
                return self._finalize_failed(
                    f"max_turns={self._config.max_turns} reached without DONE",
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
            except Exception as exc:  # noqa: BLE001 -- ensure we never crash the session silently
                logger.exception("workflow unexpected error")
                return self._finalize_failed(f"{type(exc).__name__}: {exc}")

    # ------------------- state-machine steps ------------------- #

    def _seed_context(self, user_prompt: str) -> None:
        # Only seed if the caller hasn't already loaded a System prompt.
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
        # Be permissive at the top level: an assistant turn might be pure
        # narrative ("I'll now plan...") without any structured block.
        # The parser still enforces PHILOSOPHY-mode <thought> presence.
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

        # If any tool failed, surface it as a conflict so REFLECTING runs.
        for result in results:
            if result.status is ToolStatus.FAILED:
                logger.info("tool %s failed; will reflect", result.name)
        return True

    def _reflect(self, parsed: ParsedOutput, acted: bool) -> None:
        self._transition(AgentState.REFLECTING)

        # Look for a Reflection-shaped JSON block from the assistant; if
        # present, use its `next_action`. Otherwise, default behaviour:
        #   - acted=True  -> keep looping
        #   - acted=False -> keep looping (LLM may still need turns)
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
                    content="[reflector] Plan revision requested; reconsider strategy.",
                )
            )
            return
        if decision == "retry":
            # Same plan, next turn.
            return

        # No explicit reflector decision; continue without state change.
        return

    # ------------------- error handling ------------------- #

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
                f"[feedback] Conflict detected: {exc}. "
                "Reconsider the previous claim using the new evidence."
            )
        if isinstance(exc, CodeFormatError):
            return (
                f"[feedback] Your last response could not be parsed ({exc}). "
                "Please re-emit your answer using the structured tags described "
                "in the system prompt."
            )
        suffix = f" details={details}" if details else ""
        return f"[feedback] retryable failure ({kind}): {exc}.{suffix}"

    # ------------------- termination ------------------- #

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

    # ------------------- helpers ------------------- #

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
# Small helpers
# --------------------------------------------------------------------------- #

def _safe_truncate(value, limit: int = 2000):
    """Trim oversized payloads before persisting to the audit log."""
    try:
        text = repr(value)
    except Exception:  # noqa: BLE001
        return "<unrepr-able>"
    if len(text) <= limit:
        return value
    return text[:limit] + f" ...[truncated {len(text) - limit} chars]"


def _format_tool_result_for_llm(result) -> str:
    if result.status is ToolStatus.SUCCESS:
        return f"[tool {result.name} OK]\n{result.output}"
    return f"[tool {result.name} FAILED]\n{result.error}"


__all__ = ["Workflow", "WorkflowConfig"]
