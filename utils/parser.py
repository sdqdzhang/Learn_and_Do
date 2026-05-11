"""严格响应解析器。

从 LLM 输出里提取结构化块。共识别四种块，它们可以自由交错出现，也可以
任意子集出现：

1. ``<file path="...">...</file>``  -> :class:`FileOperation`
2. ``<thought>...</thought>``       -> 字符串
3. ``<tool name="...">JSON</tool>`` -> :class:`ToolCall`（args 解析为 JSON）
4. ``` ```json ... ``` ```          -> dict（Plan / Reflection 等）

V1.1 时期的 ``# file: <path>`` 旧格式仍然作为 ``<file>`` 的兼容回退保留。

模块只在 **校验那一步** 才感知任务模式：:func:`parse_response` 接受
``mode`` 关键字参数，并强制 PHILOSOPHY 模式的 assistant 至少产出一个
``<thought>`` 块。
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
# 预编译正则（模块级，让每次 parse_response 都不必重复编译）
# --------------------------------------------------------------------------- #

_FILE_TAG_RE = re.compile(
    r'<file\s+path\s*=\s*"([^"]+)"(?:\s+action\s*=\s*"([^"]+)")?\s*>'
    r"([\s\S]*?)</file>",
    re.IGNORECASE,
)

# 旧版兼容形式：``` ```python\n# file: path\n<code>\n``` ```
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
# 公共 API
# --------------------------------------------------------------------------- #

def parse_response(
    text: str,
    *,
    mode: Optional[TaskMode] = None,
    require_block: bool = True,
) -> ParsedOutput:
    """把 LLM 响应解析为结构化的 :class:`ParsedOutput`。

    Parameters
    ----------
    text:
        assistant 的原始输出。
    mode:
        可选的任务模式。PHILOSOPHY 时强制要求至少一个 ``<thought>`` 块；
        DEVELOPMENT 时较为宽容。
    require_block:
        默认 True：若一个结构化块都没找到则抛 :class:`CodeFormatError`。
        对于自由形式的中间消息可以设置成 False。
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
            "响应中未找到任何 <file>、<thought>、<tool> 或 ```json``` 块",
            details={"snippet": text[:300]},
        )

    if mode is TaskMode.PHILOSOPHY and not thoughts:
        raise CodeFormatError(
            "PHILOSOPHY 模式要求响应中至少包含一个 <thought> 块",
            details={"snippet": text[:300]},
        )

    return parsed


# --------------------------------------------------------------------------- #
# 辅助函数
# --------------------------------------------------------------------------- #

def _parse_files(text: str) -> List[FileOperation]:
    files: List[FileOperation] = []

    for match in _FILE_TAG_RE.finditer(text):
        path = match.group(1).strip()
        action_raw = (match.group(2) or "write").lower()
        content = match.group(3)
        # 修剪一个开头/结尾的换行 —— 作者出于排版习惯常会写一个空行。
        if content.startswith("\n"):
            content = content[1:]
        if content.endswith("\n"):
            content = content[:-1]

        if not path:
            raise MissingPathError(
                "找到 <file> 块但 path 属性为空",
                details={"snippet": match.group(0)[:200]},
            )

        try:
            action = FileAction(action_raw)
        except ValueError as exc:
            raise CodeFormatError(
                f"未知的 file action：{action_raw!r}",
                details={"path": path},
            ) from exc

        files.append(FileOperation(file_path=path, content=content, action=action))

    if files:
        return files

    # 走旧版兼容格式回退路径。
    for match in _LEGACY_FILE_RE.finditer(text):
        path = match.group(1).strip()
        content = match.group(2)
        if not path:
            raise MissingPathError("旧版围栏代码块缺少 `# file:` 路径")
        files.append(FileOperation(file_path=path, content=content))

    return files


def _parse_thoughts(text: str) -> List[str]:
    return [m.group(1).strip() for m in _THOUGHT_TAG_RE.finditer(text) if m.group(1).strip()]


def _parse_tools(text: str) -> List[ToolCall]:
    calls: List[ToolCall] = []
    for match in _TOOL_TAG_RE.finditer(text):
        name = match.group(1).strip()
        body = match.group(2).strip()
        # 兼容 CDATA 包裹（让含 < / > 的 JSON 也能干净传递）。
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
                    f"<tool name='{name}'> 块的 body 不是合法的 JSON",
                    details={"body": body[:200], "error": str(exc)},
                ) from exc
            if not isinstance(args, dict):
                raise CodeFormatError(
                    f"<tool name='{name}'> 的 JSON 必须是对象，实际拿到 {type(args).__name__}",
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
                "找到 ```json``` 块但内容不是合法 JSON",
                details={"body": body[:200], "error": str(exc)},
            ) from exc
        if isinstance(value, dict):
            blocks.append(value)
        # 非 dict 的 JSON（list / 标量）会被忽略：本层只把结构化载荷
        # （Plan / Reflection / 元数据）建模成对象。工具参数走 <tool> 标签。
    return blocks


__all__ = ["parse_response"]
