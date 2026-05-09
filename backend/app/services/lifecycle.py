"""Recommendation lifecycle tracker.

Run nightly (or on-demand). For every active recommendation:
  1. Pull all candles from scan_date+1 to today.
  2. Walk forward: check if buy_low ≤ low ≤ buy_high (triggered).
  3. After triggered: track stop / TP1 / TP2 / max favorable / adverse.
  4. After expires_date or terminal hit: mark final outcome.

Terminal states: tp2 > tp1 > stopped > expired > never_triggered
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ..database import DATABASE_URL
from ..models import (
    DailyCandle,
    Recommendation,
    RecommendationOutcome,
    TradePlan,
)

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


def _terminal(state: str) -> bool:
    return state in {"tp1", "tp2", "stopped", "expired", "never_triggered"}


def update_lifecycle(*, lookback_days: int = 60, progress_cb=None) -> dict:
    """Update outcomes for every reco in the last `lookback_days` that isn't terminal.

    Returns aggregate counts per state.
    """
    eng = _get_engine()
    today = date.today()
    today_s = today.strftime("%Y-%m-%d")
    cutoff = (today - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    counts: dict[str, int] = defaultdict(int)
    n_processed = 0

    with Session(eng) as session:
        # Recos to process: scan_date >= cutoff
        recos = session.execute(
            select(Recommendation).where(Recommendation.scan_date >= cutoff)
        ).scalars().all()
        if not recos:
            return {"n_processed": 0}

        # Existing outcomes by reco_id
        existing = {
            o.recommendation_id: o
            for o in session.execute(
                select(RecommendationOutcome).where(
                    RecommendationOutcome.recommendation_id.in_([r.id for r in recos])
                )
            ).scalars().all()
        }

        # Plans by reco_id
        plans = {
            p.recommendation_id: p
            for p in session.execute(
                select(TradePlan).where(
                    TradePlan.recommendation_id.in_([r.id for r in recos])
                )
            ).scalars().all()
        }

        # Bulk-load candles for all involved codes from earliest scan_date
        codes = list({r.code for r in recos})
        earliest = min(r.scan_date for r in recos)
        candles_rows = session.execute(
            select(DailyCandle).where(
                DailyCandle.code.in_(codes),
                DailyCandle.trade_date >= earliest,
            ).order_by(DailyCandle.code, DailyCandle.trade_date.asc())
        ).scalars().all()
        cmap: dict[str, list[DailyCandle]] = defaultdict(list)
        for c in candles_rows:
            cmap[c.code].append(c)

        n_total = len(recos)
        for idx, reco in enumerate(recos):
            if progress_cb and idx % 50 == 0:
                progress_cb({"phase": "tracking", "pct": int(idx / n_total * 100),
                             "processed": idx, "total": n_total})

            outcome = existing.get(reco.id)
            if outcome and _terminal(outcome.state):
                counts[outcome.state] += 1
                continue

            plan = plans.get(reco.id)
            if not plan:
                continue

            # Skip if the plan was a "wait" (tradable=False)
            pf = plan.factors or {}
            if pf.get("state") in ("wait_breakout", "wait_pullback", "reject"):
                continue
            if pf.get("tradable") is False:
                continue

            # Create outcome row if missing
            if outcome is None:
                outcome = RecommendationOutcome(
                    recommendation_id=reco.id,
                    code=reco.code,
                    style=reco.style,
                    scan_date=reco.scan_date,
                    expires_date=reco.expires_date or "",
                    buy_low=plan.buy_low,
                    buy_high=plan.buy_high,
                    stop_loss=plan.stop_loss,
                    take_profit_1=plan.take_profit_1,
                    take_profit_2=plan.take_profit_2,
                    initial_price=reco.price,
                    state="pending",
                )
                session.add(outcome)
                session.flush()

            cl = [c for c in cmap.get(reco.code, []) if c.trade_date > reco.scan_date]
            if not cl:
                continue

            _walk_forward(outcome, cl, today_s)
            session.add(outcome)
            counts[outcome.state] += 1
            n_processed += 1

        session.commit()

    if progress_cb:
        progress_cb({"phase": "done", "pct": 100, "counts": dict(counts)})
    logger.info("lifecycle: processed=%d states=%s", n_processed, dict(counts))
    return {"n_processed": n_processed, "states": dict(counts)}


def _walk_forward(o: RecommendationOutcome, candles: list[DailyCandle], today_s: str) -> None:
    """Update outcome by replaying daily candles since scan_date."""
    triggered = bool(o.triggered_date)
    state = o.state

    for c in candles:
        if c.close is None or c.high is None or c.low is None:
            continue
        d = c.trade_date
        if d > today_s:
            break

        if not triggered:
            # Trigger if buy_low <= low <= buy_high (or open within zone)
            if c.low <= o.buy_high and c.high >= o.buy_low:
                # Use the entry edge of the buy zone
                entry = max(o.buy_low, min(o.buy_high, c.open))
                o.triggered_date = d
                o.triggered_price = float(entry)
                o.days_to_trigger = (datetime.strptime(d, "%Y-%m-%d").date()
                                      - datetime.strptime(o.scan_date, "%Y-%m-%d").date()).days
                triggered = True
                state = "triggered"
            else:
                continue

        # After triggered: track exits
        entry = o.triggered_price or 1.0
        if entry <= 0:
            continue

        # MFE / MAE
        fav = (c.high / entry - 1) * 100
        adv = (c.low / entry - 1) * 100
        if fav > o.max_favorable_pct:
            o.max_favorable_pct = float(fav)
        if adv < o.max_adverse_pct:
            o.max_adverse_pct = float(adv)

        # Stop check first (intrabar low)
        if c.low <= o.stop_loss:
            o.exit_date = d
            o.exit_price = float(o.stop_loss)
            o.exit_reason = "stopped"
            state = "stopped"
            o.realized_return_pct = (o.stop_loss / entry - 1) * 100
            break
        # TP2
        if c.high >= o.take_profit_2:
            o.exit_date = d
            o.exit_price = float(o.take_profit_2)
            o.exit_reason = "tp2"
            state = "tp2"
            o.realized_return_pct = (o.take_profit_2 / entry - 1) * 100
            break
        # TP1
        if c.high >= o.take_profit_1:
            o.exit_date = d
            o.exit_price = float(o.take_profit_1)
            o.exit_reason = "tp1"
            state = "tp1"
            o.realized_return_pct = (o.take_profit_1 / entry - 1) * 100
            break

    # Compute days_held + check expiry
    if triggered and not _terminal(state):
        last = candles[-1]
        o.realized_return_pct = (last.close / (o.triggered_price or 1.0) - 1) * 100
        try:
            t_d = datetime.strptime(o.triggered_date, "%Y-%m-%d").date()
            l_d = datetime.strptime(last.trade_date, "%Y-%m-%d").date()
            o.days_held = (l_d - t_d).days
        except Exception:
            pass
        if o.expires_date and last.trade_date >= o.expires_date:
            o.exit_date = last.trade_date
            o.exit_price = float(last.close)
            o.exit_reason = "expired"
            state = "expired"
    elif not triggered:
        # Not yet triggered → check if expired
        if o.expires_date and today_s >= o.expires_date:
            state = "never_triggered"
            o.exit_reason = "never_triggered"
            o.exit_date = today_s

    o.state = state
    o.last_checked_date = today_s
    o.updated_at = datetime.utcnow()


def aggregate_outcomes(style: str | None = None, days: int = 90) -> dict:
    """Roll-up stats over recently-completed outcomes."""
    eng = _get_engine()
    cutoff = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    with Session(eng) as session:
        stmt = select(RecommendationOutcome).where(
            RecommendationOutcome.scan_date >= cutoff
        )
        if style:
            stmt = stmt.where(RecommendationOutcome.style == style)
        rows = list(session.execute(stmt).scalars().all())

    by_state: dict[str, int] = defaultdict(int)
    rets: list[float] = []
    for r in rows:
        by_state[r.state] += 1
        if r.state in ("tp1", "tp2", "stopped", "expired"):
            rets.append(float(r.realized_return_pct))

    if not rets:
        return {"n": len(rows), "by_state": dict(by_state)}

    wins = sum(1 for r in rets if r > 0)
    return {
        "n": len(rows),
        "completed": len(rets),
        "by_state": dict(by_state),
        "win_rate": round(wins / len(rets) * 100, 1),
        "avg_return": round(sum(rets) / len(rets), 2),
        "best": round(max(rets), 2),
        "worst": round(min(rets), 2),
    }
