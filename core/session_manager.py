"""Session lifecycle.

A session = one Agent run = one sandbox container + one workspace +
one trace log. For the MVP we use **single-container-single-session**:
each new session creates a fresh container, reused across all turns
within that session.

The manager also runs a lightweight heartbeat thread that force-stops
the container after a configurable idle window, matching the spec's
"10-minute heartbeat / 1-hour idle timeout" policy.
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
# Config
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
    """Owns the container's lifetime and exposes an :class:`Executor`."""

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

    # ------------------- properties ------------------- #

    @property
    def session_id(self) -> str:
        if self._session_id is None:
            raise ConfigurationError("session has not been started yet")
        return self._session_id

    @property
    def executor(self) -> Executor:
        if self._executor is None:
            raise ConfigurationError("session has not been started yet")
        return self._executor

    @property
    def is_active(self) -> bool:
        return self._executor is not None

    # ------------------- lifecycle ------------------- #

    def start(self) -> str:
        """Begin a new session and return its id."""
        with self._lock:
            if self._executor is not None:
                # Already started; idempotent.
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
            logger.info("session started: %s", self._session_id)
            return self._session_id

    def heartbeat(self) -> None:
        """Mark the session as active; called by the workflow on every turn."""
        self._last_activity = time.time()

    def stop(self) -> None:
        with self._lock:
            self._stop_flag.set()
            if self._executor is not None:
                try:
                    self._executor.close()
                except Exception:  # noqa: BLE001
                    logger.exception("executor close failed")
                self._executor = None
            if self._heartbeat_thread is not None:
                self._heartbeat_thread = None
            if self._session_id is not None:
                logger.info("session stopped: %s", self._session_id)
            self._session_id = None

    # ------------------- context manager ------------------- #

    def __enter__(self) -> "SessionManager":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()

    # ------------------- internals ------------------- #

    def _heartbeat_loop(self) -> None:
        interval = max(1, self._config.heartbeat_interval_s)
        timeout = self._config.idle_timeout_s
        while not self._stop_flag.wait(interval):
            idle = time.time() - self._last_activity
            if idle >= timeout:
                logger.warning(
                    "session %s idle for %.0fs (limit %ds) -> auto-stopping",
                    self._session_id,
                    idle,
                    timeout,
                )
                self.stop()
                return


__all__ = ["SessionManager", "SessionConfig"]
