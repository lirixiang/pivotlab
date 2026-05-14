"""delegate: spawn a sub-agent to handle a focused sub-task.

The main agent calls this tool when it wants to offload a complex,
self-contained sub-task (e.g., "analyze LHB seat patterns for 601991")
to a child agent that runs independently and returns a summary.

The sub-agent:
  - shares the same LLM (no extra config)
  - gets its own message history (won't pollute main context)
  - can use all safe-permission tools (query_db, read_file, etc.)
  - is limited to fewer steps (default 10) to avoid runaway loops
  - cannot spawn further sub-agents (no recursion)
"""
from __future__ import annotations

import json
import uuid
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.core.loop import Agent, AgentState, ApprovalInbox, FinalEvent, ToolResultEvent

from app.agent.core.types import Message
from app.agent.llm.factory import build_llm
from app.agent.tools.registry import registry


# Sentinel to prevent infinite recursion
_IN_SUBAGENT = False
_SUBAGENT_MAX_STEPS = 10


@registry.register(
    name="delegate",
    description=(
        "将一个独立的子任务委派给 sub-agent 执行。sub-agent 拥有与你相同的工具和数据库访问权限，"
        "但有独立的上下文窗口，适合处理需要多步工具调用的聚焦任务，例如：\n"
        "- 深入分析某只股票的龙虎榜席位\n"
        "- 对比多只股票的财务指标\n"
        "- 扫描数据库回答一个复杂的数据问题\n"
        "sub-agent 完成后会返回一段文字总结。\n"
        "注意：不要用于简单的单步查询（直接自己调 query_db 更快）。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "对子任务的完整描述，包含所有必要上下文（股票代码、时间范围、分析目标等）",
            },
            "context": {
                "type": "string",
                "description": "可选：从主对话中摘取的关键背景信息，帮助 sub-agent 理解上下文",
            },
        },
        "required": ["task"],
    },
    permission="safe",
)
async def delegate(args: dict[str, Any]) -> dict[str, Any]:
    global _IN_SUBAGENT

    if _IN_SUBAGENT:
        return {"error": "sub-agent 不能再嵌套 sub-agent"}

    task = args["task"]
    context = args.get("context", "")

    _IN_SUBAGENT = True
    try:
        return await _run_subagent(task, context)
    finally:
        _IN_SUBAGENT = False


async def _run_subagent(task: str, context: str) -> dict[str, Any]:
    """Run a child agent loop and collect the final answer."""
    from app.agent.config import get_settings
    from app.agent.core.loop import Agent, AgentState, ApprovalInbox, FinalEvent, ToolResultEvent

    settings = get_settings()
    llm = build_llm(provider=settings.llm_default_provider, model=settings.llm_default_model)

    agent = Agent(llm=llm, max_steps=_SUBAGENT_MAX_STEPS)
    state = AgentState(session_id=f"sub_{uuid.uuid4().hex[:12]}")

    # Build the user prompt for the sub-agent
    prompt_parts = []
    if context:
        prompt_parts.append(f"背景信息：\n{context}\n")
    prompt_parts.append(f"请完成以下任务：\n{task}")
    user_input = "\n".join(prompt_parts)

    # Auto-approve everything (sub-agent only uses safe tools anyway)
    inbox = ApprovalInbox()

    tool_calls_made = []
    final_text = ""

    async for event in agent.run(state, user_input, inbox):
        if isinstance(event, ToolResultEvent):
            tool_calls_made.append(event.name)
        elif isinstance(event, FinalEvent):
            final_text = event.text

    return {
        "result": final_text,
        "steps_used": state.step,
        "tools_called": tool_calls_made,
    }
