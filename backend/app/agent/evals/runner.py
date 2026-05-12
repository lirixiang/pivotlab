"""Eval runner: replay golden questions, compute pass/fail + token cost.

For each question we run the agent end-to-end with auto-approval for ALL tools
(eval mode bypasses the human confirm step).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from app.agent.config import get_settings
from app.agent.core import session as session_db
from app.agent.core.loop import (
    Agent, AgentState, ApprovalDecision, ApprovalInbox, ApprovalRequest,
    AssistantDeltaEvent, FinalEvent, ToolCallEvent, ToolResultEvent, UsageEvent,
)
from app.agent.evals.golden import load_golden
from app.agent.llm.factory import build_llm
from app.agent.observability.logger import configure_logging, get_logger

log = get_logger("agent.evals")


class AutoApproveInbox(ApprovalInbox):
    """Auto-approves every tool call (eval mode)."""
    def __init__(self):
        super().__init__()

    async def wait(self, call_id: str):
        return ApprovalDecision(call_id=call_id, decision="allow")


async def _run_one(provider: str, model: str, q: dict) -> dict:
    llm = build_llm(provider, model)
    agent = Agent(llm)
    sid = await session_db.create_session(title=f"eval/{q['id']}", provider=provider, model=model)
    state = AgentState(session_id=sid)
    inbox = AutoApproveInbox()

    final_text = ""
    tools_used: list[str] = []
    tokens = {"prompt": 0, "completion": 0, "total": 0}
    t0 = time.time()
    full_text_parts: list[str] = []

    async for ev in agent.run(state, q["prompt"], inbox):
        if isinstance(ev, AssistantDeltaEvent):
            full_text_parts.append(ev.delta)
        elif isinstance(ev, ToolCallEvent):
            tools_used.append(ev.name)
        elif isinstance(ev, ToolResultEvent):
            pass
        elif isinstance(ev, UsageEvent):
            tokens["prompt"] += ev.prompt_tokens
            tokens["completion"] += ev.completion_tokens
            tokens["total"] += ev.total_tokens
        elif isinstance(ev, FinalEvent):
            final_text = ev.text or "".join(full_text_parts)

    final_text = final_text or "".join(full_text_parts)
    elapsed = time.time() - t0

    expect = [s.lower() for s in (q.get("expect") or [])]
    expect_tools = set(q.get("expect_tools") or [])
    text_lower = final_text.lower()

    text_pass = all(s in text_lower for s in expect)
    tools_pass = expect_tools.issubset(set(tools_used))
    passed = text_pass and tools_pass

    return {
        "id": q["id"],
        "prompt": q["prompt"],
        "passed": passed,
        "text_pass": text_pass,
        "tools_pass": tools_pass,
        "tools_used": tools_used,
        "expect_tools": list(expect_tools),
        "tokens": tokens,
        "elapsed_sec": round(elapsed, 2),
        "session_id": sid,
        "final_excerpt": final_text[:300],
    }


async def main_async(provider: str, model: str, ids: list[str] | None, out: Path | None):
    configure_logging()
    await session_db.init_db()
    questions = load_golden()
    if ids:
        questions = [q for q in questions if q["id"] in ids]

    results = []
    for q in questions:
        log.info("eval_run", id=q["id"])
        try:
            r = await _run_one(provider, model, q)
        except Exception as e:  # noqa: BLE001
            log.exception("eval_failed", id=q["id"])
            r = {"id": q["id"], "passed": False, "error": str(e)}
        results.append(r)
        marker = "✅" if r.get("passed") else "❌"
        print(f"{marker} {r['id']:<8} tools={r.get('tools_used')} tokens={r.get('tokens', {}).get('total')}")

    summary = {
        "provider": provider, "model": model,
        "total": len(results),
        "passed": sum(1 for r in results if r.get("passed")),
        "results": results,
    }
    print(f"\n=== {summary['passed']}/{summary['total']} passed ===")
    if out:
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote: {out}")


def main():
    s = get_settings()
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default=s.llm_default_provider)
    ap.add_argument("--model", default=s.llm_default_model)
    ap.add_argument("--ids", nargs="*")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    asyncio.run(main_async(args.provider, args.model, args.ids, args.out))


if __name__ == "__main__":
    main()
