"""将 WebSocket ``start`` JSON 解析为会话运行时对象（覆盖 .env 默认值）。"""

from __future__ import annotations

import json
import os
import re
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from core.schema import AgentRole
from core.session_manager import SessionConfig, SessionManager
from core.workflow import WorkflowConfig
from memory.session_context import ContextConfig
from runtime.executor import ExecutorConfig
from utils.llm_client import LLMConfig

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _parse_llm_endpoint_presets_env(raw: Optional[str]) -> list[dict[str, Any]]:
    """Parse ``LLM_ENDPOINT_PRESETS`` JSON array for the web settings UI.

    Each object: ``label`` (optional), ``base_url`` / ``baseUrl``, ``model``, optional ``id``.
    ``api_key`` is never included in the HTTP response (secrets stay in ``.env`` / browser only).
    """
    if not raw or not str(raw).strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        base_url = str(item.get("base_url") or item.get("baseUrl") or "").strip()
        model = str(item.get("model") or "").strip()
        if not base_url or not model:
            continue
        label = str(item.get("label") or model).strip() or model
        sid = str(item.get("id") or "").strip() or f"env-{i}"
        out.append(
            {
                "id": sid,
                "label": label,
                "base_url": base_url,
                "model": model,
                "api_key": "",
            }
        )
    return out


def _truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    return str(v).lower() in ("1", "true", "yes", "on")


def _coerce_float(v: Any, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _coerce_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _parse_csv_roles(raw: str) -> Tuple[AgentRole, ...]:
    out: list[AgentRole] = []
    for part in raw.split(","):
        p = part.strip()
        if not p:
            continue
        try:
            out.append(AgentRole(p))
        except ValueError:
            continue
    return tuple(out)


def _safe_workspace_dir(raw: str, fallback: str) -> str:
    p = Path(raw or fallback).expanduser()
    if not p.is_absolute():
        p = (_REPO_ROOT / p).resolve()
    try:
        p.relative_to(_REPO_ROOT.resolve())
    except ValueError:
        return fallback
    return str(p)


def build_llm_config(start: Dict[str, Any]) -> LLMConfig:
    base = LLMConfig.from_env()
    raw = start.get("llm")
    if not isinstance(raw, dict):
        return base
    kwargs: Dict[str, Any] = {}
    if raw.get("base_url") is not None:
        kwargs["base_url"] = str(raw["base_url"]).strip() or base.base_url
    if raw.get("api_key") is not None:
        kwargs["api_key"] = str(raw["api_key"])
    if raw.get("model") is not None:
        kwargs["model"] = str(raw["model"]).strip() or base.model
    if raw.get("timeout_seconds") is not None:
        kwargs["timeout_seconds"] = max(5.0, min(_coerce_float(raw["timeout_seconds"], base.timeout_seconds), 3600.0))
    if raw.get("max_retries") is not None:
        kwargs["max_retries"] = max(0, min(_coerce_int(raw["max_retries"], base.max_retries), 10))
    if raw.get("temperature") is not None:
        kwargs["temperature"] = max(0.0, min(_coerce_float(raw["temperature"], base.temperature), 2.0))
    return replace(base, **kwargs) if kwargs else base


def summarizer_model_from_start(start: Dict[str, Any]) -> Optional[str]:
    raw = start.get("llm")
    if not isinstance(raw, dict):
        return None
    m = raw.get("summarizer_model")
    if m is None:
        return None
    s = str(m).strip()
    return s or None


def build_context_config(start: Dict[str, Any]) -> ContextConfig:
    base = ContextConfig.from_env()
    raw = start.get("context")
    if not isinstance(raw, dict):
        return base
    kwargs: Dict[str, Any] = {}
    if raw.get("max_tokens") is not None:
        kwargs["max_tokens"] = max(512, min(_coerce_int(raw["max_tokens"], base.max_tokens), 200_000))
    if raw.get("preserve_recent") is not None:
        kwargs["preserve_recent"] = max(1, min(_coerce_int(raw["preserve_recent"], base.preserve_recent), 64))
    if raw.get("tiktoken_encoding") is not None:
        enc = str(raw["tiktoken_encoding"]).strip()
        if enc and re.match(r"^[a-zA-Z0-9_\-]+$", enc):
            kwargs["tiktoken_encoding"] = enc
    return replace(base, **kwargs) if kwargs else base


def build_executor_config(start: Dict[str, Any]) -> ExecutorConfig:
    base = ExecutorConfig.from_env()
    raw = start.get("executor")
    if not isinstance(raw, dict):
        return base
    kwargs: Dict[str, Any] = {}
    if raw.get("image") is not None:
        kwargs["image"] = str(raw["image"]).strip() or base.image
    if raw.get("name_prefix") is not None:
        kwargs["name_prefix"] = str(raw["name_prefix"]).strip() or base.name_prefix
    if raw.get("workspace_dir") is not None:
        kwargs["workspace_dir"] = _safe_workspace_dir(str(raw["workspace_dir"]), base.workspace_dir)
    if raw.get("memory_limit") is not None:
        kwargs["memory_limit"] = str(raw["memory_limit"]).strip() or base.memory_limit
    if raw.get("cpu_limit") is not None:
        kwargs["cpu_limit"] = max(0.1, min(_coerce_float(raw["cpu_limit"], base.cpu_limit), 128.0))
    if raw.get("default_timeout") is not None:
        kwargs["default_timeout"] = max(5, min(_coerce_int(raw["default_timeout"], base.default_timeout), 86400))
    return replace(base, **kwargs) if kwargs else base


def build_session_config(start: Dict[str, Any]) -> SessionConfig:
    base = SessionConfig.from_env()
    raw = start.get("session")
    if not isinstance(raw, dict):
        return base
    kwargs: Dict[str, Any] = {}
    if raw.get("name_prefix") is not None:
        p = str(raw["name_prefix"]).strip()
        if p and re.match(r"^[a-zA-Z0-9._-]+$", p):
            kwargs["name_prefix"] = p
    if raw.get("idle_timeout_seconds") is not None:
        kwargs["idle_timeout_s"] = max(60, min(_coerce_int(raw["idle_timeout_seconds"], base.idle_timeout_s), 864000))
    if raw.get("heartbeat_interval_seconds") is not None:
        kwargs["heartbeat_interval_s"] = max(
            10, min(_coerce_int(raw["heartbeat_interval_seconds"], base.heartbeat_interval_s), 3600)
        )
    return replace(base, **kwargs) if kwargs else base


def build_session_manager(start: Dict[str, Any]) -> SessionManager:
    return SessionManager(
        config=build_session_config(start),
        executor_config=build_executor_config(start),
    )


def merge_workflow_config(
    start: Dict[str, Any],
    *,
    role: AgentRole,
    max_turns: int,
) -> WorkflowConfig:
    base = WorkflowConfig.from_env(role=role)
    raw = start.get("workflow")
    overrides: Dict[str, Any] = {"max_turns": max(1, min(max_turns, 500))}
    if isinstance(raw, dict):
        if "intervention_enabled" in raw:
            overrides["intervention_enabled"] = _truthy(raw["intervention_enabled"])
        if "intervention_timeout_s" in raw:
            overrides["intervention_timeout_s"] = max(
                0.0, min(_coerce_float(raw["intervention_timeout_s"], base.intervention_timeout_s), 86400.0)
            )
        if raw.get("sensitive_tools") is not None:
            st = str(raw["sensitive_tools"]).strip()
            overrides["sensitive_tool_names"] = frozenset(t.strip() for t in st.split(",") if t.strip())
        if raw.get("multi_reflection_roles") is not None:
            overrides["multi_reflection_roles"] = _parse_csv_roles(str(raw["multi_reflection_roles"]))
        if "guard_preflight_enabled" in raw:
            overrides["guard_preflight_enabled"] = _truthy(raw["guard_preflight_enabled"])
        if "guard_outbound_enabled" in raw:
            overrides["guard_outbound_enabled"] = _truthy(raw["guard_outbound_enabled"])
        if raw.get("guard_max_rounds") is not None:
            overrides["guard_max_rounds"] = max(1, min(_coerce_int(raw["guard_max_rounds"], base.guard_max_rounds), 5))
        if raw.get("guard_model") is not None:
            gm = str(raw["guard_model"]).strip()
            overrides["guard_model"] = gm or None
        if "infer_done_after_tool_reply" in raw:
            overrides["infer_done_after_tool_reply"] = _truthy(raw["infer_done_after_tool_reply"])
    return replace(base, **overrides)


def resolve_intervention(
    start: Dict[str, Any],
    wf_cfg: WorkflowConfig,
) -> Tuple[WorkflowConfig, Optional[float]]:
    """根级 ``intervention_timeout_s`` 与 ``workflow`` 合并；返回 (workflow, iv_wait)。"""
    root_iv = start.get("intervention_timeout_s")
    root_wait: Optional[float] = None
    if root_iv is not None:
        try:
            v = float(root_iv)
            if v > 0:
                root_wait = v
        except (TypeError, ValueError):
            pass

    if root_wait is not None:
        wf2 = replace(
            wf_cfg,
            intervention_enabled=True,
            intervention_timeout_s=root_wait,
        )
        return wf2, root_wait

    if wf_cfg.intervention_enabled and wf_cfg.intervention_timeout_s > 0:
        return wf_cfg, wf_cfg.intervention_timeout_s

    return wf_cfg, None


def public_server_defaults() -> Dict[str, Any]:
    """供 ``GET /api/settings`` 暴露（不含完整 api_key）。"""
    llm = LLMConfig.from_env()
    wf = WorkflowConfig.from_env(role=AgentRole.CODER)
    ex = ExecutorConfig.from_env()
    ss = SessionConfig.from_env()
    ctx = ContextConfig.from_env()
    key = llm.api_key
    key_disp = "(empty)" if not key else (key[:3] + "…" + key[-2:] if len(key) > 6 else "***")
    return {
        "llm": {
            "base_url": llm.base_url,
            "api_key_display": key_disp,
            "model": llm.model,
            "llm_endpoint_presets": _parse_llm_endpoint_presets_env(os.getenv("LLM_ENDPOINT_PRESETS")),
            "timeout_seconds": llm.timeout_seconds,
            "max_retries": llm.max_retries,
            "temperature": llm.temperature,
            "summarizer_model": os.getenv("LLM_SUMMARIZER_MODEL", "").strip() or None,
        },
        "workflow": {
            "max_turns": wf.max_turns,
            "intervention_enabled": wf.intervention_enabled,
            "intervention_timeout_s": wf.intervention_timeout_s,
            "sensitive_tools": ",".join(sorted(wf.sensitive_tool_names)),
            "multi_reflection_roles": ",".join(r.value for r in wf.multi_reflection_roles),
            "guard_preflight_enabled": wf.guard_preflight_enabled,
            "guard_outbound_enabled": wf.guard_outbound_enabled,
            "guard_max_rounds": wf.guard_max_rounds,
            "guard_model": wf.guard_model,
            "infer_done_after_tool_reply": wf.infer_done_after_tool_reply,
        },
        "session": {
            "name_prefix": ss.name_prefix,
            "idle_timeout_seconds": ss.idle_timeout_s,
            "heartbeat_interval_seconds": ss.heartbeat_interval_s,
        },
        "executor": {
            "image": ex.image,
            "name_prefix": ex.name_prefix,
            "workspace_dir": ex.workspace_dir,
            "memory_limit": ex.memory_limit,
            "cpu_limit": ex.cpu_limit,
            "default_timeout": ex.default_timeout,
        },
        "context": {
            "max_tokens": ctx.max_tokens,
            "preserve_recent": ctx.preserve_recent,
            "tiktoken_encoding": ctx.tiktoken_encoding,
        },
    }


__all__ = [
    "build_llm_config",
    "build_context_config",
    "build_session_manager",
    "merge_workflow_config",
    "resolve_intervention",
    "summarizer_model_from_start",
    "public_server_defaults",
]
