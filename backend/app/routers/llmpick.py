"""/api/llmpick/* — LLM精选: LLM + quantitative validation endpoints."""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine

from ..database import DATABASE_URL
from ..services.llm_pick import (
    call_llm,
    get_available_providers,
    validate_candidates,
    DEFAULT_PROMPT,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/llmpick", tags=["llmpick"])


# ── DB engine (sync) ──
def _sync_url() -> str:
    return (
        str(DATABASE_URL)
        .replace("sqlite+aiosqlite", "sqlite")
        .replace("postgresql+asyncpg", "postgresql+psycopg2")
    )

_engine = None
def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(_sync_url(), echo=False, pool_pre_ping=True)
    return _engine


# ── History persistence ──
_HISTORY_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "llmpick_history"
_HISTORY_DIR.mkdir(parents=True, exist_ok=True)


def _save_history(result: dict):
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = _HISTORY_DIR / f"{ts}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2))


# ── Background task tracking ──
_task_lock = threading.Lock()
_current_task: dict | None = None


# ── Request/Response models ──
class ValidateRequest(BaseModel):
    candidates: list[dict] = Field(..., description="List of {code, name?, logic?, risk?, theme?}")
    pe_max_pctile: float = Field(0.80, ge=0, le=1)
    crowding_max_pctile: float = Field(0.90, ge=0, le=1)
    require_above_ma20: bool = True
    require_positive_flow: bool = True


class GenerateRequest(BaseModel):
    provider: str = "deepseek"
    prompt: str = ""
    auto_validate: bool = True
    pe_max_pctile: float = 0.80
    crowding_max_pctile: float = 0.90
    require_above_ma20: bool = True
    require_positive_flow: bool = True


# ── Endpoints ──

@router.get("/providers")
def list_providers():
    """List available LLM providers and their configuration status."""
    return {"providers": get_available_providers()}


@router.post("/validate")
def validate(req: ValidateRequest):
    """Validate a list of stock candidates with quantitative filters."""
    if not req.candidates:
        raise HTTPException(400, "candidates不能为空")
    if len(req.candidates) > 50:
        raise HTTPException(400, "最多支持50只股票")

    engine = _get_engine()
    results = validate_candidates(
        engine, req.candidates,
        pe_max_pctile=req.pe_max_pctile,
        crowding_max_pctile=req.crowding_max_pctile,
        require_above_ma20=req.require_above_ma20,
        require_positive_flow=req.require_positive_flow,
    )

    passed = [r for r in results if r["passed"]]
    resp = {
        "total": len(results),
        "passed": len(passed),
        "filtered": len(results) - len(passed),
        "results": results,
    }
    _save_history({"mode": "validate", "ts": time.time(), **resp})
    return resp


@router.post("/generate")
def generate(req: GenerateRequest):
    """Call LLM to generate candidates, then optionally validate."""
    global _current_task

    with _task_lock:
        if _current_task and _current_task.get("status") == "running":
            raise HTTPException(409, "已有任务运行中")
        _current_task = {
            "status": "running",
            "provider": req.provider,
            "started_at": time.time(),
            "message": "正在调用大模型...",
        }

    try:
        # Step 1: Call LLM
        llm_result = call_llm(req.provider, req.prompt)

        if llm_result.get("error"):
            with _task_lock:
                _current_task = {
                    "status": "error",
                    "error": llm_result["error"],
                    "provider": req.provider,
                    "ended_at": time.time(),
                }
            raise HTTPException(502, llm_result["error"])

        candidates = llm_result["candidates"]
        if not candidates:
            with _task_lock:
                _current_task = None
            return {
                "llm": llm_result,
                "total": 0, "passed": 0, "filtered": 0,
                "results": [],
                "message": "大模型未返回有效股票列表",
            }

        # Step 2: Validate
        validated = []
        if req.auto_validate:
            with _task_lock:
                if _current_task:
                    _current_task["message"] = f"量化验证中 ({len(candidates)}只)..."

            engine = _get_engine()
            validated = validate_candidates(
                engine, candidates,
                pe_max_pctile=req.pe_max_pctile,
                crowding_max_pctile=req.crowding_max_pctile,
                require_above_ma20=req.require_above_ma20,
                require_positive_flow=req.require_positive_flow,
            )
        else:
            validated = candidates

        passed = [r for r in validated if r.get("passed", True)]
        resp = {
            "llm": {
                "provider": llm_result["provider"],
                "model": llm_result.get("model", ""),
                "raw_response": llm_result["raw_response"],
                "candidate_count": len(candidates),
            },
            "total": len(validated),
            "passed": len(passed),
            "filtered": len(validated) - len(passed),
            "results": validated,
        }

        _save_history({"mode": "generate", "ts": time.time(), **resp})

        with _task_lock:
            _current_task = {
                "status": "completed",
                "provider": req.provider,
                "ended_at": time.time(),
            }

        return resp

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("LLM generate error")
        with _task_lock:
            _current_task = {
                "status": "error",
                "error": str(e)[:200],
                "ended_at": time.time(),
            }
        raise HTTPException(500, str(e)[:200])


@router.get("/status")
def task_status():
    """Get current task status."""
    return {"task": _current_task}


@router.get("/history")
def list_history(limit: int = 20):
    """List past LLM pick results."""
    files = sorted(_HISTORY_DIR.glob("*.json"), reverse=True)[:limit]
    items = []
    for f in files:
        try:
            d = json.loads(f.read_text())
            items.append({
                "ts": f.stem,
                "mode": d.get("mode", "unknown"),
                "total": d.get("total", 0),
                "passed": d.get("passed", 0),
                "provider": d.get("llm", {}).get("provider", ""),
            })
        except Exception:
            continue
    return {"history": items}


@router.get("/history/{ts}")
def get_history(ts: str):
    """Load a specific history result."""
    path = _HISTORY_DIR / f"{ts}.json"
    if not path.exists():
        raise HTTPException(404, "历史记录不存在")
    return json.loads(path.read_text())


@router.get("/default_prompt")
def default_prompt():
    """Return the default LLM prompt template."""
    return {"prompt": DEFAULT_PROMPT}
