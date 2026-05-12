"""Core Agent: ReAct loop with cooperative pause for tool approval.

Approval model:
  - Each tool has a `permission` level (safe / confirm / dangerous).
  - When the LLM emits a tool call:
      * safe          → execute immediately
      * confirm       → check session "always_allow" set; if absent, yield an ApprovalRequest event
                        and wait for the caller to pass a decision back into `step()`.
      * dangerous     → always emit ApprovalRequest (never auto-allow).
  - Caller drives the loop via `run()` which is an async generator yielding events:
      AssistantTextEvent, ToolCallEvent, ApprovalRequest, ToolResultEvent, FinalEvent
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

from app.agent.core.types import LLMResponse, Message, ToolCall
from app.agent.core import session as session_db
from app.agent.llm.base import BaseLLM
from app.agent.observability.logger import get_logger
from app.agent.prompts import load_system_prompt
from app.agent.tools.registry import registry

log = get_logger("agent.loop")


# ===== Events =====
@dataclass
class AssistantTextEvent:
    text: str
    type: Literal["assistant_text"] = "assistant_text"


@dataclass
class AssistantDeltaEvent:
    """Token-level streaming chunk."""
    delta: str
    type: Literal["assistant_delta"] = "assistant_delta"


@dataclass
class StepStartEvent:
    step: int
    type: Literal["step_start"] = "step_start"


@dataclass
class UsageEvent:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    type: Literal["usage"] = "usage"


@dataclass
class ToolCallEvent:
    call_id: str
    name: str
    arguments: dict[str, Any]
    type: Literal["tool_call"] = "tool_call"


@dataclass
class ApprovalRequest:
    call_id: str
    tool_name: str
    arguments: dict[str, Any]
    summary: str
    permission: str
    type: Literal["approval_request"] = "approval_request"


@dataclass
class ToolResultEvent:
    call_id: str
    name: str
    ok: bool
    result: Any
    type: Literal["tool_result"] = "tool_result"


@dataclass
class FinalEvent:
    text: str
    steps: int
    type: Literal["final"] = "final"


@dataclass
class ApprovalDecision:
    call_id: str
    decision: Literal["allow", "always_allow_tool", "deny"]
    reason: str | None = None


# ===== Agent =====
@dataclass
class AgentState:
    session_id: str
    messages: list[Message] = field(default_factory=list)
    always_allow: set[str] = field(default_factory=set)  # tool names auto-approved this session
    step: int = 0


class Agent:
    def __init__(self, llm: BaseLLM, max_steps: int = 20):
        self.llm = llm
        self.max_steps = max_steps

    async def _ensure_system(self, state: AgentState) -> None:
        if state.messages and state.messages[0].role == "system":
            return
        state.messages.insert(0, Message(role="system", content=load_system_prompt()))

    async def _execute_tool(self, call: ToolCall) -> tuple[bool, Any]:
        fn = registry.func(call.name)
        if not fn:
            return False, {"error": f"Unknown tool: {call.name}"}
        try:
            res = await fn(call.arguments)
            return True, res
        except Exception as e:  # noqa: BLE001
            log.exception("tool_failed", tool=call.name)
            return False, {"error": f"{type(e).__name__}: {e}"}

    async def run(
        self,
        state: AgentState,
        user_input: str,
        approval_inbox: "ApprovalInbox",
    ) -> AsyncIterator[Any]:
        """Drive a single user turn. Yields events; consumer awaits each."""
        await self._ensure_system(state)

        user_msg = Message(role="user", content=user_input)
        state.messages.append(user_msg)
        await session_db.append_message(state.session_id, user_msg)

        tools = registry.schemas()

        for _ in range(self.max_steps):
            state.step += 1
            yield StepStartEvent(step=state.step)
            await session_db.log_trace(state.session_id, state.step, "llm_request",
                                       {"model": self.llm.model, "n_messages": len(state.messages)})

            # Stream LLM response (falls back to single-shot via BaseLLM.stream default)
            deltas = []
            async for d in self.llm.stream(state.messages, tools=tools):
                deltas.append(d)
                if d.text_delta:
                    yield AssistantDeltaEvent(delta=d.text_delta)

            resp: LLMResponse = self.llm.assemble(deltas)
            assistant_msg = resp.message
            state.messages.append(assistant_msg)
            await session_db.append_message(state.session_id, assistant_msg)
            await session_db.log_trace(state.session_id, state.step, "llm_response",
                                       {"finish": resp.finish_reason, "usage": resp.usage.model_dump(),
                                        "tool_calls": [tc.name for tc in assistant_msg.tool_calls]})

            if resp.usage.total_tokens:
                yield UsageEvent(**resp.usage.model_dump())

            if not assistant_msg.tool_calls:
                yield FinalEvent(text=assistant_msg.content, steps=state.step)
                return

            # Process tool calls sequentially
            for tc in assistant_msg.tool_calls:
                tool = registry.get(tc.name)
                yield ToolCallEvent(call_id=tc.id, name=tc.name, arguments=tc.arguments)

                needs_approval = tool is None or tool.permission != "safe"
                if tool and tool.permission == "confirm" and tc.name in state.always_allow:
                    needs_approval = False

                if needs_approval and tool is not None:
                    summary = tool.summarize(tc.arguments) if tool.summarize else ""
                    approval_inbox.open(tc.id)  # register future BEFORE yielding to avoid race
                    yield ApprovalRequest(
                        call_id=tc.id, tool_name=tc.name, arguments=tc.arguments,
                        summary=summary, permission=tool.permission,
                    )
                    decision = await approval_inbox.wait(tc.id)
                    if decision.decision == "deny":
                        result = {"error": "User denied this tool call.",
                                  "user_reason": decision.reason or ""}
                        ok = False
                    else:
                        if decision.decision == "always_allow_tool" and tool.permission == "confirm":
                            state.always_allow.add(tc.name)
                        ok, result = await self._execute_tool(tc)
                else:
                    ok, result = await self._execute_tool(tc)

                tool_msg = Message(
                    role="tool", tool_call_id=tc.id, name=tc.name,
                    content=_serialize_result(result),
                )
                state.messages.append(tool_msg)
                await session_db.append_message(state.session_id, tool_msg)
                await session_db.log_trace(state.session_id, state.step, "tool_result",
                                           {"name": tc.name, "ok": ok})
                yield ToolResultEvent(call_id=tc.id, name=tc.name, ok=ok, result=result)

        yield FinalEvent(text="(reached max steps)", steps=state.step)


def _serialize_result(result: Any) -> str:
    import json
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(result)


# ===== Approval inbox: bridges API layer ↔ agent loop =====
class ApprovalInbox:
    """Per-session pending approvals; the API layer fulfills them."""

    def __init__(self):
        import asyncio
        self._futs: dict[str, asyncio.Future[ApprovalDecision]] = {}
        self._loop = asyncio.get_event_loop()

    def open(self, call_id: str):
        import asyncio
        self._futs[call_id] = asyncio.get_event_loop().create_future()

    async def wait(self, call_id: str) -> ApprovalDecision:
        if call_id not in self._futs:
            self.open(call_id)
        try:
            return await self._futs[call_id]
        finally:
            self._futs.pop(call_id, None)

    def resolve(self, decision: ApprovalDecision) -> bool:
        fut = self._futs.get(decision.call_id)
        if fut and not fut.done():
            fut.set_result(decision)
            return True
        return False

    def cancel_all(self) -> None:
        """Deny every pending approval (used when user cancels the turn)."""
        for cid, fut in list(self._futs.items()):
            if not fut.done():
                fut.set_result(ApprovalDecision(call_id=cid, decision="deny", reason="cancelled"))
