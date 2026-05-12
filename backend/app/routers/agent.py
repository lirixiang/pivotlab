"""Agent router: SSE streaming chat + REST endpoints.

Protocol (SSE events):
  event: step_start     data: {"step": 1}
  event: thinking_delta data: {"delta": "..."}
  event: delta          data: {"delta": "..."}
  event: tool_call      data: {"call_id": "...", "name": "...", "arguments": {...}}
  event: tool_result    data: {"call_id": "...", "ok": true, "result": ...}
  event: approval       data: {"call_id": "...", "tool_name": "...", "arguments": {...}, "permission": "confirm"}
  event: usage          data: {"prompt_tokens": ..., "completion_tokens": ..., "total_tokens": ...}
  event: done           data: {"text": "...", "steps": 2}
  event: cancelled      data: {}
  event: error          data: {"error": "..."}

Approval flow (POST /api/agent/approve):
  Since SSE is server→client only, approvals are sent via a separate POST.
  The agent loop awaits the approval future; the POST resolves it.

Cancel (POST /api/agent/cancel/{sid}):
  Cancels the running agent turn for the given session.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, is_dataclass
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agent.config import get_settings
from app.agent.core import session as session_db
from app.agent.core.loop import (
    Agent, AgentState, ApprovalDecision, ApprovalInbox,
    FinalEvent, AssistantDeltaEvent, StepStartEvent,
    ToolCallEvent, ToolResultEvent, ApprovalRequest, UsageEvent,
)
from app.agent.llm.factory import build_llm, list_available_providers
from app.agent.observability.logger import get_logger
from app.agent.tools import registry as _registry_module  # noqa: F401 — auto-registers tools
from app.agent.tools.registry import registry as tool_registry

log = get_logger("agent.router")
router = APIRouter(prefix="/api/agent", tags=["agent"])

# In-memory state
_states: dict[str, AgentState] = {}
_inboxes: dict[str, ApprovalInbox] = {}
_run_tasks: dict[str, asyncio.Task] = {}


def _get_inbox(sid: str) -> ApprovalInbox:
    if sid not in _inboxes:
        _inboxes[sid] = ApprovalInbox()
    return _inboxes[sid]


async def _get_state(sid: str) -> AgentState:
    if sid not in _states:
        msgs = await session_db.load_messages(sid)
        _states[sid] = AgentState(session_id=sid, messages=msgs)
    return _states[sid]


def _event_to_sse(ev: Any) -> str:
    if is_dataclass(ev):
        d = asdict(ev)
    else:
        d = {"type": "unknown", "data": str(ev)}
    etype = d.pop("type", "message")
    return f"event: {etype}\ndata: {json.dumps(d, ensure_ascii=False, default=str)}\n\n"


# ===== REST endpoints =====

class CreateSessionReq(BaseModel):
    title: str | None = None
    provider: str | None = None
    model: str | None = None


class ApproveReq(BaseModel):
    session_id: str
    call_id: str
    decision: str = "allow"  # allow | always_allow_tool | deny
    reason: str | None = None


class ChatReq(BaseModel):
    text: str


@router.get("/providers")
async def providers():
    s = get_settings()
    return {
        "providers": list_available_providers(),
        "default_provider": s.llm_default_provider,
        "default_model": s.llm_default_model,
    }


@router.get("/tools")
async def tools():
    return {"tools": [
        {"name": t.name, "description": t.description, "permission": t.permission}
        for t in tool_registry.all()
    ]}


@router.get("/sessions")
async def list_sessions(q: str | None = None):
    if q and q.strip():
        return {"sessions": await session_db.search_sessions(q.strip())}
    return {"sessions": await session_db.list_sessions()}


@router.post("/sessions")
async def create_session(req: CreateSessionReq):
    s = get_settings()
    provider = req.provider or s.llm_default_provider
    model = req.model or s.llm_default_model
    sid = await session_db.create_session(req.title, provider, model)
    return {"session_id": sid, "provider": provider, "model": model}


@router.get("/sessions/{sid}/messages")
async def get_messages(sid: str):
    msgs = await session_db.load_messages(sid)
    return {"messages": [m.model_dump() for m in msgs]}


@router.post("/chat/{sid}")
async def chat_sse(sid: str, req: ChatReq, request: Request):
    """Send a message and stream the agent response via SSE."""
    text_in = req.text.strip()
    if not text_in:
        raise HTTPException(400, "empty message")

    sess_list = await session_db.list_sessions(limit=200)
    sess_map = {s["id"]: s for s in sess_list}
    if sid not in sess_map:
        raise HTTPException(404, "session not found")

    meta = sess_map[sid]
    s = get_settings()
    provider = meta.get("llm_provider") or s.llm_default_provider
    model = meta.get("llm_model") or s.llm_default_model

    try:
        llm = build_llm(provider, model)
    except Exception as e:
        raise HTTPException(500, str(e))

    agent = Agent(llm, max_steps=s.agent_max_steps)
    state = await _get_state(sid)
    inbox = _get_inbox(sid)

    async def generate():
        try:
            task = asyncio.current_task()
            _run_tasks[sid] = task
            async for ev in agent.run(state, text_in, inbox):
                # Check if client disconnected
                if await request.is_disconnected():
                    break
                yield _event_to_sse(ev)
                # Synthetic plan_update event: when update_plan tool succeeds,
                # also emit a plan_update SSE so the frontend can render the plan card.
                if (isinstance(ev, ToolResultEvent) and ev.name == "update_plan"
                        and ev.ok and isinstance(ev.result, dict)):
                    steps = ev.result.get("steps")
                    if steps:
                        yield _event_to_sse_raw("plan_update", {"steps": steps})
                if isinstance(ev, FinalEvent):
                    break
        except asyncio.CancelledError:
            yield _event_to_sse_raw("cancelled", {})
        except Exception as e:
            log.exception("agent_run_failed", sid=sid)
            yield _event_to_sse_raw("error", {"error": f"{type(e).__name__}: {e}"})
        finally:
            _run_tasks.pop(sid, None)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx buffering
        },
    )


@router.post("/approve")
async def approve(req: ApproveReq):
    """Resolve a pending tool approval."""
    inbox = _inboxes.get(req.session_id)
    if not inbox:
        raise HTTPException(404, "no active session")
    ok = inbox.resolve(ApprovalDecision(
        call_id=req.call_id,
        decision=req.decision,
        reason=req.reason,
    ))
    return {"ok": ok}


@router.post("/cancel/{sid}")
async def cancel(sid: str):
    """Cancel the running agent turn."""
    task = _run_tasks.get(sid)
    if task and not task.done():
        task.cancel()
        inbox = _inboxes.get(sid)
        if inbox:
            inbox.cancel_all()
        return {"cancelled": True}
    return {"cancelled": False}


def _event_to_sse_raw(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
