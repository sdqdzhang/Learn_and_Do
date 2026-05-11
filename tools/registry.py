"""工具注册表。

持有一组 :class:`Tool` 实例，并把 :class:`ToolCall` 路由到对应的实例上。
把工具发现集中放进注册表（而不是写一个全局 ``invoke_tool`` 的 switch），
是为了让 ``main.py`` 能够按需要给每个 session 注册不同的工具集，而
workflow 内部保持任务模式无关。
"""

from __future__ import annotations

from typing import Dict, List

from core.exceptions import ToolError
from core.schema import ToolCall, ToolResult, ToolSpec
from tools.base import Tool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}

    # ------------------- 注册 / 注销 ------------------- #

    def register(self, tool: Tool, *, overwrite: bool = False) -> None:
        name = tool.spec.name
        if name in self._tools and not overwrite:
            raise ValueError(f"工具 '{name}' 已经注册过了")
        self._tools[name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    # ------------------- 内省 ------------------- #

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolError(
                f"未注册的工具：'{name}'",
                details={"available": sorted(self._tools)},
            ) from exc

    def has(self, name: str) -> bool:
        return name in self._tools

    def list_specs(self) -> List[ToolSpec]:
        return [tool.spec for tool in self._tools.values()]

    def names(self) -> List[str]:
        return sorted(self._tools)

    # ------------------- 分发 ------------------- #

    def invoke(self, call: ToolCall) -> ToolResult:
        tool = self.get(call.name)
        return tool.invoke(call)


__all__ = ["ToolRegistry"]
