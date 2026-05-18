"""JSON Lines 轨迹日志。

每个 session 写一个文件：``runtime/traces/{session_id}.jsonl``。每一行都是
一条 ``TraceEvent`` 的序列化结果；V1.4+ 在每条事件中嵌入可选的
``context_snapshot`` 与单调 ``runtime_state_id``，以支持时间旅行恢复。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

from core.schema import AgentState, TraceEvent

if TYPE_CHECKING:
    from memory.session_context import SessionContext

logger = logging.getLogger(__name__)


# 已知的事件 kind。kind 字段不强制使用这些常量（允许任意字符串），但定义在
# 这里，方便下游消费者构建稳定的 schema。
KIND_PROMPT = "prompt"
KIND_RESPONSE = "response"
KIND_TOOL_CALL = "tool_call"
KIND_TOOL_RESULT = "tool_result"
KIND_EXEC_RESULT = "exec_result"
KIND_STATE_CHANGE = "state_change"
KIND_ERROR = "error"
KIND_REFLECTION = "reflection"
KIND_PLAN = "plan"
KIND_INTERVENTION_SUSPEND = "intervention_suspend"
KIND_HUMAN_OVERRIDE = "human_override"
KIND_MULTI_REFLECTION = "multi_reflection"
KIND_GUARD_PREFLIGHT = "guard_preflight"
KIND_GUARD_OUTBOUND = "guard_outbound"
KIND_FINAL_SUMMARY = "final_summary"


class TraceLogger:
    """追加式结构化日志写入器；多线程安全。"""

    def __init__(
        self,
        session_id: str,
        log_dir: Optional[str] = None,
        *,
        context: Optional["SessionContext"] = None,
        enable_context_snapshots: bool = True,
        on_event: Optional[Callable[[TraceEvent], None]] = None,
    ) -> None:
        self._session_id = session_id
        directory = Path(log_dir or os.getenv("TRACE_DIR", "./runtime/traces")).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        self._path = directory / f"{session_id}.jsonl"
        self._lock = threading.Lock()
        self._fh = self._path.open("a", encoding="utf-8")
        self._context_ref = context
        self._enable_snapshots = enable_context_snapshots
        self._runtime_seq = 0
        self._on_event = on_event

    def attach_context(self, context: "SessionContext") -> None:
        """在 ``SessionContext`` 创建之后绑定，用于后续自动快照。"""
        self._context_ref = context

    # ------------------- 属性 ------------------- #

    @property
    def path(self) -> Path:
        return self._path

    @property
    def session_id(self) -> str:
        return self._session_id

    # ------------------- 写入 ------------------- #

    def _next_runtime_state_id(self) -> str:
        self._runtime_seq += 1
        return f"rs-{self._runtime_seq:06d}"

    def _maybe_snapshot(self, context: Optional["SessionContext"]) -> Optional[Dict[str, Any]]:
        if not self._enable_snapshots:
            return None
        ctx = context if context is not None else self._context_ref
        if ctx is None:
            return None
        try:
            return ctx.export_snapshot_dict()
        except Exception:  # noqa: BLE001
            logger.exception("导出 SessionContext 快照失败")
            return None

    def log(
        self,
        kind: str,
        payload: Dict[str, Any],
        *,
        state: AgentState,
        turn: int,
        context: Optional["SessionContext"] = None,
    ) -> TraceEvent:
        """写入一条轨迹并返回事件对象（含 ``event_id`` / 快照）。"""
        event = TraceEvent(
            event_id=f"ev-{uuid.uuid4().hex[:12]}",
            runtime_state_id=self._next_runtime_state_id(),
            session_id=self._session_id,
            turn=turn,
            state=state,
            kind=kind,
            payload=payload,
            context_snapshot=self._maybe_snapshot(context),
        )
        line = event.model_dump_json()
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()
        if self._on_event is not None:
            try:
                self._on_event(event)
            except Exception:  # noqa: BLE001
                logger.exception("TraceLogger.on_event 回调失败")
        return event

    def log_event(self, event: TraceEvent) -> None:
        line = event.model_dump_json()
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()

    # ------------------- 生命周期 ------------------- #

    def close(self) -> None:
        with self._lock:
            if self._fh is not None and not self._fh.closed:
                try:
                    self._fh.flush()
                    self._fh.close()
                except Exception:  # noqa: BLE001
                    logger.exception("关闭轨迹文件失败：%s", self._path)

    def __enter__(self) -> "TraceLogger":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - 尽力而为
        try:
            self.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# 读取辅助函数（方便测试与离线回放）
# --------------------------------------------------------------------------- #


def read_trace(path: str) -> list[TraceEvent]:
    """把一个 JSONL 轨迹文件读成 :class:`TraceEvent` 列表。"""
    events: list[TraceEvent] = []
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            data = json.loads(raw)
            events.append(TraceEvent.model_validate(data))
    return events


__all__ = [
    "TraceLogger",
    "read_trace",
    "KIND_PROMPT",
    "KIND_RESPONSE",
    "KIND_TOOL_CALL",
    "KIND_TOOL_RESULT",
    "KIND_EXEC_RESULT",
    "KIND_STATE_CHANGE",
    "KIND_ERROR",
    "KIND_REFLECTION",
    "KIND_PLAN",
    "KIND_INTERVENTION_SUSPEND",
    "KIND_HUMAN_OVERRIDE",
    "KIND_MULTI_REFLECTION",
    "KIND_GUARD_PREFLIGHT",
    "KIND_GUARD_OUTBOUND",
    "KIND_FINAL_SUMMARY",
]
