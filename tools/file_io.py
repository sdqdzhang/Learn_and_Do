"""File I/O tools confined to the workspace directory.

All three tools (read / write / list) refuse any path that escapes
``WORKSPACE_DIR``. This is the *Agent-facing* file API; the Executor has
its own host-side path handling and is independent of these tools.
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
    """Resolve ``rel_path`` against the workspace, blocking escapes."""
    root = _workspace_root()
    root.mkdir(parents=True, exist_ok=True)
    candidate = (root / rel_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise SandboxViolation(
            f"path escapes workspace: {rel_path!r}",
            details={"resolved": str(candidate)},
        ) from exc
    return candidate


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #

class FileReadTool(Tool):
    spec = ToolSpec(
        name="file_read",
        description="Read a UTF-8 text file from the workspace and return its contents.",
        args_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative path"},
                "max_bytes": {
                    "type": "integer",
                    "description": "Optional cap; oversized files are truncated.",
                },
            },
            "required": ["path"],
        },
    )

    def call(self, args: Dict[str, Any]) -> Dict[str, Any]:
        target = _resolve_safe(args["path"])
        if not target.exists():
            raise ToolError(f"file not found: {args['path']}")
        if not target.is_file():
            raise ToolError(f"not a regular file: {args['path']}")

        max_bytes: Optional[int] = args.get("max_bytes")
        try:
            raw = target.read_bytes()
        except OSError as exc:
            raise ToolError(f"read failed: {exc}") from exc

        truncated = False
        if max_bytes is not None and len(raw) > max_bytes:
            raw = raw[:max_bytes]
            truncated = True

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError(f"file is not valid UTF-8: {exc}") from exc

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
            "Create or overwrite a UTF-8 text file inside the workspace. "
            "Parent directories are created as needed."
        ),
        args_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
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
            raise ToolError(f"write failed: {exc}") from exc
        return {"path": args["path"], "bytes_written": target.stat().st_size}


class FileListTool(Tool):
    spec = ToolSpec(
        name="file_list",
        description="List files and directories inside a workspace folder (one level deep).",
        args_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Workspace-relative directory; defaults to root.",
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
            raise ToolError(f"not a directory: {rel}")

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
