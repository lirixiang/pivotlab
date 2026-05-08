import asyncio
import logging
import time
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import WatchlistItem, DailyCandle, Stock
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

    result = []
    for r in rows:
        q = quotes.get(r.code)
        s = stocks.get(r.code)
        result.append({
            "id": r.id,
            "code": r.code,
            "name": (s.name if s else "") or r.name,
            "note": r.note,
            "industry": s.industry if s else "",
            "price": q.close if q else 0.0,
            "change_pct": q.change_pct if q else 0.0,
            "volume": q.volume if q else 0.0,
            "amount": q.amount if q else 0.0,
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
