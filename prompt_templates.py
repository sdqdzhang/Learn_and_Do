"""Jinja2 驱动的 prompt 引擎（中文优先版本）。

一个 :class:`SystemPromptBuilder` 就能为任意 ``(AgentRole, TaskMode)`` 组合
渲染出 System prompt。角色描述、输出规则、以及外层 Jinja 模板里的固定文案
全部使用中文 —— 项目主要面向中文本地模型（Qwen、DeepSeek、ChatGLM 等）。

会被 parser / workflow 消费的"结构化标识符"——JSON 键名（``goal``/``steps``/
``next_action``）和 ``<file>``/``<thought>``/``<tool>`` 标签——保留英文，
因为它们是 *代码契约*，不是自然语言。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from jinja2 import Environment, StrictUndefined

from core.schema import AgentRole, TaskMode, ToolSpec


# --------------------------------------------------------------------------- #
# 角色 × 模式的基础模板（中文）
# --------------------------------------------------------------------------- #

_ROLE_BASE: Dict[AgentRole, str] = {
    AgentRole.CODER: (
        "你是编码者（Coder）。你的职责是编写、修改和修复 Python 源代码，以"
        "满足用户的需求。倾向于小而聚焦的改动。在输出代码之前，先在脑中走一"
        "遍逻辑。"
    ),
    AgentRole.REVIEWER: (
        "你是审阅者（Reviewer）。你的职责是批判性地检查编码者或调查员产出"
        "的内容。指出具体的 bug、遗漏的边界情况，以及任何与既定目标相悖之"
        "处。语气要简洁、具体。"
    ),
    AgentRole.PHILOSOPHER: (
        "你是哲学家（Philosopher）。你的职责是围绕用户的问题构建、捍卫或修"
        "正论证。明确陈述前提。每当你提出一个非显而易见的主张时，把它标注"
        "为一个调查员可以去证伪的假设。"
    ),
    AgentRole.INVESTIGATOR: (
        "你是调查员（Investigator）。你的职责是把哲学家的假设翻译成可执行"
        "的脚本（爬取、统计、模拟），并如实回报数据 —— 包括那些反驳假设的"
        "结果。"
    ),
    AgentRole.PLANNER: (
        "你是规划者（Planner）。请把目标分解为 3 到 7 个编号步骤。每个步骤"
        "必须能在一轮之内执行完成（写一个文件、调用一个工具，或得出一个明"
        "确结论）。把计划放在 ```json``` 代码块中输出，结构如下："
        '{"goal": "...", "steps": ["...", "..."], "notes": "..."}。'
    ),
    AgentRole.REFLECTOR: (
        "你是反思者（Reflector）。根据最新的计划与实际发生的情况，决定是重"
        "试当前步骤、修订计划，还是结束。把你的决定放在 ```json``` 代码块"
        "中输出，结构如下："
        '{"observations": "...", "conflicts": [...], '
        '"next_action": "retry|revise|done"}。'
    ),
}


# --------------------------------------------------------------------------- #
# 按 TaskMode 区分的输出格式规则（中文）
# --------------------------------------------------------------------------- #

_OUTPUT_RULES: Dict[TaskMode, str] = {
    TaskMode.DEVELOPMENT: (
        "输出规则：\n"
        "- 源代码必须放在 `<file path=\"相对/路径.py\">...</file>` 标签内。\n"
        "- `path` 始终是相对于项目工作空间的相对路径，不允许绝对路径或 `..`。\n"
        "- 工具调用必须使用 `<tool name=\"<工具名>\">{JSON 参数}</tool>`。\n"
        "- `<thought>...</thought>` 块可选；遇到非平凡推理时鼓励使用。"
    ),
    TaskMode.PHILOSOPHY: (
        "输出规则：\n"
        "- 每一轮响应都必须至少包含一个 `<thought>...</thought>` 块，"
        "用于展示你的推理链条。\n"
        "- 实证脚本必须放在 `<file path=\"相对/路径.py\">...</file>` 标签内。\n"
        "- 工具调用必须使用 `<tool name=\"<工具名>\">{JSON 参数}</tool>`。\n"
        "- 把工具返回的数据视为证据；任何统计数字都不允许凭空编造。"
    ),
}


# 通用结尾：安全与语气约束。在所有 (role, mode) 下都会被附加上。
_COMMON_EPILOGUE = (
    "约束：\n"
    "- 绝不尝试任何破坏性的系统命令（如 rm -rf /、fork 炸弹等）。\n"
    "- 严格保持你的角色；不要冒充用户或其他角色发言。\n"
    "- 如果你无法继续，请直白说明，而不是编造输出。\n"
    "- 当你认为整体任务已经完成、不再需要进一步动作时，"
    "在回复末尾加上 `[完成]` 标记。"
)


# 给 TaskMode 枚举值配上人类可读的中文标签。
_MODE_LABEL_CN: Dict[TaskMode, str] = {
    TaskMode.DEVELOPMENT: "代码开发（DEVELOPMENT）",
    TaskMode.PHILOSOPHY: "哲学研究（PHILOSOPHY）",
}


# --------------------------------------------------------------------------- #
# Jinja 模板（中文外壳）
# --------------------------------------------------------------------------- #

_TEMPLATE_SOURCE = """\
{{ role_base }}

任务模式：{{ mode_label }}。

{{ output_rules }}
{% if tools %}
可用工具（每次只能通过一个 <tool> 标签调用其中之一）：
{% for t in tools %}
- {{ t.name }}：{{ t.description }}
  参数 schema：{{ t.args_schema | tojson }}
{% endfor %}
{% else %}
本轮没有挂载任何工具；请直接作答。
{% endif %}
{% if extra_ctx %}
附加上下文：
{% for k, v in extra_ctx.items() %}
- {{ k }}：{{ v }}
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
# 公共 API
# --------------------------------------------------------------------------- #

class SystemPromptBuilder:
    """为一对 (role, mode) 拼装 System prompt。"""

    def __init__(
        self,
        role: AgentRole,
        mode: TaskMode,
        tools: Optional[List[ToolSpec]] = None,
        extra_ctx: Optional[Dict[str, str]] = None,
    ) -> None:
        if role not in _ROLE_BASE:
            raise ValueError(f"未知的 AgentRole：{role!r}")
        if mode not in _OUTPUT_RULES:
            raise ValueError(f"未知的 TaskMode：{mode!r}")
        self._role = role
        self._mode = mode
        self._tools = list(tools or [])
        self._extra_ctx = dict(extra_ctx or {})

    @property
    def role(self) -> AgentRole:
        return self._role

    @property
    def mode(self) -> TaskMode:
        return self._mode

    def with_tools(self, tools: List[ToolSpec]) -> "SystemPromptBuilder":
        """返回一个工具列表已替换的新副本（不可变模式）。"""
        return SystemPromptBuilder(self._role, self._mode, tools, self._extra_ctx)

    def with_context(self, **kv: str) -> "SystemPromptBuilder":
        """返回一个合并了额外上下文的新副本。"""
        merged = {**self._extra_ctx, **kv}
        return SystemPromptBuilder(self._role, self._mode, self._tools, merged)

    def render(self) -> str:
        return _compiled_template.render(
            role_base=_ROLE_BASE[self._role],
            mode_label=_MODE_LABEL_CN[self._mode],
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
# 便利函数
# --------------------------------------------------------------------------- #

def supported_pairs() -> List[Tuple[AgentRole, TaskMode]]:
    """枚举本引擎能渲染的所有 (role, mode) 组合。"""
    return [(role, mode) for role in _ROLE_BASE for mode in _OUTPUT_RULES]


__all__ = ["SystemPromptBuilder", "supported_pairs"]
