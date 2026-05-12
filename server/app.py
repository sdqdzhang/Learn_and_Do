"""FastAPI + WebSocket：实时推送 TraceEvent，并接收人类干预指令。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from dataclasses import replace
from typing import Any, Optional

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> None:
        return None

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from bootstrap import build_summarizer, register_tools
from core.audit import TraceLogger
from core.exceptions import FatalError
from core.intervention import InterventionChannel
from core.schema import AgentRole, TaskMode, TraceEvent
from core.session_manager import SessionManager
from core.workflow import Workflow, WorkflowConfig
from memory.session_context import SessionContext
from runtime.stream_tunnel import default_tunnel
from tools.registry import ToolRegistry
from utils.llm_client import LLMClient

logger = logging.getLogger(__name__)

load_dotenv()

app = FastAPI(title="Tiny-Devin Live Session", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.websocket("/ws/session")
async def session_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        start_raw = await websocket.receive_text()
    except WebSocketDisconnect:
        return

    try:
        start = json.loads(start_raw)
    except json.JSONDecodeError:
        await websocket.send_text(json.dumps({"type": "error", "message": "invalid JSON"}))
        await websocket.close()
        return

    if start.get("type") != "start":
        await websocket.send_text(json.dumps({"type": "error", "message": "first frame must be type=start"}))
        await websocket.close()
        return

    prompt = str(start.get("prompt", "")).strip()
    if not prompt:
        await websocket.send_text(json.dumps({"type": "error", "message": "prompt required"}))
        await websocket.close()
        return

    role = AgentRole(str(start.get("role", AgentRole.CODER.value)))
    mode = TaskMode(str(start.get("mode", TaskMode.DEVELOPMENT.value)))
    max_turns = start.get("max_turns")
    no_search = bool(start.get("no_search", False))
    no_rag = bool(start.get("no_rag", True))
    iv_timeout = start.get("intervention_timeout_s")

    loop = asyncio.get_running_loop()
    out_q: asyncio.Queue[Optional[str]] = asyncio.Queue()

    def push_line(line: str) -> None:
        try:
            loop.call_soon_threadsafe(out_q.put_nowait, line)
        except RuntimeError:
            pass

    def on_trace(ev: TraceEvent) -> None:
        push_line(ev.model_dump_json())

    intervention: Optional[InterventionChannel] = None
    base_wf = WorkflowConfig.from_env(role=role)
    if iv_timeout is not None and float(iv_timeout) > 0:
        intervention = InterventionChannel()
        wf_cfg = replace(
            base_wf,
            max_turns=int(max_turns) if max_turns is not None else base_wf.max_turns,
            intervention_enabled=True,
            intervention_timeout_s=float(iv_timeout),
        )
    else:
        wf_cfg = replace(
            base_wf,
            max_turns=int(max_turns) if max_turns is not None else base_wf.max_turns,
        )

    errors: list[str] = []

    def worker() -> None:
        session: Optional[SessionManager] = None
        trace: Optional[TraceLogger] = None
        session_id_local = ""
        try:
            llm = LLMClient()
            session = SessionManager()
            session_id_local = session.start()
            context = SessionContext()
            context.set_summarizer(build_summarizer(llm))
            tools = ToolRegistry()
            register_tools(
                tools,
                executor=session.executor,
                enable_search=not no_search,
                enable_rag=not no_rag,
            )
            trace = TraceLogger(
                session_id=session_id_local,
                context=context,
                on_event=on_trace,
            )
            default_tunnel.register(session_id_local, session_id_local)
            workflow = Workflow(
                llm=llm,
                tools=tools,
                context=context,
                trace=trace,
                mode=mode,
                config=wf_cfg,
                intervention=intervention,
            )
            result = workflow.run(prompt)
            push_line(
                json.dumps(
                    {
                        "type": "done",
                        "session_id": result.session_id,
                        "final_state": result.final_state.value,
                        "turns": result.turns,
                        "error": result.error,
                        "last_message": result.last_message,
                    },
                    ensure_ascii=False,
                )
            )
        except FatalError as exc:
            errors.append(str(exc))
            push_line(json.dumps({"type": "error", "message": str(exc)}, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            logger.exception("session worker failed")
            errors.append(str(exc))
            push_line(
                json.dumps({"type": "error", "message": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)
            )
        finally:
            if trace is not None:
                trace.close()
            if session is not None:
                default_tunnel.unregister(session_id_local)
                session.stop()
            push_line(None)

    threading.Thread(target=worker, name="agent-session", daemon=True).start()

    async def recv_human() -> None:
        try:
            while True:
                raw = await websocket.receive_text()
                msg = json.loads(raw)
                if msg.get("type") == "human" and intervention is not None:
                    intervention.submit(str(msg.get("text", "")))
        except WebSocketDisconnect:
            return
        except Exception as exc:  # noqa: BLE001
            logger.debug("recv_human ended: %s", exc)

    recv_task = asyncio.create_task(recv_human())
    try:
        while True:
            line = await out_q.get()
            if line is None:
                break
            await websocket.send_text(line)
    finally:
        recv_task.cancel()
        try:
            await recv_task
        except asyncio.CancelledError:
            pass


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
