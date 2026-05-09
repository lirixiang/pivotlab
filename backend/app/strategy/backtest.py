"""Walk-forward backtest of the rule-based recommender.

For each historical scan-date, we:
  1. Compute features using only candles up to that date (no leakage).
  2. Score every stock with the requested style.
  3. Take top-N picks.
  4. Simulate "buy at next-day open, sell when stop/TP hit, or after holding_max days".

Outputs win-rate, average return, max drawdown, Sharpe.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ..database import DATABASE_URL
from ..models import DailyCandle, Stock
from ..schemas import Candle
from .features import extract
from .styles import score as score_style, passes_style_filter
from .trade_plan import build_trade_plan

logger = logging.getLogger(__name__)


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


def _simulate_one(
    candles: list[Candle],
    entry_idx: int,
    plan: Any,
    holding_max: int,
) -> tuple[float, int, str]:
    """Simulate a single trade starting at next bar after entry_idx.

    Returns (return_pct, days_held, exit_reason).
    """
    if entry_idx + 1 >= len(candles):
        return 0.0, 0, "no_next_bar"

    # Buy at next bar's open
    entry = candles[entry_idx + 1]
    buy_price = entry.open
    if buy_price <= 0:
        return 0.0, 0, "bad_entry"

    # Optional: skip if next-day open gaps above buy_high (chase risk)
    if buy_price > plan.buy_high * 1.02:
        return 0.0, 0, "gap_too_high"

    stop = plan.stop_loss
    tp1 = plan.take_profit_1
    tp2 = plan.take_profit_2

    days_held = 0
    for i in range(entry_idx + 1, min(entry_idx + 1 + holding_max, len(candles))):
        b = candles[i]
        days_held += 1
        # Stop first (intrabar low)
        if b.low <= stop:
            return (stop / buy_price - 1) * 100, days_held, "stop"
        # TP2
        if b.high >= tp2:
            return (tp2 / buy_price - 1) * 100, days_held, "tp2"
        # TP1
        if b.high >= tp1:
            return (tp1 / buy_price - 1) * 100, days_held, "tp1"

    # Time exit at last bar's close
    last = candles[min(entry_idx + holding_max, len(candles) - 1)]
    return (last.close / buy_price - 1) * 100, days_held, "time"


def run_backtest(
    *,
    style: str = "swing",
    days: int = 180,
    snapshot_step: int = 5,
    top_n: int = 10,
    universe_limit: int = 600,
    progress_cb=None,
) -> dict:
    """Run a walk-forward backtest of the recommender.

    Returns: {style, n_trades, win_rate, avg_return, sharpe, max_dd,
              by_exit, sample_trades}
    """
    eng = _get_engine()
    with Session(eng) as session:
        stocks = list(session.execute(select(Stock).where(Stock.is_st == False)).scalars())  # noqa: E712
        stocks = stocks[:universe_limit]
        codes = [s.code for s in stocks]
        if not codes:
            return {"error": "empty_universe"}

        cutoff = (date.today() - timedelta(days=int(days * 1.6 + 200))).strftime("%Y-%m-%d")
        rows = session.execute(
            select(DailyCandle).where(
                DailyCandle.code.in_(codes),
                DailyCandle.trade_date >= cutoff,
            ).order_by(DailyCandle.code, DailyCandle.trade_date.asc())
        ).scalars().all()

        bucket: dict[str, list[Candle]] = defaultdict(list)
        for r in rows:
            bucket[r.code].append(Candle(
                date=r.trade_date,
                open=r.open or 0.0, high=r.high or 0.0,
                low=r.low or 0.0, close=r.close or 0.0,
                volume=r.volume or 0.0,
            ))

    if progress_cb:
        progress_cb({"phase": "loaded", "stocks": len(bucket)})

    # Get a "calendar" of available trade dates from the most-active stock.
    longest = max(bucket.values(), key=len, default=[])
    if not longest:
        return {"error": "no_candles"}
    all_dates = [c.date for c in longest]
    # Snapshot dates: only the last `days` calendar days, stepped
    cutoff_d = date.today() - timedelta(days=days)
    snap_dates = [d for d in all_dates if datetime.strptime(d, "%Y-%m-%d").date() >= cutoff_d]
    snap_dates = snap_dates[::snapshot_step]
    if len(snap_dates) < 3:
        return {"error": "not_enough_history"}

    trades: list[dict] = []
    n_snaps = len(snap_dates)
    for si, snap_d in enumerate(snap_dates):
        if progress_cb and si % 5 == 0:
            progress_cb({"phase": "scanning", "pct": int(si / n_snaps * 100)})

        # Score the universe AS-OF snap_d
        cands: list[tuple[float, str, list[Candle], int, Any, list[str]]] = []
        for code, cl in bucket.items():
            if len(cl) < 80:
                continue
            # Find idx of snap_d (last bar at or before snap_d)
            idx = -1
            for j in range(len(cl) - 1, -1, -1):
                if cl[j].date <= snap_d:
                    idx = j
                    break
            if idx < 80:
                continue
            sub = cl[: idx + 1]
            fs = extract(sub, code=code)
            if not fs:
                continue
            ok, _ = passes_style_filter(style, fs)
            if not ok:
                continue
            sc, reasons, _f = score_style(style, fs)
            if sc < 50:
                continue
            cands.append((sc, code, cl, idx, fs, reasons))

        cands.sort(key=lambda x: -x[0])
        for sc, code, cl, idx, fs, reasons in cands[:top_n]:
            try:
                plan = build_trade_plan(
                    style=style, candles=cl[: idx + 1], levels=[], fs=fs,
                    score=sc, reasons=reasons,
                )
            except Exception:
                continue
            if not plan.tradable:
                continue
            ret, held, exit_reason = _simulate_one(cl, idx, plan, plan.holding_days_max)
            trades.append({
                "snap_date": snap_d, "code": code,
                "score": round(sc, 1), "return_pct": round(ret, 2),
                "days_held": held, "exit": exit_reason,
                "rr": plan.risk_reward,
            })

    # Aggregate
    if not trades:
        return {"style": style, "n_trades": 0, "error": "no_trades"}

    rets = np.array([t["return_pct"] for t in trades])
    wins = int((rets > 0).sum())
    total = len(rets)
    cum = np.cumsum(rets)
    peak = np.maximum.accumulate(cum)
    dd = (cum - peak)
    max_dd = float(dd.min()) if len(dd) else 0.0
    sharpe = float(rets.mean() / (rets.std() + 1e-9) * np.sqrt(252 / max(1, snapshot_step)))

    by_exit: dict[str, int] = defaultdict(int)
    for t in trades:
        by_exit[t["exit"]] += 1

    return {
        "style": style,
        "n_trades": total,
        "win_rate": round(wins / total * 100, 1),
        "avg_return": round(float(rets.mean()), 2),
        "median_return": round(float(np.median(rets)), 2),
        "best": round(float(rets.max()), 2),
        "worst": round(float(rets.min()), 2),
        "max_dd_cum": round(max_dd, 2),
        "sharpe_proxy": round(sharpe, 2),
        "avg_days_held": round(float(np.mean([t["days_held"] for t in trades])), 1),
        "by_exit": dict(by_exit),
        "sample_trades": trades[:30],
        "params": {
            "days": days, "snapshot_step": snapshot_step,
            "top_n": top_n, "universe_limit": universe_limit,
        },
    }
