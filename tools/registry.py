"""Tool registry.

Holds a collection of :class:`Tool` instances and dispatches
:class:`ToolCall` objects to the matching one. Keeping discovery in a
registry (rather than a global ``invoke_tool`` switch statement) means
``main.py`` can register the exact tools each session needs and the
workflow stays mode-agnostic.
"""

from __future__ import annotations

from typing import Dict, List

from core.exceptions import ToolError
from core.schema import ToolCall, ToolResult, ToolSpec
from tools.base import Tool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}

    # ------------------- registration ------------------- #

    def register(self, tool: Tool, *, overwrite: bool = False) -> None:
        name = tool.spec.name
        if name in self._tools and not overwrite:
            raise ValueError(f"tool '{name}' is already registered")
        self._tools[name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    # ------------------- introspection ------------------- #

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolError(
                f"no such tool: '{name}'",
                details={"available": sorted(self._tools)},
            ) from exc

    def has(self, name: str) -> bool:
        return name in self._tools

    def list_specs(self) -> List[ToolSpec]:
        return [tool.spec for tool in self._tools.values()]

    def names(self) -> List[str]:
        return sorted(self._tools)

    # ------------------- dispatch ------------------- #

    def invoke(self, call: ToolCall) -> ToolResult:
        tool = self.get(call.name)
        return tool.invoke(call)


__all__ = ["ToolRegistry"]
