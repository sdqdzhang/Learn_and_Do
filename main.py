"""命令行入口。

使用示例
--------

代码 Agent：

    python main.py --mode development --role coder \
        --prompt "写一个反转字符串的 Python 函数，并用单测证明它能跑"

哲学 Agent：

    python main.py --mode philosophy --role philosopher \
        --prompt "技术进步是否真的提升了人类的幸福感？"

入口脚本只负责把各层拼起来：LLMClient、SessionManager（含 Executor）、
ToolRegistry、SessionContext、TraceLogger、Workflow。它刻意保持精简 ——
所有"有意思的行为"都在 ``core/workflow.py`` 里。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import replace
from typing import Optional

from core.audit import TraceLogger
from core.exceptions import ConfigurationError, FatalError
from core.schema import AgentRole, TaskMode
from core.session_manager import SessionManager
from core.workflow import Workflow, WorkflowConfig
from memory.session_context import SessionContext
from memory.vector_store import VectorStore
from runtime.stream_tunnel import default_tunnel
from tools.file_io import FileListTool, FileReadTool, FileWriteTool
from tools.rag import RagQueryTool, RagUpsertTool
from tools.registry import ToolRegistry
from tools.repl import PythonReplTool
from tools.search import WebSearchTool
from utils.llm_client import LLMClient


def _setup_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _build_summarizer(client: LLMClient):
    """返回一个把 List[ChatMessage] 浓缩成单条文本的函数。

    :class:`SessionContext` 在 token 预算溢出时会调用它。
    """

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


def _register_tools(
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


def _parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="tiny-devin",
        description="运行一次单 session 的 Agent 会话（代码模式或哲学模式）。",
    )
    p.add_argument(
        "--mode",
        choices=[m.value for m in TaskMode],
        default=TaskMode.DEVELOPMENT.value,
        help="任务模式：development（代码开发）或 philosophy（哲学研究）。",
    )
    p.add_argument(
        "--role",
        choices=[r.value for r in AgentRole],
        default=AgentRole.CODER.value,
        help="本次对话初始的 Agent 角色。",
    )
    p.add_argument(
        "--prompt",
        required=True,
        help="启动整个 session 的用户指令。",
    )
    p.add_argument(
        "--max-turns",
        type=int,
        default=None,
        help="覆盖环境变量 WORKFLOW_MAX_TURNS，仅对本次运行生效。",
    )
    p.add_argument(
        "--no-search",
        action="store_true",
        help="禁用 web_search 工具（不访问外网）。",
    )
    p.add_argument(
        "--no-rag",
        action="store_true",
        help="禁用 RAG 工具（不加载向量库）。",
    )
    return p.parse_args(argv)


def main(argv: Optional[list] = None) -> int:
    _setup_logging()
    args = _parse_args(argv)

    mode = TaskMode(args.mode)
    role = AgentRole(args.role)

    llm = LLMClient()

    session = SessionManager()
    try:
        session_id = session.start()
    except FatalError as exc:
        print(f"启动 session 失败：{exc}", file=sys.stderr)
        return 2

    context = SessionContext()
    context.set_summarizer(_build_summarizer(llm))

    tools = ToolRegistry()
    _register_tools(
        tools,
        executor=session.executor,
        enable_search=not args.no_search,
        enable_rag=not args.no_rag,
    )

    trace = TraceLogger(session_id=session_id, context=context)
    default_tunnel.register(session_id, session_id)

    base_wf = WorkflowConfig.from_env(role=role)
    workflow_config = replace(
        base_wf,
        max_turns=args.max_turns if args.max_turns is not None else base_wf.max_turns,
    )

    workflow = Workflow(
        llm=llm,
        tools=tools,
        context=context,
        trace=trace,
        mode=mode,
        config=workflow_config,
    )

    try:
        result = workflow.run(args.prompt)
    except ConfigurationError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2
    finally:
        default_tunnel.unregister(session_id)
        trace.close()
        session.stop()

    print("=" * 60)
    print(f"会话 ID    ：{result.session_id}")
    print(f"最终状态   ：{result.final_state.value}")
    print(f"轮次       ：{result.turns}")
    if result.error:
        print(f"错误信息   ：{result.error}")
    if result.last_message:
        print("-" * 60)
        print(result.last_message)
    print("=" * 60)

    return 0 if result.final_state.value == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
