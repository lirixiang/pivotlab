from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import WatchlistItem, QuoteCache, Stock
from ..schemas import WatchlistCreate
from ..services.data_provider import get_quote

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


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
    # Batch-load quotes and stock info
    quotes = {}
    qrows = (await db.execute(select(QuoteCache).where(QuoteCache.code.in_(codes)))).scalars().all()
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
            "name": r.name or (s.name if s else ""),
            "note": r.note,
            "industry": s.industry if s else "",
            "price": q.price if q else 0.0,
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
