"""工具预检与出站校验：独立 system/user 会话，与主对话上下文隔离。"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from core.schema import ParsedOutput, TaskMode

_JSON_FENCE_LOOSE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)

PREFLIGHT_SYSTEM = """你是「工具预检修复器」（独立会话，不继承主对话历史）。
主助手刚生成了一轮回复，其中可能包含 `<tool name="...">{JSON}</tool>` 块，也可能混有未闭合的 `<thought>`、臆造的 `<python_repl>`、或非法 JSON。

你的任务：只输出**一整段**可直接作为 assistant 正文替换原稿的修订文本，须满足：
1. 仅使用约定结构化块：`<file path="...">...</file>`、`<thought>...</thought>`（必须成对闭合）、`<tool name="...">{}</tool>`，以及可选的 ```json 反思块。
2. 每个 `<tool>` 的 body 必须是合法 JSON 对象；`name` 必须来自用户给出的「允许工具名」列表之一。
3. 不要输出任何前言/后记（不要写「以下是修复稿」），不要发明新标签。

若无法完全修好，仍输出你能力范围内最可解析、最接近原意的版本。"""

OUTBOUND_SYSTEM = """你是「出站完成度与契约校验器」（独立会话）。
主助手在回复中使用了 `[完成]` / done 类终止哨兵，且本轮**尚未执行任何工具**。你需要判断：在 DEVELOPMENT 类代码任务下，这是否属于「假完成」或格式仍违规。

用户消息中可能附带「最近对话摘录」：其中可含更早轮次的用户目标、工具成功/失败结果等，请结合摘录判断「完成」是否合理，不要只看本轮正文。

你只允许输出**一个** Markdown JSON 围栏，格式如下（键名固定）：
```json
{
  "allow_session_done": true 或 false,
  "assistant_replacement": "若非空，则用其完整替换上一轮 assistant 正文；若无需替换则为空字符串",
  "feedback": "当 allow_session_done 为 false 时，简短说明原因（中文）；可为空字符串"
}
```

判定指引：
- 用户目标明显需要执行代码/读写文件/REPL，而正文中没有合规 `<tool>` 却声称完成 → `allow_session_done` 应为 false。
- 若仅需删改错误完成标记、补上合规 `<tool>` 即可 → 可将修正稿写入 `assistant_replacement` 且将 `allow_session_done` 置为 true 或 false（由你是否认为任务已真正满足决定）。
- JSON 必须能被标准库解析；不要夹杂围栏外的文字。"""

OUTBOUND_POST_TOOL_SYSTEM = """你是「出站收束校验器」（独立会话）。
主助手**本轮没有**调用 `<tool>`，回复里也**没有** `[完成]` / done 类终止哨兵；但对话摘录显示**上一条**很可能是「工具执行成功」的结果。

请判断：在 DEVELOPMENT 任务下，助手本轮的自然语言是否已经**充分收束**用户目标（例如复述了 REPL 打印的答案、概括了 file_read 内容），可以**结束本会话**。

若仍明显缺工具/缺代码提交/需继续排查，则 `allow_session_done` 应为 false，并在 `feedback` 中说明缺什么。

你只允许输出**一个** Markdown JSON 围栏，格式如下（键名固定）：
```json
{
  "allow_session_done": true 或 false,
  "assistant_replacement": "若非空，用其完整替换本轮 assistant 正文；若只需在末尾补 `[完成]` 可写整段替换稿；无需替换则为空字符串",
  "feedback": "当 allow_session_done 为 false 时说明原因（中文）；可为空字符串"
}
```
- JSON 必须能被标准库解析；不要夹杂围栏外的文字。"""


def build_preflight_user(
    *,
    assistant_reply: str,
    mode: TaskMode,
    allowed_tool_names: List[str],
) -> str:
    names = ", ".join(sorted(allowed_tool_names))
    return (
        f"## 任务模式\n{mode.value}\n\n"
        f"## 允许的工具名（须完全一致）\n{names}\n\n"
        "## 待修复的助手原文（请输出替换全文）\n---\n"
        f"{assistant_reply}\n---\n"
    )


def build_outbound_user(
    *,
    assistant_reply: str,
    mode: TaskMode,
    user_goal: str,
    acted: bool,
    transcript_tail: Optional[str] = None,
) -> str:
    base = (
        f"## 任务模式\n{mode.value}\n\n"
        f"## 用户原始目标（摘要）\n{user_goal[:2000]}\n\n"
        f"## 本轮是否已执行工具\nacted={acted}（当前分支为未执行工具却出现完成标记）\n\n"
        "## 待校验的助手原文\n---\n"
        f"{assistant_reply}\n---\n"
    )
    if transcript_tail and transcript_tail.strip():
        base += (
            "\n## 最近对话摘录（含更早轮次与工具结果；辅助判断「完成」是否合理）\n---\n"
            f"{transcript_tail.strip()[:8000]}\n---\n"
        )
    return base


def build_outbound_post_tool_user(
    *,
    assistant_reply: str,
    mode: TaskMode,
    user_goal: str,
    transcript_tail: str,
) -> str:
    return (
        f"## 任务模式\n{mode.value}\n\n"
        f"## 用户原始目标\n{user_goal[:2000]}\n\n"
        "## 最近对话摘录（上一条通常应为工具成功输出；其后为「本轮助手」自然语言收束）\n---\n"
        f"{transcript_tail.strip()[:8000]}\n---\n\n"
        "## 本轮助手自然语言回复（未含 [完成] 哨兵）\n---\n"
        f"{assistant_reply.strip()}\n---\n"
    )


def extract_json_verdict(text: str) -> Optional[Dict[str, Any]]:
    """从修复/校验模型输出中抽出第一个合法 JSON 对象。"""
    for m in _JSON_FENCE_LOOSE.finditer(text):
        body = m.group(1).strip()
        if not body:
            continue
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def summarize_tool_calls(parsed: ParsedOutput) -> str:
    lines: list[str] = []
    for c in parsed.tool_calls:
        lines.append(f"- {c.name}({c.id}): {c.args!r}")
    return "\n".join(lines) if lines else "(无)"


__all__ = [
    "PREFLIGHT_SYSTEM",
    "OUTBOUND_SYSTEM",
    "OUTBOUND_POST_TOOL_SYSTEM",
    "build_preflight_user",
    "build_outbound_user",
    "build_outbound_post_tool_user",
    "extract_json_verdict",
    "summarize_tool_calls",
]
