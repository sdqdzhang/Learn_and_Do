"""CLI entrypoint.

Usage examples
--------------

Code agent:

    python main.py --mode development --role coder \
        --prompt "Write a Python function that reverses a string and prove it works"

Philosophy agent:

    python main.py --mode philosophy --role philosopher \
        --prompt "Does technological progress increase human happiness?"

The entrypoint wires together every layer: LLMClient, SessionManager
(+ Executor), ToolRegistry, SessionContext, TraceLogger, and Workflow.
It is intentionally small — all the interesting behaviour lives in
``core/workflow.py``.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Optional

from core.audit import TraceLogger
from core.exceptions import ConfigurationError, FatalError
from core.schema import AgentRole, TaskMode
from core.session_manager import SessionManager
from core.workflow import Workflow, WorkflowConfig
from memory.session_context import SessionContext
from memory.vector_store import VectorStore
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
    """Return a function that condenses a list of ChatMessage into one string.

    Used by :class:`SessionContext` when its token budget overflows.
    """

    def summarize(messages) -> str:
        prompt = (
            "Compress the following conversation into a concise digest. "
            "Preserve every decision, every conflict, and every file that was created. "
            "Drop greetings and filler. Output plain prose, <300 words."
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
        description="Run a single Agent session (code or philosophy).",
    )
    p.add_argument(
        "--mode",
        choices=[m.value for m in TaskMode],
        default=TaskMode.DEVELOPMENT.value,
        help="Task mode: development or philosophy.",
    )
    p.add_argument(
        "--role",
        choices=[r.value for r in AgentRole],
        default=AgentRole.CODER.value,
        help="Initial agent role for the conversation.",
    )
    p.add_argument(
        "--prompt",
        required=True,
        help="The user prompt that kicks off the session.",
    )
    p.add_argument(
        "--max-turns",
        type=int,
        default=None,
        help="Override WORKFLOW_MAX_TURNS env for this run.",
    )
    p.add_argument(
        "--no-search", action="store_true", help="Disable the web_search tool."
    )
    p.add_argument(
        "--no-rag", action="store_true", help="Disable RAG tools (vector store)."
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
        print(f"failed to start session: {exc}", file=sys.stderr)
        return 2

    trace = TraceLogger(session_id=session_id)

    tools = ToolRegistry()
    _register_tools(
        tools,
        executor=session.executor,
        enable_search=not args.no_search,
        enable_rag=not args.no_rag,
    )

    context = SessionContext()
    context.set_summarizer(_build_summarizer(llm))

    workflow_config = WorkflowConfig(
        max_turns=args.max_turns
        if args.max_turns is not None
        else WorkflowConfig.from_env(role=role).max_turns,
        role=role,
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
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    finally:
        trace.close()
        session.stop()

    print("=" * 60)
    print(f"session_id : {result.session_id}")
    print(f"final_state: {result.final_state.value}")
    print(f"turns      : {result.turns}")
    if result.error:
        print(f"error      : {result.error}")
    if result.last_message:
        print("-" * 60)
        print(result.last_message)
    print("=" * 60)

    return 0 if result.final_state.value == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
