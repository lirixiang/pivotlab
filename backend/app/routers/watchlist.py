import asyncio
import json
import logging
import os
import time
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import WatchlistItem, DailyCandle, Stock, FinancialSnapshot, StockConcept
from ..schemas import WatchlistCreate
from ..services.data_provider import get_candles, get_quote
from ..services.levels_multifactor import (
    compute_decision_score,
    detect_levels_multifactor,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])

# In-memory cache for decision scores (code -> (score, label, ts))
_score_cache: dict[str, tuple[float, str, float]] = {}
_SCORE_TTL = 300  # 5 min cache


# Screener cache directory (relative to backend root)
_SCREENER_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ".screener_cache",
)
_PATTERN_LABELS = {
    "breakout_pullback": "突破回踩",
    "bottom_stabilize": "下跌企稳",
    "stabilize": "下跌企稳",
    "box_support": "箱体支撑",
    "volume_breakout": "放量突破",
    "macd_divergence": "MACD背离",
    "near_support": "靠近支撑",
}


def _load_screener_hits() -> dict[str, dict]:
    """Return code -> best screener hit across all patterns (highest score wins)."""
    out: dict[str, dict] = {}
    if not os.path.isdir(_SCREENER_CACHE_DIR):
        return out
    for fname in os.listdir(_SCREENER_CACHE_DIR):
        if not fname.endswith(".json") or "_2" in fname:  # skip history snapshots
            continue
        pattern = fname[:-5]
        try:
            with open(os.path.join(_SCREENER_CACHE_DIR, fname)) as f:
                data = json.load(f)
            for it in data.get("items", []):
                code = it.get("code", "")
                if not code:
                    continue
                score = it.get("score", 0) or 0
                prev = out.get(code)
                if prev is None or score > prev.get("score", 0):
                    out[code] = {**it, "pattern": pattern,
                                 "pattern_label": _PATTERN_LABELS.get(pattern, pattern)}
        except Exception:
            continue
    return out


@router.get("")
async def list_watchlist(db: AsyncSession = Depends(get_db)):
    """Return watchlist items enriched with latest quote data."""
    rows = (
        await db.execute(
            select(WatchlistItem).order_by(WatchlistItem.created_at.desc())
        )
    ).scalars().all()
    if not rows:
        return []

    codes = [r.code for r in rows]
    # Batch-load today's candle data (replaces quote_cache)
    from datetime import date as _date
    today = _date.today().strftime("%Y-%m-%d")
    quotes = {}
    qrows = (await db.execute(
        select(DailyCandle).where(DailyCandle.code.in_(codes), DailyCandle.trade_date == today)
    )).scalars().all()
    for q in qrows:
        quotes[q.code] = q

    stocks = {}
    srows = (await db.execute(select(Stock).where(Stock.code.in_(codes)))).scalars().all()
    for s in srows:
        stocks[s.code] = s

    # Financial snapshots
    fins: dict[str, FinancialSnapshot] = {}
    frows = (await db.execute(
        select(FinancialSnapshot).where(FinancialSnapshot.code.in_(codes))
    )).scalars().all()
    for f in frows:
        fins[f.code] = f

    # Concepts: top 3 per code
    concepts_map: dict[str, list[str]] = {}
    crows = (await db.execute(
        select(StockConcept).where(StockConcept.code.in_(codes)).order_by(StockConcept.code, StockConcept.id)
    )).scalars().all()
    for c in crows:
        lst = concepts_map.setdefault(c.code, [])
        if len(lst) < 3 and c.concept:
            lst.append(c.concept)

    # Screener best-hit per code
    hits = _load_screener_hits()

    # Sparkline: last 30 closes per code
    sparks: dict[str, list[float]] = {}
    spark_rows = (await db.execute(
        select(DailyCandle.code, DailyCandle.trade_date, DailyCandle.close)
        .where(DailyCandle.code.in_(codes))
        .order_by(DailyCandle.code, DailyCandle.trade_date.desc())
    )).all()
    for code, _td, close in spark_rows:
        lst = sparks.setdefault(code, [])
        if len(lst) < 30 and close is not None:
            lst.append(round(float(close), 3))
    for code in sparks:
        sparks[code].reverse()

    result = []
    for r in rows:
        q = quotes.get(r.code)
        s = stocks.get(r.code)
        f = fins.get(r.code)
        hit = hits.get(r.code)
        result.append({
            "id": r.id,
            "code": r.code,
            "name": (s.name if s else "") or r.name,
            "note": r.note,
            "industry": s.industry if s else "",
            "market": s.market if s else "",
            "price": (q.close if q else None) or 0.0,
            "change_pct": (q.change_pct if q else None) or 0.0,
            "volume": (q.volume if q else None) or 0.0,
            "amount": (q.amount if q else None) or 0.0,
            "turnover_rate": (q.turnover_rate if q else None) or 0.0,
            "pe": (q.pe_ratio if q and q.pe_ratio else (f.pe_ratio_ttm if f else None)),
            "market_cap": (q.market_cap if q else None) or 0.0,
            "roe": f.roe if f else None,
            "fundamental_status": (f.fundamental_status if f else "unknown") or "unknown",
            "fundamental_summary": (f.fundamental_summary if f else "") or "",
            "concepts": concepts_map.get(r.code, []),
            "sparkline": sparks.get(r.code, []),
            # Screener-derived fields (best hit across patterns)
            "score": hit.get("score") if hit else None,
            "pattern": hit.get("pattern") if hit else None,
            "pattern_label": hit.get("pattern_label") if hit else None,
            "triggers": hit.get("triggers", []) if hit else [],
            "distance_to_support_pct": hit.get("distance_to_support_pct") if hit else None,
            "rr_ratio": hit.get("rr_ratio") if hit else None,
            "support_score": hit.get("support_score") if hit else None,
            "volume_ratio": hit.get("volume_ratio") if hit else None,
            "created_at": r.created_at.isoformat() if r.created_at else "",
        })
    return result


@router.post("")
async def add_watchlist(item: WatchlistCreate, db: AsyncSession = Depends(get_db)):
    existing = (
        await db.execute(
            select(WatchlistItem).where(WatchlistItem.code == item.code)
        )
    ).scalar_one_or_none()
    if existing:
        return {"id": existing.id, "code": existing.code, "name": existing.name, "ok": True}
    name = item.name
    if not name:
        stock = (await db.execute(select(Stock).where(Stock.code == item.code))).scalar_one_or_none()
        if stock:
            name = stock.name
        else:
            try:
                name = get_quote(item.code).name
            except Exception:
                name = item.code
    row = WatchlistItem(code=item.code, name=name, note=item.note)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"id": row.id, "code": row.code, "name": row.name, "ok": True}


@router.delete("/{code}")
async def remove_watchlist(code: str, db: AsyncSession = Depends(get_db)):
    row = (
        await db.execute(
            select(WatchlistItem).where(WatchlistItem.code == code)
        )
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "not found")
    await db.delete(row)
    await db.commit()
    return {"ok": True}


def _compute_one(code: str) -> tuple[str, float, str]:
    """Compute decision score for a single stock (runs in thread)."""
    try:
        cached = _score_cache.get(code)
        if cached and time.time() - cached[2] < _SCORE_TTL:
            return code, cached[0], cached[1]

        candles = get_candles(code, period="daily", days=240)
        if not candles:
            return code, 0.0, "—"

        levels = detect_levels_multifactor(candles)
        price = candles[-1].close
        score, label = compute_decision_score(candles, levels, price)
        _score_cache[code] = (score, label, time.time())
        return code, score, label
    except Exception as exc:
        logger.warning("decision score failed for %s: %s", code, exc)
        return code, 0.0, "—"


@router.get("/scores")
async def watchlist_scores(db: AsyncSession = Depends(get_db)):
    """Return decision scores for all watchlist stocks."""
    rows = (
        await db.execute(
            select(WatchlistItem).order_by(WatchlistItem.created_at.desc())
        )
    ).scalars().all()
    if not rows:
        return []

    codes = [r.code for r in rows]
    loop = asyncio.get_running_loop()
    tasks = [loop.run_in_executor(None, _compute_one, c) for c in codes]
    results = await asyncio.gather(*tasks)

    return [
        {"code": code, "decision_score": score, "decision_label": label}
        for code, score, label in results
    ]
