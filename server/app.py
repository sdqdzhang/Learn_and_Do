"""FastAPI + WebSocket：实时推送 TraceEvent，并接收人类干预指令。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Optional

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> None:
        return None

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from bootstrap import build_summarizer, register_tools
from server.start_payload import (
    build_context_config,
    build_llm_config,
    build_session_manager,
    merge_workflow_config,
    public_server_defaults,
    resolve_intervention,
    summarizer_model_from_start,
)
from core.audit import KIND_FINAL_SUMMARY, TraceLogger
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

# 相对仓库根目录：WebSocket 会话轨迹写入此目录（JSONL，可前端回放）。
REPO_ROOT = Path(__file__).resolve().parent.parent
SESSION_TRACE_DIR = REPO_ROOT / "log"
_TRACE_NAME_RE = re.compile(r"^[a-zA-Z0-9._-]+\.jsonl$")

app = FastAPI(title="Tiny-Devin Live Session", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _coerce_max_turns(raw: Any, fallback: int) -> int:
    if raw is None:
        return fallback
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return fallback
    return max(1, min(v, 500))


def _build_final_summary(
    *,
    llm: LLMClient,
    context: SessionContext,
    prompt: str,
    result_state: str,
    turns: int,
    last_message: Optional[str],
    error: Optional[str],
) -> str:
    """用当前模型把本次会话压成一段给用户看的最终结果。"""
    transcript_lines: list[str] = []
    for m in context.messages()[-20:]:
        content = (m.content or "").strip()
        if len(content) > 1600:
            content = content[:1600] + "..."
        transcript_lines.append(f"[{m.role.value}]\n{content}")
    system = (
        "你是一个会话收尾总结器。请根据 agent 的执行轨迹，输出给最终用户看的中文结果。"
        "要求：1) 先说明最终状态；2) 总结已经完成了什么；3) 如有文件、工具输出、失败原因或后续建议，"
        "用简洁条目列出；4) 不要编造轨迹中没有的信息。"
    )
    user = (
        f"用户原始任务：{prompt}\n"
        f"最终状态：{result_state}\n"
        f"轮数：{turns}\n"
        f"错误：{error or '无'}\n"
        f"最后一条 assistant 消息：\n{last_message or '无'}\n\n"
        "最近上下文：\n"
        + "\n\n".join(transcript_lines)
    )
    summary = llm.chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.2,
        max_tokens=900,
    ).strip()
    return summary or (last_message or error or "会话已结束，但模型未返回总结。")


@app.get("/api/settings")
def server_settings() -> dict[str, Any]:
    """返回当前进程从环境读取的默认配置（供前端设置页对齐 / 同步）。"""
    return {"defaults": public_server_defaults()}


@app.get("/api/traces")
def list_session_traces() -> list[dict[str, Any]]:
    """列出 ``log/*.jsonl`` 会话轨迹（按修改时间新到旧）。"""
    SESSION_TRACE_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for p in sorted(SESSION_TRACE_DIR.glob("*.jsonl"), key=lambda x: x.stat().st_mtime, reverse=True):
        st = p.stat()
        rows.append({"name": p.name, "size": st.st_size, "mtime": st.st_mtime})
    return rows


@app.get("/api/traces/{name}")
def download_session_trace(name: str) -> FileResponse:
    """下载单条 JSONL 轨迹供前端回放。"""
    safe_name = Path(name).name
    if not _TRACE_NAME_RE.match(safe_name):
        raise HTTPException(status_code=400, detail="invalid trace name")
    path = (SESSION_TRACE_DIR / safe_name).resolve()
    root = SESSION_TRACE_DIR.resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid path") from None
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path, media_type="application/x-ndjson", filename=safe_name)


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
    no_search = bool(start.get("no_search", False))
    no_rag = bool(start.get("no_rag", True))

    loop = asyncio.get_running_loop()
    out_q: asyncio.Queue[Optional[str]] = asyncio.Queue()
    cancel_event = threading.Event()

    def push_line(line: str) -> None:
        try:
            loop.call_soon_threadsafe(out_q.put_nowait, line)
        except RuntimeError:
            pass

    def on_trace(ev: TraceEvent) -> None:
        push_line(ev.model_dump_json())

    errors: list[str] = []

    def worker() -> None:
        session: Optional[SessionManager] = None
        trace: Optional[TraceLogger] = None
        session_id_local = ""
        try:
            llm = LLMClient(build_llm_config(start))
            session = build_session_manager(start)
            session_id_local = session.start()
            context = SessionContext(config=build_context_config(start))
            context.set_summarizer(
                build_summarizer(llm, summarizer_model=summarizer_model_from_start(start)),
            )
            tools = ToolRegistry()
            register_tools(
                tools,
                executor=session.executor,
                enable_search=not no_search,
                enable_rag=not no_rag,
            )
            base_ref = WorkflowConfig.from_env(role=role)
            coerced_max = _coerce_max_turns(start.get("max_turns"), base_ref.max_turns)
            wf_cfg = merge_workflow_config(start, role=role, max_turns=coerced_max)
            wf_cfg, iv_wait = resolve_intervention(start, wf_cfg)
            intervention: Optional[InterventionChannel] = None
            if iv_wait is not None and float(iv_wait) > 0:
                intervention = InterventionChannel()
            SESSION_TRACE_DIR.mkdir(parents=True, exist_ok=True)
            trace = TraceLogger(
                session_id=session_id_local,
                log_dir=str(SESSION_TRACE_DIR),
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
                cancel_event=cancel_event,
            )
            result = workflow.run(prompt)
            final_summary = ""
            try:
                final_summary = _build_final_summary(
                    llm=llm,
                    context=context,
                    prompt=prompt,
                    result_state=result.final_state.value,
                    turns=result.turns,
                    last_message=result.last_message,
                    error=result.error,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("生成最终总结失败：%s", exc)
                final_summary = result.last_message or result.error or "会话已结束，但最终总结生成失败。"
            trace.log(
                KIND_FINAL_SUMMARY,
                {
                    "summary": final_summary,
                    "final_state": result.final_state.value,
                    "turns": result.turns,
                    "error": result.error,
                },
                state=result.final_state,
                turn=result.turns,
                context=context,
            )
            push_line(
                json.dumps(
                    {
                        "type": "done",
                        "session_id": result.session_id,
                        "final_state": result.final_state.value,
                        "turns": result.turns,
                        "error": result.error,
                        "last_message": result.last_message,
                        "final_summary": final_summary,
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
                mtype = str(msg.get("type", ""))
                if mtype == "cancel":
                    cancel_event.set()
                    continue
                if mtype == "human" and intervention is not None:
                    intervention.submit(str(msg.get("text", "")))
        except WebSocketDisconnect:
            cancel_event.set()
            return
        except Exception as exc:  # noqa: BLE001
            logger.debug("recv_human ended: %s", exc)

    recv_task = asyncio.create_task(recv_human())
    try:
        while True:
            line = await out_q.get()
            if line is None:
                break
            try:
                await websocket.send_text(line)
            except WebSocketDisconnect:
                cancel_event.set()
                break
            except RuntimeError as exc:
                # 客户端已断开时，部分 ASGI 实现会抛 RuntimeError 而非 WebSocketDisconnect。
                if "websocket.send" in str(exc) or "websocket.close" in str(exc):
                    cancel_event.set()
                    break
                raise
    finally:
        recv_task.cancel()
        try:
            await recv_task
        except asyncio.CancelledError:
            pass


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
