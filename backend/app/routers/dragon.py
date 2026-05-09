"""龙头战法 API router — /api/dragon

Endpoints:
  POST   /train            Train dragon models (Stage 1 + Stage 2)
  GET    /train_progress   List active/recent training jobs
  DELETE /train_progress/{tid}  Cancel/clear a training job
  GET    /status           Dragon model status (file mtime/size)
  GET    /market-cycle     Today's market cycle judgement
  POST   /scan             Scan today's dragon candidates
  GET    /scan/today       Get cached top dragons for today (uses persisted DB)
  GET    /signal/{code}    Generate full signal for one stock
  POST   /backtest         Run dragon strategy backtest
  GET    /zt-pool          Today's ZT pool (with optional date filter)
  GET    /lhb/{code}       Stock's LHB history
  GET    /board-heat       Concept heat ranking with history trend
  GET    /knowledge        Hot money knowledge base
  POST   /sync             Trigger dragon data sync (zt_pool / lhb / heat / all)
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import select, func, and_

from ..database import AsyncSessionLocal
from ..models import (
    ZtPoolDaily, LhbRecord, LhbSeatDetail, ConceptHeatHistory, DragonSignal, Stock,
)
from ..services import dragon_strategy as ds
from ..services.dragon_strategy import HOT_MONEY_PATTERNS, judge_market_cycle, dragon_model_status
from ..services.sync_worker import spawn_sync

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dragon", tags=["dragon"])


# ═══════════════════════════════════════════════════════════════
# In-memory training/scan progress trackers
# ═══════════════════════════════════════════════════════════════

_train_jobs: dict[str, dict[str, Any]] = {}
_scan_jobs: dict[str, dict[str, Any]] = {}


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ═══════════════════════════════════════════════════════════════
# Sync triggers (delegate to spawn_sync)
# ═══════════════════════════════════════════════════════════════

@router.post("/sync")
async def trigger_sync(body: dict | None = None):
    """Trigger dragon data sync.

    Body: { "task": "zt_pool" | "lhb" | "concept_heat_history" | "dragon_all",
             "date_str": "YYYY-MM-DD" (optional) }
    """
    body = body or {}
    task = body.get("task", "dragon_all")
    if task not in {"zt_pool", "lhb", "concept_heat_history", "dragon_all"}:
        return {"error": f"unknown task: {task}"}
    kwargs = {}
    if body.get("date_str"):
        kwargs["date_str"] = body["date_str"]
    started = spawn_sync(task, **kwargs)
    return {"task": task, "status": "started" if started else "already_running"}


@router.post("/backfill")
async def trigger_backfill(body: dict | None = None):
    """Backfill historical dragon data over a date range.

    Body (all optional):
      { "start_date": "YYYY-MM-DD",
        "end_date":   "YYYY-MM-DD",
        "days": 60,                  # used if start/end omitted (last N days)
        "include_zt": true,
        "include_lhb": true,
        "include_concept": true,
        "sleep_sec": 0.3 }            # delay between EM API calls
    """
    body = body or {}
    kwargs: dict = {}
    for k in ("start_date", "end_date", "days", "include_zt", "include_lhb",
              "include_concept", "sleep_sec"):
        if k in body and body[k] is not None:
            kwargs[k] = body[k]
    started = spawn_sync("dragon_backfill", **kwargs)
    return {"task": "dragon_backfill", "status": "started" if started else "already_running"}


# ═══════════════════════════════════════════════════════════════
# Status / market cycle
# ═══════════════════════════════════════════════════════════════

@router.get("/status")
async def status():
    return dragon_model_status()


@router.get("/market-cycle")
async def market_cycle(date: str | None = Query(None)):
    d = date or _today()

    def _run():
        return asdict(judge_market_cycle(d))

    return await asyncio.to_thread(_run)


# ═══════════════════════════════════════════════════════════════
# ZT pool / LHB / Board heat data endpoints
# ═══════════════════════════════════════════════════════════════

@router.get("/zt-pool")
async def zt_pool(
    date: str | None = Query(None),
    pool_type: str = Query("zt", regex="^(zt|zb|dt)$"),
    min_consecutive: int = Query(1, ge=0),
    limit: int = Query(200, ge=1, le=500),
):
    d = date or _today()
    async with AsyncSessionLocal() as s:
        stmt = (
            select(ZtPoolDaily).where(
                ZtPoolDaily.trade_date == d,
                ZtPoolDaily.pool_type == pool_type,
                ZtPoolDaily.consecutive >= min_consecutive,
            )
            .order_by(ZtPoolDaily.consecutive.desc(), ZtPoolDaily.first_zt_time.asc())
            .limit(limit)
        )
        rows = (await s.execute(stmt)).scalars().all()
    return {
        "trade_date": d, "pool_type": pool_type, "count": len(rows),
        "items": [
            {
                "code": r.code, "name": r.name, "change_pct": r.change_pct,
                "close": r.close, "amount": r.amount,
                "market_cap": r.market_cap, "turnover_rate": r.turnover_rate,
                "first_zt_time": r.first_zt_time, "last_zt_time": r.last_zt_time,
                "open_count": r.open_count, "seal_amount": r.seal_amount,
                "consecutive": r.consecutive, "concept": r.concept,
                "industry": r.industry, "zt_status": r.zt_status,
            }
            for r in rows
        ],
    }


@router.get("/lhb/{code}")
async def lhb_history(code: str, limit: int = Query(30, ge=1, le=200)):
    async with AsyncSessionLocal() as s:
        recs = (await s.execute(
            select(LhbRecord).where(LhbRecord.code == code)
            .order_by(LhbRecord.trade_date.desc()).limit(limit)
        )).scalars().all()
        if not recs:
            return {"code": code, "records": [], "seats": {}}
        dates = [r.trade_date for r in recs]
        seats = (await s.execute(
            select(LhbSeatDetail).where(
                LhbSeatDetail.code == code,
                LhbSeatDetail.trade_date.in_(dates),
            ).order_by(LhbSeatDetail.trade_date.desc(), LhbSeatDetail.rank)
        )).scalars().all()
    by_date: dict[str, list[dict]] = {}
    for ss in seats:
        by_date.setdefault(ss.trade_date, []).append({
            "rank": ss.rank, "side": ss.side, "seat_name": ss.seat_name,
            "buy_amount": ss.buy_amount, "sell_amount": ss.sell_amount,
            "net_amount": ss.net_amount, "is_known_hot": ss.is_known_hot,
            "hot_money_tag": ss.hot_money_tag,
        })
    return {
        "code": code,
        "records": [
            {
                "trade_date": r.trade_date, "name": r.name, "reason": r.reason,
                "close": r.close, "change_pct": r.change_pct,
                "turnover": r.turnover, "buy_total": r.buy_total,
                "sell_total": r.sell_total, "net_amount": r.net_amount,
                "net_rate": r.net_rate,
            }
            for r in recs
        ],
        "seats": by_date,
    }


@router.get("/board-heat")
async def board_heat(
    date: str | None = Query(None),
    limit: int = Query(30, ge=1, le=200),
    history_days: int = Query(7, ge=1, le=30),
):
    """Top hot boards on `date`, plus their heat trend over last `history_days`."""
    d = date or _today()
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(ConceptHeatHistory).where(ConceptHeatHistory.trade_date == d)
            .order_by(ConceptHeatHistory.heat_score.desc().nullslast()).limit(limit)
        )).scalars().all()
        if not rows:
            return {"trade_date": d, "items": []}
        concepts = [r.concept for r in rows]
        # Trend
        trend_rows = (await s.execute(
            select(ConceptHeatHistory.concept, ConceptHeatHistory.trade_date,
                    ConceptHeatHistory.heat_score).where(
                ConceptHeatHistory.concept.in_(concepts),
                ConceptHeatHistory.trade_date <= d,
            ).order_by(ConceptHeatHistory.concept,
                        ConceptHeatHistory.trade_date.desc())
        )).all()
    trend: dict[str, list[dict]] = {}
    for c, dt, sc in trend_rows:
        if len(trend.setdefault(c, [])) < history_days:
            trend[c].append({"date": dt, "score": sc})
    for k in trend:
        trend[k].reverse()

    return {
        "trade_date": d,
        "items": [
            {
                "concept": r.concept, "board_code": r.board_code,
                "change_pct": r.change_pct, "net_inflow": r.net_inflow,
                "heat_score": r.heat_score, "heat_level": r.heat_level,
                "rank": r.rank, "zt_count": r.zt_count, "up_ratio": r.up_ratio,
                "leader_code": r.leader_code, "leader_name": r.leader_name,
                "leader_change": r.leader_change,
                "leader_consecutive": r.leader_consecutive,
                "trend": trend.get(r.concept, []),
            }
            for r in rows
        ],
    }


@router.get("/knowledge")
async def knowledge_base():
    return HOT_MONEY_PATTERNS


# ═══════════════════════════════════════════════════════════════
# Training
# ═══════════════════════════════════════════════════════════════

@router.post("/train")
async def train(body: dict):
    """Body: {start_date, end_date, epochs?, train_stage2?: bool}."""
    start = body.get("start_date")
    end = body.get("end_date")
    epochs = int(body.get("epochs", 30))
    train_s2 = bool(body.get("train_stage2", True))
    if not start or not end:
        return {"error": "start_date and end_date required (YYYY-MM-DD)"}

    tid = str(uuid.uuid4())[:12]
    job = {
        "task_id": tid, "status": "running", "progress": 0, "message": "queued",
        "start_date": start, "end_date": end, "epochs": epochs,
        "train_stage2": train_s2,
        "started_at": time.time(), "ended_at": None, "result": None,
    }
    _train_jobs[tid] = job

    def _cb(pct: int, msg: str):
        job["progress"] = int(pct)
        job["message"] = msg

    def _runner():
        try:
            res = ds.train_dragon_models(start, end, epochs=epochs,
                                          train_stage2=train_s2, progress_cb=_cb)
            job["result"] = res
            job["status"] = "done"
            job["progress"] = 100
            job["message"] = "训练完成"
        except Exception as e:
            logger.exception("dragon training failed")
            job["status"] = "error"
            job["message"] = str(e)
        finally:
            job["ended_at"] = time.time()

    asyncio.get_event_loop().run_in_executor(None, _runner)
    return {"task_id": tid, "status": "started"}


@router.get("/train_progress")
async def train_progress():
    return list(_train_jobs.values())


@router.delete("/train_progress/{tid}")
async def clear_train(tid: str):
    if tid in _train_jobs:
        del _train_jobs[tid]
        return {"status": "cleared"}
    return {"status": "not_found"}


@router.delete("/train_progress")
async def clear_all_train():
    n = len(_train_jobs)
    _train_jobs.clear()
    return {"removed": n}


# ═══════════════════════════════════════════════════════════════
# Scan / Signal
# ═══════════════════════════════════════════════════════════════

@router.post("/scan")
async def scan(body: dict | None = None):
    body = body or {}
    date = body.get("date") or _today()
    threshold = float(body.get("threshold", 60.0))
    top_n = int(body.get("top_n", 30))
    persist = bool(body.get("persist", True))

    tid = str(uuid.uuid4())[:12]
    job = {
        "task_id": tid, "status": "running", "progress": 0,
        "message": f"扫描 {date} 龙头候选...",
        "date": date, "threshold": threshold,
        "started_at": time.time(), "ended_at": None,
        "candidates": [],
    }
    _scan_jobs[tid] = job

    def _runner():
        try:
            cands = ds.identify_dragons(date, score_threshold=threshold,
                                          top_n=top_n, persist=persist)
            # Generate full signals for top candidates
            results = []
            for i, c in enumerate(cands):
                sig = ds.generate_dragon_signal(c["code"], date)
                if sig:
                    results.append({
                        **asdict(sig),
                        "dragon_rank": c["dragon_rank"],
                        "dragon_score": c["dragon_score"],
                        "amount": c.get("amount"),
                        "industry": c.get("industry"),
                    })
                job["progress"] = int((i + 1) / max(len(cands), 1) * 100)
            job["candidates"] = results
            job["status"] = "done"
            job["progress"] = 100
            job["message"] = f"完成 - {len(results)} 个候选"
        except Exception as e:
            logger.exception("dragon scan failed")
            job["status"] = "error"
            job["message"] = str(e)
        finally:
            job["ended_at"] = time.time()

    asyncio.get_event_loop().run_in_executor(None, _runner)
    return {"task_id": tid, "status": "started"}


@router.get("/scan_progress")
async def scan_progress():
    return list(_scan_jobs.values())


@router.delete("/scan_progress/{tid}")
async def clear_scan(tid: str):
    if tid in _scan_jobs:
        del _scan_jobs[tid]
        return {"status": "cleared"}
    return {"status": "not_found"}


@router.delete("/scan_progress")
async def clear_all_scans():
    n = len(_scan_jobs)
    _scan_jobs.clear()
    return {"removed": n}


@router.get("/scan/today")
async def get_today_signals(date: str | None = Query(None), limit: int = Query(50, ge=1, le=200)):
    """Return persisted signals from dragon_signals table (latest scan)."""
    d = date or _today()
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(DragonSignal).where(DragonSignal.trade_date == d)
            .order_by(DragonSignal.dragon_score.desc()).limit(limit)
        )).scalars().all()
    return {
        "trade_date": d,
        "items": [
            {
                "code": r.code, "name": r.name, "signal_type": r.signal_type,
                "dragon_rank": r.dragon_rank, "dragon_score": r.dragon_score,
                "concept": r.concept, "consecutive": r.consecutive,
                "model_conf": r.model_conf,
                "entry_price": r.entry_price, "stop_price": r.stop_price,
                "target_price": r.target_price, "market_cycle": r.market_cycle,
                "reason": r.reason,
            }
            for r in rows
        ],
    }


@router.get("/signal/{code}")
async def signal_for(code: str, date: str | None = Query(None)):
    d = date or _today()

    def _run():
        sig = ds.generate_dragon_signal(code, d)
        return asdict(sig) if sig else None

    res = await asyncio.to_thread(_run)
    if res is None:
        return {"error": "insufficient data or model not trained"}
    return res


# ═══════════════════════════════════════════════════════════════
# Backtest
# ═══════════════════════════════════════════════════════════════

@router.post("/backtest")
async def backtest(body: dict):
    start = body.get("start_date")
    end = body.get("end_date")
    if not start or not end:
        return {"error": "start_date and end_date required"}

    def _run():
        return ds.backtest_dragon(
            start, end,
            score_threshold=float(body.get("score_threshold", 70.0)),
            hold_days=int(body.get("hold_days", 5)),
            stop_pct=float(body.get("stop_pct", -5.0)),
            max_positions=int(body.get("max_positions", 3)),
            filter_ice=bool(body.get("filter_ice", True)),
            filter_cooldown=bool(body.get("filter_cooldown", True)),
            init_cash=float(body.get("init_cash", 1_000_000.0)),
        )

    return await asyncio.to_thread(_run)
