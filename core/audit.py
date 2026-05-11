"""JSON Lines trace logger.

One file per session at ``runtime/traces/{session_id}.jsonl``. Each line
is a :class:`TraceEvent` serialized to JSON. The format is intentionally
simple — flat, append-only, line-delimited — so external tools (jq,
pandas, BI dashboards) can consume it without any custom parser.

Why not just stdlib ``logging``? Trace events are *structured by
definition* (state machine transitions, prompts, tool I/O); shoving them
into a free-form log message would require parsing the message back out
on every read. Keep them structured.
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


# Known kinds. Not enforced (free strings allowed) but documented here so
# downstream consumers can build a stable schema.
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
    """Append-only structured logger; safe to use from multiple threads."""

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

    # ------------------- properties ------------------- #

    @property
    def path(self) -> Path:
        return self._path

    @property
    def session_id(self) -> str:
        return self._session_id

    # ------------------- write ------------------- #

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

    # ------------------- lifecycle ------------------- #

    def close(self) -> None:
        with self._lock:
            if self._fh is not None and not self._fh.closed:
                try:
                    self._fh.flush()
                    self._fh.close()
                except Exception:  # noqa: BLE001
                    logger.exception("failed to close trace file %s", self._path)

    def __enter__(self) -> "TraceLogger":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - best effort
        try:
            self.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Reader helper (handy for tests and offline replay)
# --------------------------------------------------------------------------- #

def read_trace(path: str) -> list[TraceEvent]:
    """Load a JSONL trace file into a list of :class:`TraceEvent`."""
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
