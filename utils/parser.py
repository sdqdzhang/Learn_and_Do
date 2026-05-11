"""Strict response parser.

Extracts structured blocks from LLM output. Four block kinds are
recognised; they may freely interleave and any subset may be present:

1. ``<file path="...">...</file>``  -> ``FileOperation``
2. ``<thought>...</thought>``       -> string
3. ``<tool name="...">JSON</tool>`` -> ``ToolCall`` (args parsed as JSON)
4. fenced ```` ```json ... ``` ```` -> dict (Plan / Reflection / etc.)

The legacy ``# file: <path>`` form (V1.1) is still accepted as a fallback
for the ``<file>`` block.

This module is mode-aware **only at the validation step**:
:func:`parse_response` accepts a ``mode`` kwarg and enforces that
PHILOSOPHY assistants produce at least one ``<thought>`` block.
"""

from __future__ import annotations

import json
import re
from typing import List, Optional

from core.exceptions import CodeFormatError, MissingPathError
from core.schema import (
    FileAction,
    FileOperation,
    ParsedOutput,
    TaskMode,
    ToolCall,
)


# --------------------------------------------------------------------------- #
# Compiled regexes (module-level so each parse_response call is cheap)
# --------------------------------------------------------------------------- #

_FILE_TAG_RE = re.compile(
    r'<file\s+path\s*=\s*"([^"]+)"(?:\s+action\s*=\s*"([^"]+)")?\s*>'
    r"([\s\S]*?)</file>",
    re.IGNORECASE,
)

# Legacy fallback:  ```python\n# file: path\n<code>\n```
_LEGACY_FILE_RE = re.compile(
    r"```[a-zA-Z0-9_+\-]*\s*\n\s*#\s*file:\s*([^\n]+)\n([\s\S]*?)\n```",
    re.IGNORECASE,
)

_THOUGHT_TAG_RE = re.compile(
    r"<thought>([\s\S]*?)</thought>", re.IGNORECASE
)

_TOOL_TAG_RE = re.compile(
    r'<tool\s+name\s*=\s*"([^"]+)"\s*>([\s\S]*?)</tool>',
    re.IGNORECASE,
)

_JSON_FENCE_RE = re.compile(
    r"```json\s*\n([\s\S]*?)\n```",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def parse_response(
    text: str,
    *,
    mode: Optional[TaskMode] = None,
    require_block: bool = True,
) -> ParsedOutput:
    """Parse an LLM response into a structured :class:`ParsedOutput`.

    Parameters
    ----------
    text:
        Raw assistant content.
    mode:
        Optional task mode. PHILOSOPHY enforces at least one ``<thought>``
        block; DEVELOPMENT is permissive.
    require_block:
        If True (default), raise :class:`CodeFormatError` when no block of
        any kind is found. Set False for free-form intermediate messages.
    """
    if text is None:
        text = ""

    files = _parse_files(text)
    thoughts = _parse_thoughts(text)
    tool_calls = _parse_tools(text)
    json_blocks = _parse_json_blocks(text)

    parsed = ParsedOutput(
        files=files,
        thoughts=thoughts,
        tool_calls=tool_calls,
        json_blocks=json_blocks,
        raw_text=text,
    )

    if require_block and parsed.is_empty():
        raise CodeFormatError(
            "no <file>, <thought>, <tool> or ```json``` block found in response",
            details={"snippet": text[:300]},
        )

    if mode is TaskMode.PHILOSOPHY and not thoughts:
        raise CodeFormatError(
            "PHILOSOPHY mode requires at least one <thought> block",
            details={"snippet": text[:300]},
        )

    return parsed


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _parse_files(text: str) -> List[FileOperation]:
    files: List[FileOperation] = []

    for match in _FILE_TAG_RE.finditer(text):
        path = match.group(1).strip()
        action_raw = (match.group(2) or "write").lower()
        content = match.group(3)
        # Trim a single leading/trailing newline that authors commonly insert
        # for readability.
        if content.startswith("\n"):
            content = content[1:]
        if content.endswith("\n"):
            content = content[:-1]

        if not path:
            raise MissingPathError(
                "<file> block found but path attribute is empty",
                details={"snippet": match.group(0)[:200]},
            )

        try:
            action = FileAction(action_raw)
        except ValueError as exc:
            raise CodeFormatError(
                f"unknown file action: {action_raw!r}",
                details={"path": path},
            ) from exc

        files.append(FileOperation(file_path=path, content=content, action=action))

    if files:
        return files

    # Legacy fallback path.
    for match in _LEGACY_FILE_RE.finditer(text):
        path = match.group(1).strip()
        content = match.group(2)
        if not path:
            raise MissingPathError("legacy fenced code block lacks `# file:` path")
        files.append(FileOperation(file_path=path, content=content))

    return files


def _parse_thoughts(text: str) -> List[str]:
    return [m.group(1).strip() for m in _THOUGHT_TAG_RE.finditer(text) if m.group(1).strip()]


def _parse_tools(text: str) -> List[ToolCall]:
    calls: List[ToolCall] = []
    for match in _TOOL_TAG_RE.finditer(text):
        name = match.group(1).strip()
        body = match.group(2).strip()
        # Allow CDATA wrapper for safety with JSON containing < / > characters.
        cdata = re.match(r"^<!\[CDATA\[([\s\S]*?)\]\]>$", body)
        if cdata:
            body = cdata.group(1).strip()
        if not body:
            args = {}
        else:
            try:
                args = json.loads(body)
            except json.JSONDecodeError as exc:
                raise CodeFormatError(
                    f"<tool name='{name}'> body is not valid JSON",
                    details={"body": body[:200], "error": str(exc)},
                ) from exc
            if not isinstance(args, dict):
                raise CodeFormatError(
                    f"<tool name='{name}'> JSON must be an object, got {type(args).__name__}",
                )
        calls.append(ToolCall(name=name, args=args))
    return calls


def _parse_json_blocks(text: str) -> List[dict]:
    blocks: List[dict] = []
    for match in _JSON_FENCE_RE.finditer(text):
        body = match.group(1).strip()
        if not body:
            continue
        try:
            value = json.loads(body)
        except json.JSONDecodeError as exc:
            raise CodeFormatError(
                "found ```json``` block but body is not valid JSON",
                details={"body": body[:200], "error": str(exc)},
            ) from exc
        if isinstance(value, dict):
            blocks.append(value)
        # Non-dict JSON (lists, scalars) is intentionally ignored: this layer
        # only models structured payloads (Plan / Reflection / metadata) as
        # objects. Tool args travel inside <tool> tags instead.
    return blocks


__all__ = ["parse_response"]
