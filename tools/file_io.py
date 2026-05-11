"""文件 IO 工具，所有读写都被限制在工作空间目录内。

读 / 写 / 列表三个工具都会拒绝任何越过 ``WORKSPACE_DIR`` 的路径。这一组
是 *Agent 视角* 的文件 API；Executor 自己另有一套宿主机侧的路径处理，
与这里的工具是独立的两层防御。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.exceptions import SandboxViolation, ToolError
from core.schema import ToolSpec
from tools.base import Tool


def _workspace_root() -> Path:
    root = os.getenv("WORKSPACE_DIR", "./runtime/workspace")
    return Path(root).resolve()


def _resolve_safe(rel_path: str) -> Path:
    """把 ``rel_path`` 相对工作空间做路径拼接，并拒绝越权访问。"""
    root = _workspace_root()
    root.mkdir(parents=True, exist_ok=True)
    candidate = (root / rel_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise SandboxViolation(
            f"路径越权访问工作空间之外：{rel_path!r}",
            details={"resolved": str(candidate)},
        ) from exc
    return candidate


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #

class FileReadTool(Tool):
    spec = ToolSpec(
        name="file_read",
        description="从工作空间读取一个 UTF-8 文本文件，返回其内容。",
        args_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "工作空间内的相对路径"},
                "max_bytes": {
                    "type": "integer",
                    "description": "可选字节上限；超过则截断。",
                },
            },
            "required": ["path"],
        },
    )

    def call(self, args: Dict[str, Any]) -> Dict[str, Any]:
        target = _resolve_safe(args["path"])
        if not target.exists():
            raise ToolError(f"文件不存在：{args['path']}")
        if not target.is_file():
            raise ToolError(f"目标不是常规文件：{args['path']}")

        max_bytes: Optional[int] = args.get("max_bytes")
        try:
            raw = target.read_bytes()
        except OSError as exc:
            raise ToolError(f"读取失败：{exc}") from exc

        truncated = False
        if max_bytes is not None and len(raw) > max_bytes:
            raw = raw[:max_bytes]
            truncated = True

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError(f"文件不是有效的 UTF-8 编码：{exc}") from exc

        return {
            "path": args["path"],
            "content": text,
            "size_bytes": target.stat().st_size,
            "truncated": truncated,
        }


class FileWriteTool(Tool):
    spec = ToolSpec(
        name="file_write",
        description=(
            "在工作空间内创建或覆盖一个 UTF-8 文本文件，"
            "必要时会自动创建父目录。"
        ),
        args_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "工作空间内的相对路径"},
                "content": {"type": "string", "description": "要写入的完整文本"},
            },
            "required": ["path", "content"],
        },
    )

    def call(self, args: Dict[str, Any]) -> Dict[str, Any]:
        target = _resolve_safe(args["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_text(args["content"], encoding="utf-8")
        except OSError as exc:
            raise ToolError(f"写入失败：{exc}") from exc
        return {"path": args["path"], "bytes_written": target.stat().st_size}


class FileListTool(Tool):
    spec = ToolSpec(
        name="file_list",
        description="列出工作空间内某个目录下的文件与子目录（仅一层深度）。",
        args_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "工作空间内的相对目录；留空则视为根目录。",
                },
            },
        },
    )

    def call(self, args: Dict[str, Any]) -> Dict[str, Any]:
        rel = args.get("path", "")
        target = _resolve_safe(rel) if rel else _workspace_root()
        if not target.exists():
            return {"path": rel, "entries": []}
        if not target.is_dir():
            raise ToolError(f"目标不是目录：{rel}")

        entries: List[Dict[str, Any]] = []
        for child in sorted(target.iterdir()):
            entries.append(
                {
                    "name": child.name,
                    "type": "dir" if child.is_dir() else "file",
                    "size_bytes": child.stat().st_size if child.is_file() else None,
                }
            )
        return {"path": rel or ".", "entries": entries}


__all__ = ["FileReadTool", "FileWriteTool", "FileListTool"]
