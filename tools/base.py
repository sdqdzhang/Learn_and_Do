"""Tool protocol.

Every tool plugged into the workflow must:

1. Expose a :attr:`spec` of type :class:`core.schema.ToolSpec` describing
   its name, prose description, and JSON-Schema-shaped argument schema.
2. Implement :meth:`call`, which takes a validated ``args`` dict and
   returns a :class:`ToolResult`.

This keeps the workflow loop free of ``if-elif`` ladders: it just
invokes ``registry.invoke(tool_call)``.
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
    """Base class for all tools."""

    #: Subclasses must override with a class-level :class:`ToolSpec`.
    spec: ToolSpec

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Allow abstract subclasses without a spec, but concrete subclasses
        # must declare one.
        if not getattr(cls, "__abstractmethods__", None) and not getattr(cls, "spec", None):
            raise TypeError(f"{cls.__name__} must define a class-level `spec: ToolSpec`")

    # ------------------- public entry point ------------------- #

    def invoke(self, call: ToolCall) -> ToolResult:
        """Wrap :meth:`call` with timing, error handling, and result framing."""
        started = time.perf_counter()
        try:
            self._validate_args(call.args)
            output = self.call(call.args)
        except ToolError as exc:
            logger.warning("tool %s ToolError: %s", self.spec.name, exc)
            return ToolResult(
                call_id=call.id,
                name=self.spec.name,
                status=ToolStatus.FAILED,
                error=str(exc),
                metrics={"elapsed_ms": int((time.perf_counter() - started) * 1000)},
            )
        except Exception as exc:  # noqa: BLE001 -- normalise into ToolResult
            logger.exception("tool %s unexpected error", self.spec.name)
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

    # ------------------- to override ------------------- #

    @abc.abstractmethod
    def call(self, args: Dict[str, Any]) -> Any:
        """Concrete tool logic. Raise :class:`ToolError` for soft failures."""

    # ------------------- helpers ------------------- #

    def _validate_args(self, args: Dict[str, Any]) -> None:
        """Light-weight validation against ``spec.args_schema``.

        We only enforce *required* keys and *type* declarations of top-level
        properties; deep JSON Schema validation is intentionally out of
        scope for the base layer (callers can swap in jsonschema if needed).
        """
        schema = self.spec.args_schema or {}
        required = schema.get("required", [])
        for key in required:
            if key not in args:
                raise ToolError(
                    f"missing required arg '{key}' for tool '{self.spec.name}'",
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
                    f"arg '{key}' for tool '{self.spec.name}' has wrong type: "
                    f"expected {declared}, got {type(value).__name__}",
                    details={"value": value},
                )


__all__ = ["Tool"]
