"""Python REPL tool.

Executes a snippet of Python code inside the Tiny-Devin sandbox via
:class:`runtime.executor.Executor`. Keeping execution inside Docker is
non-negotiable: Agent-emitted code is untrusted by definition.

The tool is *thin* — it shapes the snippet into a single ``main.py``
file operation, dispatches it, and converts ``ExecutionResult`` into a
tool-friendly dict.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.exceptions import ToolError
from core.schema import FileOperation, ToolSpec
from tools.base import Tool


class PythonReplTool(Tool):
    spec = ToolSpec(
        name="python_repl",
        description=(
            "Execute a Python snippet inside the sandbox container. "
            "Returns stdout, stderr and resulting workspace files."
        ),
        args_schema={
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "filename": {
                    "type": "string",
                    "description": "Workspace path to write the snippet to (default 'main.py').",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Wall-clock timeout seconds (default: executor's default).",
                },
                "extra_pip": {
                    "type": "array",
                    "description": "Extra pip packages to install before execution.",
                },
            },
            "required": ["code"],
        },
    )

    def __init__(self, executor: Optional[Any] = None) -> None:
        """``executor`` is duck-typed to keep this module decoupled from
        :mod:`runtime.executor` (handy for tests with a stub executor).
        """
        self._executor = executor

    def bind_executor(self, executor: Any) -> None:
        self._executor = executor

    def call(self, args: Dict[str, Any]) -> Dict[str, Any]:
        if self._executor is None:
            raise ToolError(
                "python_repl tool has no executor bound; "
                "call .bind_executor(executor) before use",
            )

        filename = args.get("filename", "main.py")
        code = args["code"]
        if not code.strip():
            raise ToolError("code is empty")

        files = [FileOperation(file_path=filename, content=code)]
        timeout = args.get("timeout")
        extra_pip = args.get("extra_pip") or []

        result = self._executor.run(
            files=files,
            command=["python", filename],
            extra_pip=extra_pip,
            timeout=timeout,
        )

        return {
            "is_success": result.is_success,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "active_files": result.active_files,
            "artifacts": result.artifacts,
            "metrics": result.metrics,
        }


__all__ = ["PythonReplTool"]
