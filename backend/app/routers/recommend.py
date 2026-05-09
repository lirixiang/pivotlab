"""/api/recommend/* — new strategy & trade-plan endpoints."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import Path as FastAPIPath
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from ..database import DATABASE_URL
from ..strategy import STYLES, STYLE_LABELS
from ..strategy.recommender import rebuild_for_code, scan_universe
from ..strategy import store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/recommend", tags=["recommend"])


# ── Sync-engine helper (mirrors data_provider pattern) ────────
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


# ── Background scan tracking ──────────────────────────────────
_PROGRESS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "recommend_progress"
_PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
_scan_lock = threading.Lock()
_current_scan: dict | None = None  # {"id": str, "started": ts, "styles": [...]}


def _progress_file(scan_id: str) -> Path:
    return _PROGRESS_DIR / f"{scan_id}.json"


def _write_progress(scan_id: str, payload: dict) -> None:
    try:
        _progress_file(scan_id).write_text(json.dumps(payload, ensure_ascii=False))
    except Exception:
        pass


def _read_progress(scan_id: str) -> dict | None:
    f = _progress_file(scan_id)
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text())
    except Exception:
        return None


def _run_scan_thread(scan_id: str, styles: list[str], top_n: int, min_score: float):
    global _current_scan
    payload = {
        "scan_id": scan_id, "styles": styles, "top_n": top_n,
        "started_at": datetime.utcnow().isoformat(),
        "status": "running", "phase": "starting", "pct": 0,
    }
    _write_progress(scan_id, payload)

    def cb(info: dict):
        payload.update(info)
        _write_progress(scan_id, payload)

    try:
        counts = scan_universe(
            styles=styles, top_n=top_n, min_score=min_score, progress_cb=cb,
        )
        payload.update({
            "status": "done", "phase": "done", "pct": 100,
            "counts": counts, "finished_at": datetime.utcnow().isoformat(),
        })
    except Exception as e:
        logger.exception("scan failed")
        payload.update({
            "status": "error", "error": str(e),
            "finished_at": datetime.utcnow().isoformat(),
        })
    _write_progress(scan_id, payload)
    with _scan_lock:
        if _current_scan and _current_scan.get("id") == scan_id:
            _current_scan = None


# ──────────────────────────────────────────────────────────────
#  Endpoints
# ──────────────────────────────────────────────────────────────

@router.get("/styles")
def list_styles():
    return [{"key": k, "label": STYLE_LABELS[k]} for k in STYLES]


@router.post("/scan")
def trigger_scan(
    styles: str = Query("", description="逗号分隔, 空=全部"),
    top_n: int = Query(100, ge=10, le=500),
    min_score: float = Query(50.0, ge=0, le=100),
):
    """Kick off a background scan. Returns immediately with a scan_id."""
    global _current_scan
    with _scan_lock:
        if _current_scan is not None:
            return {"scan_id": _current_scan["id"], "status": "already_running"}
        sl = [s for s in styles.split(",") if s] if styles else list(STYLES)
        bad = [s for s in sl if s not in STYLES]
        if bad:
            raise HTTPException(400, f"unknown styles: {bad}")
        scan_id = f"scan_{int(time.time())}"
        _current_scan = {"id": scan_id, "started": time.time(), "styles": sl}
        t = threading.Thread(
            target=_run_scan_thread,
            args=(scan_id, sl, top_n, min_score),
            daemon=True,
        )
        t.start()
        return {"scan_id": scan_id, "status": "started", "styles": sl}


@router.get("/scan/{scan_id}")
def scan_progress(scan_id: str):
    p = _read_progress(scan_id)
    if not p:
        raise HTTPException(404, "scan_id not found")
    return p


@router.get("/scan")
def current_scan():
    """Return the in-flight scan progress (or null)."""
    with _scan_lock:
        cur = _current_scan
    if not cur:
        return {"running": False}
    p = _read_progress(cur["id"]) or {}
    return {"running": True, **p}


@router.get("/list")
def list_recommendations(
    style: str | None = Query(None),
    scan_date: str | None = Query(None, description="YYYY-MM-DD; default = latest"),
    limit: int = Query(300, ge=1, le=500),
):
    """Return today's (or specified date's) recommendations."""
    if style and style not in STYLES:
        raise HTTPException(400, f"unknown style: {style}")
    eng = _get_engine()
    with Session(eng) as session:
        if not scan_date:
            scan_date = _latest_scan_date(session, style=style)
        items = store.list_recent(
            session, style=style, scan_date=scan_date, limit=limit,
        )
    return {
        "scan_date": scan_date,
        "style": style,
        "count": len(items),
        "items": items,
    }


@router.get("/stock/{code}")
def detail_for_code(
    code: str = FastAPIPath(..., pattern=r"^\d{6}$", description="6-digit stock code"),
    rebuild: bool = Query(False, description="强制重新计算(忽略缓存)"),
):
    """Return all-style recommendations for a single stock.

    If `rebuild=True`, recomputes on the fly (no DB write). Otherwise
    returns the latest persisted recommendations.
    """
    if rebuild:
        return {"code": code, "items": rebuild_for_code(code), "from": "live"}

    eng = _get_engine()
    with Session(eng) as session:
        rows = store.get_for_code(session, code)
    if not rows:
        # Fallback to live
        return {"code": code, "items": rebuild_for_code(code), "from": "live"}
    return {"code": code, "items": rows, "from": "db"}


# ── Helpers ───────────────────────────────────────────────────

def _latest_scan_date(session: Session, style: str | None = None) -> str:
    from sqlalchemy import select, func
    from ..models import Recommendation
    stmt = select(func.max(Recommendation.scan_date))
    if style:
        stmt = stmt.where(Recommendation.style == style)
    row = session.execute(stmt).scalar()
    return row or date.today().strftime("%Y-%m-%d")


# ═══════════════════════════════════════════════════════════════
#  Backtest endpoint — walk-forward sim of the recommender
# ═══════════════════════════════════════════════════════════════
@router.post("/backtest")
def backtest_strategy(
    style: str = Query("swing", description="Style to backtest"),
    days: int = Query(180, ge=30, le=720, description="历史窗口天数"),
    snapshot_step: int = Query(5, ge=1, le=20, description="每隔几天打一次分"),
    top_n: int = Query(10, ge=1, le=30),
    universe_limit: int = Query(600, ge=50, le=5000),
):
    """Walk-forward backtest of the rule-based recommender.

    Returns aggregate stats: win-rate, avg return, max drawdown,
    plus a sample of trades for inspection.
    """
    if style not in STYLES:
        raise HTTPException(400, f"Unknown style {style!r}, must be one of {STYLES}")
    from ..strategy.backtest import run_backtest
    try:
        result = run_backtest(
            style=style, days=days, snapshot_step=snapshot_step,
            top_n=top_n, universe_limit=universe_limit,
        )
    except Exception as e:
        logger.exception("backtest failed")
        raise HTTPException(500, str(e))
    return result


# ═══════════════════════════════════════════════════════════════
#  ML training endpoints
# ═══════════════════════════════════════════════════════════════
_TRAIN_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "train_progress"
_TRAIN_DIR.mkdir(parents=True, exist_ok=True)
_train_lock = threading.Lock()
_current_train: dict | None = None  # {"id":..., "model":..., "started":...}

# Whitelist of trainable models — keys are stable identifiers used by both
# the API and the registry directory layout.
_MODELS = {"lgbm", "seq", "rl"}


def _train_progress_file(job_id: str) -> Path:
    return _TRAIN_DIR / f"{job_id}.json"


def _write_train(job_id: str, payload: dict):
    try:
        _train_progress_file(job_id).write_text(
            json.dumps(payload, ensure_ascii=False, default=str)
        )
    except Exception:
        pass


def _read_train(job_id: str) -> dict | None:
    f = _train_progress_file(job_id)
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text())
    except Exception:
        return None


def _run_train_thread(job_id: str, model: str, params: dict):
    global _current_train
    payload = {
        "job_id": job_id, "model": model, "params": params,
        "started_at": datetime.utcnow().isoformat(),
        "status": "running", "phase": "starting", "pct": 0,
    }
    _write_train(job_id, payload)

    def cb(info: dict):
        payload.update(info)
        _write_train(job_id, payload)

    try:
        if model == "lgbm":
            from ..strategy.ml import lgbm as _m
            meta = _m.train(progress_cb=cb, **params)
        elif model == "seq":
            from ..strategy.ml import sequence as _m
            meta = _m.train(progress_cb=cb, **params)
        elif model == "rl":
            from ..strategy.ml import rl_position as _m
            meta = _m.train(progress_cb=cb, **params)
        else:
            raise ValueError(f"unknown model: {model}")
        payload.update({
            "status": "done", "phase": "done", "pct": 100,
            "meta": meta, "finished_at": datetime.utcnow().isoformat(),
        })
    except Exception as e:
        logger.exception("train failed")
        payload.update({
            "status": "error", "error": str(e),
            "finished_at": datetime.utcnow().isoformat(),
        })
    _write_train(job_id, payload)
    with _train_lock:
        if _current_train and _current_train.get("id") == job_id:
            _current_train = None


@router.post("/train/{model}")
def trigger_train(
    model: str,
    horizon_days: int = Query(10, ge=3, le=30),
    universe_limit: int = Query(600, ge=50, le=5000),
    history_years: float = Query(2.0, ge=0.5, le=5.0),
    epochs: int = Query(12, ge=1, le=50, description="seq only"),
    total_timesteps: int = Query(80_000, ge=10_000, le=2_000_000, description="rl only"),
):
    """Start a background training job for one of {lgbm, seq, rl}."""
    global _current_train
    if model not in _MODELS:
        raise HTTPException(400, f"model must be one of {sorted(_MODELS)}")
    with _train_lock:
        if _current_train is not None:
            return {
                "job_id": _current_train["id"],
                "status": "already_running",
                "model": _current_train["model"],
            }
        if model == "lgbm":
            params = dict(horizon_days=horizon_days,
                          universe_limit=universe_limit,
                          history_years=history_years)
        elif model == "seq":
            params = dict(horizon_days=horizon_days,
                          universe_limit=universe_limit,
                          history_years=history_years,
                          epochs=epochs)
        else:  # rl
            params = dict(universe_limit=min(universe_limit, 400),
                          total_timesteps=total_timesteps)
        job_id = f"train_{model}_{int(time.time())}"
        _current_train = {"id": job_id, "model": model,
                          "started": time.time(), "params": params}
        t = threading.Thread(
            target=_run_train_thread, args=(job_id, model, params), daemon=True,
        )
        t.start()
        return {"job_id": job_id, "status": "started", "model": model, "params": params}


@router.get("/train/{job_id}")
def train_progress(job_id: str):
    p = _read_train(job_id)
    if not p:
        raise HTTPException(404, "job_id not found")
    return p


@router.get("/trainings")
def current_train():
    """Return the in-flight training job (or null) and the registry of trained models."""
    from ..strategy.ml import registry as reg
    with _train_lock:
        cur = _current_train
    cur_payload = None
    if cur:
        cur_payload = {"running": True, **(_read_train(cur["id"]) or {})}
    return {
        "current": cur_payload,
        "registry": reg.list_models(),
    }


# ═══════════════════════════════════════════════════════════════
#  Index k-line + Lifecycle endpoints
# ═══════════════════════════════════════════════════════════════
@router.post("/sync_indices")
def sync_indices_now():
    """Synchronously pull index k-lines (small, fast)."""
    from ..services.index_sync import sync_indices
    try:
        return sync_indices()
    except Exception as e:
        logger.exception("sync_indices failed")
        raise HTTPException(500, str(e))


@router.get("/index/env")
def market_env():
    """Return the current real-index-derived market environment."""
    from ..services.index_sync import market_environment_from_index, get_recent_closes
    trend, atr_pct = market_environment_from_index("sh000001")
    closes = get_recent_closes("sh000001", days=20)
    return {
        "code": "sh000001",
        "trend": round(trend, 3),
        "atr_pct": round(atr_pct * 100, 3),
        "verdict": (
            "强多头" if trend > 0.5 else
            "偏多" if trend > 0.1 else
            "偏空" if trend < -0.1 else
            "震荡"
        ),
        "recent_closes": closes,
    }


@router.post("/lifecycle/update")
def lifecycle_update(lookback_days: int = Query(60, ge=1, le=365)):
    """Run the lifecycle tracker for recent recommendations."""
    from ..services.lifecycle import update_lifecycle
    try:
        return update_lifecycle(lookback_days=lookback_days)
    except Exception as e:
        logger.exception("lifecycle update failed")
        raise HTTPException(500, str(e))


@router.get("/lifecycle/stats")
def lifecycle_stats(
    style: str | None = Query(None),
    days: int = Query(90, ge=7, le=365),
):
    """Aggregated outcome statistics over recent recommendations."""
    from ..services.lifecycle import aggregate_outcomes
    return aggregate_outcomes(style=style, days=days)


@router.get("/lifecycle/recent")
def lifecycle_recent(
    style: str | None = Query(None),
    state: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
):
    """List recent outcome rows for inspection."""
    from sqlalchemy import select
    from ..models import RecommendationOutcome, Stock
    eng = _get_engine()
    with Session(eng) as session:
        stmt = select(RecommendationOutcome).order_by(
            RecommendationOutcome.scan_date.desc(),
            RecommendationOutcome.id.desc(),
        )
        if style:
            stmt = stmt.where(RecommendationOutcome.style == style)
        if state:
            stmt = stmt.where(RecommendationOutcome.state == state)
        rows = list(session.execute(stmt.limit(limit)).scalars().all())
        # name lookup
        codes = list({r.code for r in rows})
        names = {}
        if codes:
            for s in session.execute(select(Stock).where(Stock.code.in_(codes))).scalars():
                names[s.code] = s.name
    return [{
        "id": r.id, "code": r.code, "name": names.get(r.code, ""),
        "style": r.style, "scan_date": r.scan_date,
        "state": r.state, "exit_reason": r.exit_reason,
        "buy_low": r.buy_low, "buy_high": r.buy_high,
        "stop_loss": r.stop_loss,
        "take_profit_1": r.take_profit_1, "take_profit_2": r.take_profit_2,
        "initial_price": r.initial_price,
        "triggered_date": r.triggered_date, "triggered_price": r.triggered_price,
        "exit_date": r.exit_date, "exit_price": r.exit_price,
        "max_favorable_pct": r.max_favorable_pct,
        "max_adverse_pct": r.max_adverse_pct,
        "realized_return_pct": r.realized_return_pct,
        "days_to_trigger": r.days_to_trigger,
        "days_held": r.days_held,
    } for r in rows]
