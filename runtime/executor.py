"""Docker 沙箱执行器。

职责：

1. **文件注入。** 把传入的 :class:`FileOperation` 列表落到宿主机的
   ``WORKSPACE_DIR``。
2. **容器管理。** 既可以复用一个长生命周期容器（由 :class:`SessionManager`
   持有），也可以一次性新起 / 删除容器。
3. **资源约束。** 读取环境变量里的 CPU / 内存配额并应用到容器上。
4. **双向同步。** 命令退出后再在容器里跑一遍 ``find``，对比出本轮新生成的
   文件并通过 ``artifacts`` 暴露给调用方。

Executor **任务模式无关**：它不在乎跑的是 pytest 还是爬虫脚本，也不需要
在乎。
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
# 配置
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
# 危险命令启发式黑名单
# --------------------------------------------------------------------------- #

_FORBIDDEN_FRAGMENTS = (
    "rm -rf /",
    "rm -rf /*",
    ":(){:|:&};:",        # fork 炸弹
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
                f"拒绝执行命中黑名单的指令片段：{needle!r}",
                details={"command": list(command)},
            )


# --------------------------------------------------------------------------- #
# Executor
# --------------------------------------------------------------------------- #

class Executor:
    """基于 Docker 的沙箱。

    ``container_name`` 是可选参数。传入时表示 Executor 由一个由
    :class:`SessionManager` 管理的长生命周期容器持有；不传时每次
    :meth:`run` 都会新起一个一次性容器并在结束后删除。
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
        self._client = None  # 懒加载

    # ------------------- 公共 API ------------------- #

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
        """先应用 ``files``，再在沙箱里执行 ``command``。"""
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
        except Exception as exc:  # noqa: BLE001 -- 把未预期错误统一包成 TinyDevinError
            logger.exception("executor 发生未预期错误")
            raise TinyDevinError(
                "executor 发生未预期错误",
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
        # 当 ``container_name`` 由 SessionManager 持有时，由 SessionManager
        # 负责真正的容器清理；一次性容器在 _run_command 内部就已经自删了。
        pass

    def teardown_persistent_container(self) -> None:
        """删除由 ``container_name`` 标识的长生命周期容器（会话结束时调用）。"""
        if not self._container_name:
            return
        name = self._container_name
        try:
            import docker  # type: ignore
        except ImportError:
            logger.debug("teardown_persistent_container：未安装 docker SDK，跳过")
            return

        try:
            client = self._docker_client()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "teardown_persistent_container：无法连接 Docker，容器 %s 可能残留：%s",
                name,
                exc,
            )
            return

        try:
            c = client.containers.get(name)
        except docker.errors.NotFound:  # type: ignore[attr-defined]
            # 常见情况：session 内从未触发过 Executor.run，持久容器尚未创建。
            logger.debug("持久化容器 %s 不存在，跳过删除", name)
            return

        try:
            if getattr(c, "status", None) == "running":
                c.stop(timeout=15)
            c.remove(force=True)
        except docker.errors.NotFound:  # type: ignore[attr-defined]
            logger.debug("持久化容器 %s 已被删除", name)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "删除持久化容器 %s 失败（容器可能仍残留在 Docker 中）：%s",
                name,
                exc,
                exc_info=True,
            )

    # ------------------- 文件注入 ------------------- #

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
                # 注入阶段无需处理 READ；读取由工具层负责。
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
                f"路径越权访问工作空间之外：{rel_path!r}",
                details={"resolved": str(candidate)},
            ) from exc
        return candidate

    # ------------------- 工作空间快照差异 ------------------- #

    def _snapshot_workspace(self) -> set[str]:
        snapshot: set[str] = set()
        for path in self._workspace.rglob("*"):
            if not path.is_file():
                continue
            if any(part.startswith(".") for part in path.relative_to(self._workspace).parts):
                continue
            snapshot.add(str(path.relative_to(self._workspace)).replace("\\", "/"))
        return snapshot

    # ------------------- Docker 胶水代码 ------------------- #

    def _docker_client(self):
        if self._client is not None:
            return self._client
        try:
            import docker  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise ContainerImageError(
                "docker SDK 未安装；请通过 requirements.txt 安装",
            ) from exc

        try:
            self._client = docker.from_env()
            self._client.ping()
        except Exception as exc:  # noqa: BLE001
            raise ContainerImageError(
                "无法连接 docker 守护进程",
                details={"error": str(exc)},
            ) from exc
        return self._client

    def _ensure_image(self) -> None:
        client = self._docker_client()
        try:
            client.images.get(self._config.image)
        except Exception as exc:  # noqa: BLE001 -- 含 docker.errors.ImageNotFound
            raise ContainerImageError(
                f"基础镜像不存在：{self._config.image}",
                details={"error": str(exc)},
            ) from exc

    def _ensure_persistent_container(self, client, host_workspace: str) -> "object":
        """为 ``container_name`` 启动或复用 ``sleep infinity`` 常驻容器。"""
        import docker  # type: ignore

        assert self._container_name is not None
        name = self._container_name
        try:
            c = client.containers.get(name)
            if c.status != "running":
                c.start()
            return c
        except docker.errors.NotFound:  # type: ignore[attr-defined]
            pass
        c = client.containers.run(
            image=self._config.image,
            command=["sleep", "infinity"],
            name=name,
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
        logger.info("已启动持久化沙箱容器：%s", name)
        return c

    def _run_command(
        self,
        command: Sequence[str],
        *,
        extra_pip: List[str],
        timeout: int,
    ) -> tuple[str, str, int, dict]:
        self._ensure_image()
        client = self._docker_client()
        import docker  # type: ignore

        full_cmd = self._compose_shell_command(command, extra_pip=extra_pip)
        run_name = self._container_name or f"{self._config.name_prefix}-{uuid.uuid4().hex[:8]}"
        host_workspace = str(self._workspace)

        # ---------- 会话级持久化容器：docker exec，不在每轮销毁 ----------
        if self._container_name:
            container = self._ensure_persistent_container(client, host_workspace)
            try:
                exit_code, streams = container.exec_run(
                    cmd=["bash", "-lc", full_cmd],
                    workdir="/workspace",
                    demux=True,
                )
                exit_code = int(exit_code)
                if streams is None:
                    stdout_b, stderr_b = b"", b""
                else:
                    stdout_b, stderr_b = streams[0] or b"", streams[1] or b""
                stdout = stdout_b.decode("utf-8", errors="replace")
                stderr = stderr_b.decode("utf-8", errors="replace")
            except Exception as exc:  # noqa: BLE001
                raise ResourceExhausted(
                    "持久化容器内命令执行失败或超时",
                    details={"error": str(exc), "timeout_s": timeout},
                ) from exc
            env_info = {
                "image": self._config.image,
                "container_name": self._container_name,
                "persistent": True,
            }
            return stdout, stderr, exit_code, env_info

        # ---------- 一次性容器（无 container_name）----------
        try:
            container = client.containers.run(
                image=self._config.image,
                command=["bash", "-lc", full_cmd],
                name=run_name,
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
                "启动沙箱容器失败",
                details={"error": str(exc)},
            ) from exc

        try:
            wait_result = container.wait(timeout=timeout)
            exit_code = int(wait_result.get("StatusCode", -1))
            stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
            stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 -- 视为超时 / OOM 这类 Fatal
            try:
                container.kill()
            except Exception:  # noqa: BLE001
                pass
            raise ResourceExhausted(
                "容器超时或被操作系统强制杀掉",
                details={"error": str(exc), "timeout_s": timeout},
            ) from exc
        finally:
            try:
                try:
                    container.reload()
                except docker.errors.NotFound:  # type: ignore[attr-defined]
                    pass
                else:
                    if container.status == "running":
                        container.stop(timeout=15)
                    container.remove(force=True)
            except docker.errors.NotFound:  # type: ignore[attr-defined]
                pass
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "删除一次性容器 %s 失败（容器可能残留在 Docker 中）：%s",
                    run_name,
                    exc,
                    exc_info=True,
                )

        env_info = {"image": self._config.image, "container_name": run_name, "persistent": False}
        return stdout, stderr, exit_code, env_info

    def _compose_shell_command(
        self, command: Sequence[str], *, extra_pip: List[str]
    ) -> str:
        # 对每个参数做引号转义，文件名带空格也能正确传入容器。
        from shlex import quote

        quoted_cmd = " ".join(quote(part) for part in command)
        if extra_pip:
            pip_cmd = "pip install --no-input " + " ".join(quote(p) for p in extra_pip)
            return f"{pip_cmd} && {quoted_cmd}"
        return quoted_cmd


__all__ = ["Executor", "ExecutorConfig"]
