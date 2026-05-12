"""StreamTunnel：把持久化沙箱容器的交互式 TTY 暴露到 WebSocket 的适配骨架。

完整实现依赖具体 ASGI 栈（FastAPI / aiohttp）与 ``docker attach`` 或
``docker exec`` PTY 模式。此处提供：

* 会话级 ``session_id -> container_name`` 映射约定；
* 供上层服务注册/注销的薄接口。

接入方应在启动 WebSocket 时调用 :meth:`StreamTunnel.register`，在连接
关闭时 :meth:`StreamTunnel.unregister`。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class TunnelEndpoint:
    session_id: str
    container_name: str


class StreamTunnel:
    """全局（进程内）可查询的 TTY 隧道登记簿 —— 供 WebSocket 层查询。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._routes: Dict[str, TunnelEndpoint] = {}

    def register(self, session_id: str, container_name: str) -> None:
        with self._lock:
            self._routes[session_id] = TunnelEndpoint(
                session_id=session_id,
                container_name=container_name,
            )

    def unregister(self, session_id: str) -> None:
        with self._lock:
            self._routes.pop(session_id, None)

    def lookup(self, session_id: str) -> Optional[TunnelEndpoint]:
        with self._lock:
            return self._routes.get(session_id)


default_tunnel = StreamTunnel()

__all__ = ["StreamTunnel", "TunnelEndpoint", "default_tunnel"]
