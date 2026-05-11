"""Docker sandbox executor.

Responsibilities:

1. **File injection.** Materialise the list of :class:`FileOperation` on
   the host inside ``WORKSPACE_DIR``.
2. **Container management.** Either reuse a long-lived container (when a
   :class:`SessionManager` owns one) or spin up a one-shot container.
3. **Resource control.** Apply CPU / memory limits read from env.
4. **Bidirectional sync.** After the command exits, run a ``find`` inside
   the container to discover the resulting file tree, then expose any
   new artifacts to the caller.

The Executor is **mode-agnostic**: it doesn't know whether the script is
a pytest run or a scraping job, and it doesn't need to.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

from core.exceptions import (
    ContainerImageError,
    ResourceExhausted,
    SandboxViolation,
    TinyDevinError,
)
from core.schema import ExecutionResult, FileAction, FileOperation

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ExecutorConfig:
    image: str
    name_prefix: str
    workspace_dir: str
    memory_limit: str
    cpu_limit: float
    default_timeout: int

    @classmethod
    def from_env(cls) -> "ExecutorConfig":
        return cls(
            image=os.getenv("DOCKER_BASE_IMAGE", "tiny-devin-base:latest"),
            name_prefix=os.getenv("CONTAINER_NAME_PREFIX", "tiny-devin"),
            workspace_dir=os.getenv("WORKSPACE_DIR", "./runtime/workspace"),
            memory_limit=os.getenv("CONTAINER_MEMORY_LIMIT", "2g"),
            cpu_limit=float(os.getenv("CONTAINER_CPU_LIMIT", "2.0")),
            default_timeout=int(os.getenv("EXECUTOR_DEFAULT_TIMEOUT", "60")),
        )


# --------------------------------------------------------------------------- #
# Forbidden command heuristics
# --------------------------------------------------------------------------- #

_FORBIDDEN_FRAGMENTS = (
    "rm -rf /",
    "rm -rf /*",
    ":(){:|:&};:",        # fork bomb
    "mkfs",
    "dd if=",
    "shutdown",
    "reboot",
)


def _ensure_command_safe(command: Sequence[str]) -> None:
    flat = " ".join(command).lower()
    for needle in _FORBIDDEN_FRAGMENTS:
        if needle in flat:
            raise SandboxViolation(
                f"refused to run forbidden command fragment: {needle!r}",
                details={"command": list(command)},
            )


# --------------------------------------------------------------------------- #
# Executor
# --------------------------------------------------------------------------- #

class Executor:
    """Docker-backed sandbox.

    ``container_name`` is optional. When provided, the Executor expects a
    long-lived container managed by :class:`SessionManager`. When omitted,
    each :meth:`run` call spins up a fresh container and removes it
    afterwards.
    """

    def __init__(
        self,
        config: Optional[ExecutorConfig] = None,
        *,
        container_name: Optional[str] = None,
    ) -> None:
        self._config = config or ExecutorConfig.from_env()
        self._workspace = Path(self._config.workspace_dir).resolve()
        self._workspace.mkdir(parents=True, exist_ok=True)
        self._container_name = container_name
        self._client = None  # lazy

    # ------------------- public ------------------- #

    @property
    def config(self) -> ExecutorConfig:
        return self._config

    @property
    def workspace_path(self) -> Path:
        return self._workspace

    def run(
        self,
        files: Optional[List[FileOperation]] = None,
        command: Optional[Sequence[str]] = None,
        *,
        extra_pip: Optional[Sequence[str]] = None,
        timeout: Optional[int] = None,
    ) -> ExecutionResult:
        """Apply ``files`` then run ``command`` inside the sandbox."""
        files = files or []
        command = list(command or ["python", "--version"])
        timeout = timeout or self._config.default_timeout
        _ensure_command_safe(command)

        self._apply_files(files)

        before_snapshot = self._snapshot_workspace()
        started = time.perf_counter()

        try:
            stdout, stderr, exit_code, env_info = self._run_command(
                command,
                extra_pip=list(extra_pip or []),
                timeout=timeout,
            )
        except ContainerImageError:
            raise
        except ResourceExhausted:
            raise
        except SandboxViolation:
            raise
        except TinyDevinError:
            raise
        except Exception as exc:  # noqa: BLE001 -- wrap unexpected errors
            logger.exception("executor unexpected failure")
            raise TinyDevinError(
                "executor unexpected failure",
                details={"error": str(exc)},
            ) from exc

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        after_snapshot = self._snapshot_workspace()
        artifacts = sorted(after_snapshot - before_snapshot)

        return ExecutionResult(
            is_success=(exit_code == 0),
            stdout=stdout,
            stderr=stderr,
            active_files=sorted(after_snapshot),
            metrics={"elapsed_ms": elapsed_ms, "exit_code": exit_code},
            metadata=env_info,
            artifacts=artifacts,
        )

    def close(self) -> None:
        # Owned by SessionManager when ``container_name`` is set; otherwise
        # one-shot runs already clean themselves up.
        pass

    # ------------------- file injection ------------------- #

    def _apply_files(self, files: List[FileOperation]) -> None:
        for op in files:
            target = self._resolve_inside_workspace(op.file_path)
            if op.action is FileAction.DELETE:
                if target.exists():
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                continue
            if op.action is FileAction.READ:
                # READ is a no-op on injection; the tool layer handles reads.
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            content = op.content or ""
            target.write_text(content, encoding="utf-8")

    def _resolve_inside_workspace(self, rel_path: str) -> Path:
        candidate = (self._workspace / rel_path).resolve()
        try:
            candidate.relative_to(self._workspace)
        except ValueError as exc:
            raise SandboxViolation(
                f"file path escapes workspace: {rel_path!r}",
                details={"resolved": str(candidate)},
            ) from exc
        return candidate

    # ------------------- workspace diffing ------------------- #

    def _snapshot_workspace(self) -> set[str]:
        snapshot: set[str] = set()
        for path in self._workspace.rglob("*"):
            if not path.is_file():
                continue
            if any(part.startswith(".") for part in path.relative_to(self._workspace).parts):
                continue
            snapshot.add(str(path.relative_to(self._workspace)).replace("\\", "/"))
        return snapshot

    # ------------------- docker glue ------------------- #

    def _docker_client(self):
        if self._client is not None:
            return self._client
        try:
            import docker  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise ContainerImageError(
                "docker SDK not installed; install via requirements.txt",
            ) from exc

        try:
            self._client = docker.from_env()
            self._client.ping()
        except Exception as exc:  # noqa: BLE001
            raise ContainerImageError(
                "cannot reach docker daemon",
                details={"error": str(exc)},
            ) from exc
        return self._client

    def _ensure_image(self) -> None:
        client = self._docker_client()
        try:
            client.images.get(self._config.image)
        except Exception as exc:  # noqa: BLE001 -- includes docker.errors.ImageNotFound
            raise ContainerImageError(
                f"base image not found: {self._config.image}",
                details={"error": str(exc)},
            ) from exc

    def _run_command(
        self,
        command: Sequence[str],
        *,
        extra_pip: List[str],
        timeout: int,
    ) -> tuple[str, str, int, dict]:
        self._ensure_image()
        client = self._docker_client()

        full_cmd = self._compose_shell_command(command, extra_pip=extra_pip)
        run_name = self._container_name or f"{self._config.name_prefix}-{uuid.uuid4().hex[:8]}"
        host_workspace = str(self._workspace)

        try:
            container = client.containers.run(
                image=self._config.image,
                command=["bash", "-lc", full_cmd],
                name=run_name if not self._container_name else None,
                volumes={host_workspace: {"bind": "/workspace", "mode": "rw"}},
                working_dir="/workspace",
                mem_limit=self._config.memory_limit,
                nano_cpus=int(self._config.cpu_limit * 1e9),
                network_mode=os.getenv("CONTAINER_NETWORK_MODE", "bridge"),
                detach=True,
                stdout=True,
                stderr=True,
                remove=False,
            )
        except Exception as exc:  # noqa: BLE001
            raise ContainerImageError(
                "failed to launch sandbox container",
                details={"error": str(exc)},
            ) from exc

        try:
            wait_result = container.wait(timeout=timeout)
            exit_code = int(wait_result.get("StatusCode", -1))
            stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
            stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 -- treat as fatal timeout / oom
            try:
                container.kill()
            except Exception:  # noqa: BLE001
                pass
            raise ResourceExhausted(
                "container exceeded timeout or was killed by OS",
                details={"error": str(exc), "timeout_s": timeout},
            ) from exc
        finally:
            try:
                container.remove(force=True)
            except Exception:  # noqa: BLE001
                logger.warning("failed to remove container %s", run_name)

        env_info = {"image": self._config.image, "container_name": run_name}
        return stdout, stderr, exit_code, env_info

    def _compose_shell_command(
        self, command: Sequence[str], *, extra_pip: List[str]
    ) -> str:
        # Quote individual args so a user file with spaces still works.
        from shlex import quote

        quoted_cmd = " ".join(quote(part) for part in command)
        if extra_pip:
            pip_cmd = "pip install --no-input " + " ".join(quote(p) for p in extra_pip)
            return f"{pip_cmd} && {quoted_cmd}"
        return quoted_cmd


__all__ = ["Executor", "ExecutorConfig"]
