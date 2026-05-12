"""会话生命周期管理。

一个 session = 一次 Agent 运行 = 一个沙箱容器 + 一个工作空间 + 一份轨迹日志。
MVP 阶段采用 **单容器单会话** 策略：每次新建 session 起一个新容器，session
内的所有轮次共用这个容器。

管理器还会跑一个轻量心跳线程，超过配置的空闲窗口后强制停止容器，对应规范
里 "10 分钟心跳 / 1 小时空闲超时" 的策略。
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from core.exceptions import ConfigurationError
from runtime.executor import Executor, ExecutorConfig

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class SessionConfig:
    name_prefix: str
    idle_timeout_s: int
    heartbeat_interval_s: int

    @classmethod
    def from_env(cls) -> "SessionConfig":
        return cls(
            name_prefix=os.getenv("CONTAINER_NAME_PREFIX", "tiny-devin"),
            idle_timeout_s=int(os.getenv("CONTAINER_IDLE_TIMEOUT_SECONDS", "3600")),
            heartbeat_interval_s=int(
                os.getenv("CONTAINER_HEARTBEAT_INTERVAL_SECONDS", "600")
            ),
        )


# --------------------------------------------------------------------------- #
# SessionManager
# --------------------------------------------------------------------------- #

class SessionManager:
    """持有容器生命周期，并对外暴露一个 :class:`Executor`。"""

    def __init__(
        self,
        config: Optional[SessionConfig] = None,
        executor_config: Optional[ExecutorConfig] = None,
    ) -> None:
        self._config = config or SessionConfig.from_env()
        self._executor_config = executor_config or ExecutorConfig.from_env()
        self._executor: Optional[Executor] = None
        self._session_id: Optional[str] = None
        self._last_activity: float = time.time()
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()
        self._lock = threading.Lock()

    # ------------------- 属性 ------------------- #

    @property
    def session_id(self) -> str:
        if self._session_id is None:
            raise ConfigurationError("session 尚未启动")
        return self._session_id

    @property
    def executor(self) -> Executor:
        if self._executor is None:
            raise ConfigurationError("session 尚未启动")
        return self._executor

    @property
    def is_active(self) -> bool:
        return self._executor is not None

    # ------------------- 生命周期 ------------------- #

    def start(self) -> str:
        """启动一个新 session，返回 session_id。"""
        with self._lock:
            if self._executor is not None:
                # 已经启动过；保证幂等性。
                return self._session_id  # type: ignore[return-value]

            self._session_id = f"{self._config.name_prefix}-{uuid.uuid4().hex[:8]}"
            self._executor = Executor(
                self._executor_config,
                container_name=self._session_id,
            )
            self._last_activity = time.time()
            self._stop_flag.clear()
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                name=f"{self._session_id}-heartbeat",
                daemon=True,
            )
            self._heartbeat_thread.start()
            logger.info("session 已启动：%s", self._session_id)
            return self._session_id

    def heartbeat(self) -> None:
        """标记 session 仍然活跃；workflow 每轮调用一次。"""
        self._last_activity = time.time()

    def stop(self) -> None:
        with self._lock:
            self._stop_flag.set()
            if self._executor is not None:
                try:
                    self._executor.teardown_persistent_container()
                except Exception:  # noqa: BLE001
                    logger.exception("清理持久化容器失败")
                try:
                    self._executor.close()
                except Exception:  # noqa: BLE001
                    logger.exception("executor 关闭失败")
                self._executor = None
            if self._heartbeat_thread is not None:
                self._heartbeat_thread = None
            if self._session_id is not None:
                logger.info("session 已停止：%s", self._session_id)
            self._session_id = None

    # ------------------- 上下文管理器 ------------------- #

    def __enter__(self) -> "SessionManager":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()

    # ------------------- 内部实现 ------------------- #

    def _heartbeat_loop(self) -> None:
        interval = max(1, self._config.heartbeat_interval_s)
        timeout = self._config.idle_timeout_s
        while not self._stop_flag.wait(interval):
            idle = time.time() - self._last_activity
            if idle >= timeout:
                logger.warning(
                    "session %s 空闲已达 %.0fs（上限 %ds），自动停止",
                    self._session_id,
                    idle,
                    timeout,
                )
                self.stop()
                return


__all__ = ["SessionManager", "SessionConfig"]
