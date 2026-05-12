"""人类干预通道（上帝模式）。

与具体传输层（WebSocket / HTTP）解耦：服务端在收到人类指令后调用
:meth:`InterventionChannel.submit`；编排层在 ``INTERVENTION`` 状态或
挂起点调用 :meth:`InterventionChannel.wait` 取走消息。
"""

from __future__ import annotations

import threading
from typing import Optional


class InterventionChannel:
    """线程安全的单槽人类消息队列。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._pending: Optional[str] = None

    def submit(self, text: str) -> None:
        """由 API / WebSocket 处理器调用，写入一条待消费的人类指令。"""
        payload = (text or "").strip()
        if not payload:
            return
        with self._cond:
            self._pending = payload
            self._cond.notify_all()

    def wait(self, timeout: Optional[float]) -> Optional[str]:
        """阻塞直到收到一条消息或超时。

        - ``timeout is None``：无限等待（仅建议服务端使用）。
        - ``timeout <= 0``：非阻塞，仅当已有消息时取出。
        """
        with self._cond:
            if self._pending is not None:
                msg = self._pending
                self._pending = None
                return msg
            if timeout is not None and timeout <= 0:
                return None
            ok = self._cond.wait(timeout=timeout)
            if not ok:
                return None
            msg = self._pending
            self._pending = None
            return msg

    def peek(self) -> Optional[str]:
        """不取出，仅查看是否有积压（调试用）。"""
        with self._lock:
            return self._pending


__all__ = ["InterventionChannel"]
