"""JSON Lines 轨迹日志。

每个 session 写一个文件：``runtime/traces/{session_id}.jsonl``。每一行都是
一条 ``TraceEvent`` 的 JSON 序列化结果。格式刻意保持简单 —— 扁平、追加、
按行分隔 —— 这样下游工具（jq、pandas、BI 面板）可以零定制地消费。

为什么不直接用标准库 ``logging``？因为轨迹事件天生是结构化的（状态机切换、
prompt、工具输入输出），如果硬塞进自由文本日志，每次读取都得再做一遍反向
解析。我们把它们保留为结构化数据。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from core.schema import AgentState, TraceEvent

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


class TraceLogger:
    """追加式结构化日志写入器；多线程安全。"""

    def __init__(
        self,
        session_id: str,
        log_dir: Optional[str] = None,
    ) -> None:
        self._session_id = session_id
        directory = Path(log_dir or os.getenv("TRACE_DIR", "./runtime/traces")).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        self._path = directory / f"{session_id}.jsonl"
        self._lock = threading.Lock()
        self._fh = self._path.open("a", encoding="utf-8")

    # ------------------- 属性 ------------------- #

    @property
    def path(self) -> Path:
        return self._path

    @property
    def session_id(self) -> str:
        return self._session_id

    # ------------------- 写入 ------------------- #

    def log(
        self,
        kind: str,
        payload: Dict[str, Any],
        *,
        state: AgentState,
        turn: int,
    ) -> None:
        event = TraceEvent(
            session_id=self._session_id,
            turn=turn,
            state=state,
            kind=kind,
            payload=payload,
        )
        line = event.model_dump_json()
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()

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
]
