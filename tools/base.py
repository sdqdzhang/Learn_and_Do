"""Tool 协议。

任何接入到 workflow 里的工具都必须：

1. 在类级别声明一个 :attr:`spec`，类型为 :class:`core.schema.ToolSpec`，
   描述工具的名称、自然语言说明，以及形似 JSON Schema 的参数描述。
2. 实现 :meth:`call` —— 拿到经过校验的 ``args`` dict，返回任意可序列化结
   果；如失败则抛 :class:`ToolError`。

这样 workflow 主循环里不会出现一长串 ``if-elif`` 分支，而是直接
``registry.invoke(tool_call)``。
"""

from __future__ import annotations

import abc
import logging
import time
from typing import Any, Dict

from core.exceptions import ToolError
from core.schema import ToolCall, ToolResult, ToolSpec, ToolStatus

logger = logging.getLogger(__name__)


class Tool(abc.ABC):
    """所有工具的基类。"""

    #: 子类必须重写：一个类级别的 :class:`ToolSpec`。
    spec: ToolSpec

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # 抽象子类可以没有 spec；具体子类必须声明一个。
        if not getattr(cls, "__abstractmethods__", None) and not getattr(cls, "spec", None):
            raise TypeError(f"{cls.__name__} 必须在类级别声明 `spec: ToolSpec`")

    # ------------------- 对外入口 ------------------- #

    def invoke(self, call: ToolCall) -> ToolResult:
        """给 :meth:`call` 套上计时、异常捕获、结果封装。"""
        started = time.perf_counter()
        try:
            self._validate_args(call.args)
            output = self.call(call.args)
        except ToolError as exc:
            logger.warning("工具 %s 报错 ToolError：%s", self.spec.name, exc)
            return ToolResult(
                call_id=call.id,
                name=self.spec.name,
                status=ToolStatus.FAILED,
                error=str(exc),
                metrics={"elapsed_ms": int((time.perf_counter() - started) * 1000)},
            )
        except Exception as exc:  # noqa: BLE001 -- 规范化进 ToolResult
            logger.exception("工具 %s 抛出未预期异常", self.spec.name)
            return ToolResult(
                call_id=call.id,
                name=self.spec.name,
                status=ToolStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}",
                metrics={"elapsed_ms": int((time.perf_counter() - started) * 1000)},
            )

        return ToolResult(
            call_id=call.id,
            name=self.spec.name,
            status=ToolStatus.SUCCESS,
            output=output,
            metrics={"elapsed_ms": int((time.perf_counter() - started) * 1000)},
        )

    # ------------------- 子类需实现 ------------------- #

    @abc.abstractmethod
    def call(self, args: Dict[str, Any]) -> Any:
        """具体工具的业务逻辑；软失败请抛 :class:`ToolError`。"""

    # ------------------- 辅助方法 ------------------- #

    def _validate_args(self, args: Dict[str, Any]) -> None:
        """对 ``spec.args_schema`` 做轻量级校验。

        基础层只校验：必填字段是否齐全，以及顶层 property 的 type 是否
        匹配。完整的 JSON Schema 校验刻意不做（调用方需要时可以自己接入
        ``jsonschema``）。
        """
        schema = self.spec.args_schema or {}
        required = schema.get("required", [])
        for key in required:
            if key not in args:
                raise ToolError(
                    f"工具 '{self.spec.name}' 缺少必填参数 '{key}'",
                    details={"args": args, "required": required},
                )

        props = schema.get("properties", {})
        type_map = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        for key, value in args.items():
            if key not in props:
                continue
            declared = props[key].get("type")
            if not declared:
                continue
            expected = type_map.get(declared)
            if expected and not isinstance(value, expected):
                raise ToolError(
                    f"工具 '{self.spec.name}' 的参数 '{key}' 类型不对："
                    f"声明为 {declared}，实际为 {type(value).__name__}",
                    details={"value": value},
                )


__all__ = ["Tool"]
