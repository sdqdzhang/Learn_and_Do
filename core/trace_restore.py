"""按轨迹事件恢复 SessionContext（时间旅行 API 核心逻辑）。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from core.audit import read_trace
from core.schema import TraceEvent
from memory.session_context import ContextConfig, SessionContext


def find_event_by_id(events: list[TraceEvent], event_id: str) -> Optional[TraceEvent]:
    """在已解析事件列表中按 ``event_id`` 查找。"""
    for ev in events:
        if ev.event_id == event_id:
            return ev
    return None


def restore_context_from_event(event: TraceEvent) -> SessionContext:
    """根据单条事件内嵌的快照恢复一个新的 :class:`SessionContext`。

    若该事件不含 ``context_snapshot``，则返回空上下文。
    """
    ctx = SessionContext(config=ContextConfig.from_env())
    snap = event.context_snapshot
    if snap:
        ctx.replace_from_snapshot(snap)
    return ctx


def restore_context_from_trace_file(
    path: Union[str, Path],
    event_id: str,
) -> SessionContext:
    """读取 JSONL 轨迹文件，定位 ``event_id`` 对应行并恢复上下文。"""
    events = read_trace(str(path))
    ev = find_event_by_id(events, event_id)
    if ev is None:
        raise KeyError(f"未找到 event_id={event_id!r}：{path}")
    return restore_context_from_event(ev)


__all__ = [
    "find_event_by_id",
    "restore_context_from_event",
    "restore_context_from_trace_file",
]
