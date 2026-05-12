"""Plan tool — let the agent show task progress to the user.

When invoked, this tool returns the steps payload back through tool_result.
The router watches for this tool name and emits an additional `plan_update` SSE
event so the frontend can render a progress card.
"""
from __future__ import annotations

from typing import Any

from app.agent.tools.registry import registry


_VALID_STATUSES = {"not-started", "in-progress", "completed"}


@registry.register(
    name="update_plan",
    description=(
        "**任务规划工具**。在开始执行多步骤任务时调用，向用户展示执行计划与进度。"
        "复杂任务（预计需要 ≥ 3 次工具调用，如选股流水线、综合分析、跨数据源整合）"
        "**必须在第一步**调用此工具列出 2-6 个步骤；"
        "每完成一步、或开始新一步时，**重新调用**此工具更新所有步骤的 status。"
        "简单单一查询（如\"600519 现价多少\"）不要调用此工具。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "description": "完整的步骤列表（每次调用都要传完整列表，不是增量）",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer", "description": "1-based step number"},
                        "title": {"type": "string", "description": "短描述（5-15字，动词开头）"},
                        "status": {
                            "type": "string",
                            "enum": ["not-started", "in-progress", "completed"],
                            "description": "当前状态（同一时刻最多一个 in-progress）",
                        },
                    },
                    "required": ["id", "title", "status"],
                },
            },
        },
        "required": ["steps"],
    },
    permission="safe",
)
async def update_plan(args: dict[str, Any]) -> dict[str, Any]:
    raw_steps = args.get("steps") or []
    if not isinstance(raw_steps, list) or not raw_steps:
        return {"error": "steps must be a non-empty list"}

    cleaned: list[dict] = []
    for i, st in enumerate(raw_steps, start=1):
        if not isinstance(st, dict):
            continue
        sid = int(st.get("id") or i)
        title = str(st.get("title") or "").strip()
        status = str(st.get("status") or "not-started").strip()
        if status not in _VALID_STATUSES:
            status = "not-started"
        if not title:
            continue
        cleaned.append({"id": sid, "title": title, "status": status})

    if not cleaned:
        return {"error": "no valid steps after cleaning"}

    # Hard cap to protect UI / token budget. If LLM exceeds it, keep first N
    # and append a synthetic "..." step so it's obvious things were truncated.
    HARD_CAP = 20
    truncated = False
    if len(cleaned) > HARD_CAP:
        cleaned = cleaned[:HARD_CAP]
        truncated = True

    in_progress = [s for s in cleaned if s["status"] == "in-progress"]
    if len(in_progress) > 1:
        # Keep only the first in-progress, rest revert to not-started
        kept = False
        for s in cleaned:
            if s["status"] == "in-progress":
                if kept:
                    s["status"] = "not-started"
                else:
                    kept = True

    completed = sum(1 for s in cleaned if s["status"] == "completed")
    out: dict[str, Any] = {
        "ok": True,
        "steps": cleaned,
        "summary": f"{completed}/{len(cleaned)} 步已完成",
    }
    if truncated:
        out["warning"] = (
            f"步骤超过上限 {HARD_CAP}，已截断。请将相邻小步骤合并，控制在 10 步内。"
        )
    return out
