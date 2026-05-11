"""Python REPL 工具。

通过 :class:`runtime.executor.Executor` 在 Tiny-Devin 沙箱里执行一段 Python
代码。让代码跑在 Docker 里是不可妥协的底线：Agent 输出的代码默认就是不
可信的。

工具本身很薄 —— 把代码片段包装成 ``main.py`` 这一份 FileOperation 提交给
Executor，再把 ``ExecutionResult`` 转成对工具友好的 dict。
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
            "在沙箱容器内执行一段 Python 代码片段，"
            "返回 stdout / stderr 以及运行后工作空间里的文件列表。"
        ),
        args_schema={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "要执行的 Python 代码片段"},
                "filename": {
                    "type": "string",
                    "description": "把代码片段写入的工作空间路径，默认 'main.py'。",
                },
                "timeout": {
                    "type": "integer",
                    "description": "墙钟超时秒数，未填则使用执行器的默认值。",
                },
                "extra_pip": {
                    "type": "array",
                    "description": "运行前需要额外安装的 pip 包列表。",
                },
            },
            "required": ["code"],
        },
    )

    def __init__(self, executor: Optional[Any] = None) -> None:
        """``executor`` 用鸭子类型而不是强引用 :mod:`runtime.executor`，便于
        单测里塞一个 stub executor。
        """
        self._executor = executor

    def bind_executor(self, executor: Any) -> None:
        self._executor = executor

    def call(self, args: Dict[str, Any]) -> Dict[str, Any]:
        if self._executor is None:
            raise ToolError(
                "python_repl 工具未绑定 executor；"
                "请先调用 .bind_executor(executor) 再使用",
            )

        filename = args.get("filename", "main.py")
        code = args["code"]
        if not code.strip():
            raise ToolError("code 不能为空")

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
