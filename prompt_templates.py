"""Jinja2-driven prompt engine.

A single :class:`SystemPromptBuilder` produces the System prompt for any
``(AgentRole, TaskMode)`` combination. Behaviour is data-driven:

* The role-specific *base prompt* and the mode-specific *output rules*
  are looked up from in-module tables.
* Available tools are injected automatically from a list of
  :class:`ToolSpec`, so adding a new tool requires no template change.
* Arbitrary key/value context can be merged via ``extra_ctx``.

The aim is "ONE engine, MANY personalities": swap the role and the
mode and the same workflow runs a different Agent.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from jinja2 import Environment, StrictUndefined

from core.schema import AgentRole, TaskMode, ToolSpec


# --------------------------------------------------------------------------- #
# Role × Mode base templates
# --------------------------------------------------------------------------- #

# Each entry is a *role*'s base description. Mode-specific tweaks are
# layered on via the OUTPUT_RULES table below.
_ROLE_BASE: Dict[AgentRole, str] = {
    AgentRole.CODER: (
        "You are the Coder. Your job is to write, modify, and fix Python source files "
        "to satisfy the user's request. Prefer small, focused diffs. Always run the "
        "code in your head before emitting it."
    ),
    AgentRole.REVIEWER: (
        "You are the Reviewer. Your job is to critically inspect what the Coder or "
        "Investigator produced. Surface concrete bugs, missing edge cases, and "
        "anything that contradicts the stated goal. Be terse and specific."
    ),
    AgentRole.PHILOSOPHER: (
        "You are the Philosopher. Your job is to construct, defend, or revise "
        "arguments about the user's question. State assumptions explicitly. Whenever "
        "you commit to a non-obvious claim, mark it as a hypothesis the Investigator "
        "could falsify."
    ),
    AgentRole.INVESTIGATOR: (
        "You are the Investigator. Your job is to translate the Philosopher's "
        "hypotheses into executable scripts (scraping, statistics, simulation) and "
        "report the data back faithfully — including results that refute the "
        "hypothesis."
    ),
    AgentRole.PLANNER: (
        "You are the Planner. Decompose the goal into 3-7 numbered steps. Each step "
        "must be actionable in one turn (write a file, run a tool, draw a "
        "conclusion). Output the plan as a ```json``` block: "
        '{"goal": "...", "steps": ["...", "..."], "notes": "..."}.'
    ),
    AgentRole.REFLECTOR: (
        "You are the Reflector. Given the latest plan and what actually happened, "
        "decide whether to retry the same step, revise the plan, or finish. Output "
        "your decision as a ```json``` block: "
        '{"observations": "...", "conflicts": [...], "next_action": "retry|revise|done"}.'
    ),
}


# Output-format rules keyed by TaskMode. These layer on top of the role
# description and are visible to every role.
_OUTPUT_RULES: Dict[TaskMode, str] = {
    TaskMode.DEVELOPMENT: (
        "Output rules:\n"
        '- Source code goes inside `<file path="relative/path.py">...</file>` tags.\n'
        "- `path` is always relative to the project workspace.\n"
        "- For tool invocation, use `<tool name=\"<tool_name>\">{json args}</tool>`.\n"
        "- A `<thought>...</thought>` block is optional but encouraged for non-trivial reasoning."
    ),
    TaskMode.PHILOSOPHY: (
        "Output rules:\n"
        "- Every response MUST contain at least one `<thought>...</thought>` block "
        "describing your reasoning chain.\n"
        '- Empirical scripts go in `<file path="relative/path.py">...</file>` tags.\n'
        "- Tool calls use `<tool name=\"<tool_name>\">{json args}</tool>`.\n"
        "- Treat data from tools as evidence; never invent statistics."
    ),
}


# A shared epilogue with safety and tone constraints. Applies in every
# (role, mode) combination.
_COMMON_EPILOGUE = (
    "Constraints:\n"
    "- Never attempt destructive system commands (rm -rf /, fork bombs, etc.).\n"
    "- Stay strictly in character; do not impersonate the user.\n"
    "- If you cannot proceed, say so plainly rather than fabricating output."
)


# --------------------------------------------------------------------------- #
# Jinja template
# --------------------------------------------------------------------------- #

_TEMPLATE_SOURCE = """\
{{ role_base }}

Task mode: {{ mode_label }}.

{{ output_rules }}
{% if tools %}
Available tools (call exactly one at a time via the <tool> tag):
{% for t in tools %}
- {{ t.name }}: {{ t.description }}
  args schema: {{ t.args_schema | tojson }}
{% endfor %}
{% else %}
No tools are wired up for this turn; respond directly.
{% endif %}
{% if extra_ctx %}
Additional context:
{% for k, v in extra_ctx.items() %}
- {{ k }}: {{ v }}
{% endfor %}
{% endif %}
{{ epilogue }}
"""


_jinja_env = Environment(
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=False,
)
_compiled_template = _jinja_env.from_string(_TEMPLATE_SOURCE)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

class SystemPromptBuilder:
    """Compose the System prompt for one (role, mode) pair."""

    def __init__(
        self,
        role: AgentRole,
        mode: TaskMode,
        tools: Optional[List[ToolSpec]] = None,
        extra_ctx: Optional[Dict[str, str]] = None,
    ) -> None:
        if role not in _ROLE_BASE:
            raise ValueError(f"unknown AgentRole: {role!r}")
        if mode not in _OUTPUT_RULES:
            raise ValueError(f"unknown TaskMode: {mode!r}")
        self._role = role
        self._mode = mode
        self._tools = list(tools or [])
        self._extra_ctx = dict(extra_ctx or {})

    # Properties keep the rendered output stable across calls; useful for
    # tests that want to assert prompt content without running render().
    @property
    def role(self) -> AgentRole:
        return self._role

    @property
    def mode(self) -> TaskMode:
        return self._mode

    def with_tools(self, tools: List[ToolSpec]) -> "SystemPromptBuilder":
        """Return a copy with a refreshed tool list."""
        return SystemPromptBuilder(self._role, self._mode, tools, self._extra_ctx)

    def with_context(self, **kv: str) -> "SystemPromptBuilder":
        """Return a copy with extra context merged in."""
        merged = {**self._extra_ctx, **kv}
        return SystemPromptBuilder(self._role, self._mode, self._tools, merged)

    def render(self) -> str:
        return _compiled_template.render(
            role_base=_ROLE_BASE[self._role],
            mode_label=self._mode.value.upper(),
            output_rules=_OUTPUT_RULES[self._mode],
            tools=[
                {
                    "name": t.name,
                    "description": t.description,
                    "args_schema": t.args_schema,
                }
                for t in self._tools
            ],
            extra_ctx=self._extra_ctx,
            epilogue=_COMMON_EPILOGUE,
        ).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# Convenience helpers
# --------------------------------------------------------------------------- #

def supported_pairs() -> List[Tuple[AgentRole, TaskMode]]:
    """Enumerate every (role, mode) combination this engine can render."""
    return [(role, mode) for role in _ROLE_BASE for mode in _OUTPUT_RULES]


__all__ = ["SystemPromptBuilder", "supported_pairs"]
