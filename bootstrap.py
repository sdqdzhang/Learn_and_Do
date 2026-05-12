"""CLI 与 WebSocket 服务共用的 Agent 组装逻辑（避免重复）。"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from core.schema import AgentRole, TaskMode
from memory.session_context import SessionContext
from memory.vector_store import VectorStore
from tools.file_io import FileListTool, FileReadTool, FileWriteTool
from tools.rag import RagQueryTool, RagUpsertTool
from tools.registry import ToolRegistry
from tools.repl import PythonReplTool
from tools.search import WebSearchTool
from utils.llm_client import LLMClient

logger = logging.getLogger(__name__)


def build_summarizer(client: LLMClient) -> Callable:
    def summarize(messages) -> str:
        prompt = (
            "请把下面这段对话压缩成一份简洁的摘要。"
            "必须完整保留：每一个已做出的决定、每一个出现过的冲突、"
            "以及每一个被创建或修改的文件。"
            "请省略问候语和与任务无关的寒暄。"
            "输出纯文本叙述，控制在 300 字以内，使用中文。"
        )
        flat = "\n\n".join(f"[{m.role.value}] {m.content}" for m in messages)
        return client.chat(
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": flat},
            ]
        )

    return summarize


def register_tools(
    registry: ToolRegistry,
    *,
    executor,
    enable_search: bool,
    enable_rag: bool,
) -> None:
    registry.register(FileReadTool())
    registry.register(FileWriteTool())
    registry.register(FileListTool())

    repl = PythonReplTool()
    repl.bind_executor(executor)
    registry.register(repl)

    if enable_search:
        registry.register(WebSearchTool())

    if enable_rag:
        store = VectorStore()
        registry.register(RagQueryTool(store))
        registry.register(RagUpsertTool(store))
