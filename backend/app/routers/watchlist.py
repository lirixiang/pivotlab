from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import WatchlistItem
from ..schemas import WatchlistCreate, WatchlistOut
from ..services.data_provider import get_quote

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


@router.get("", response_model=list[WatchlistOut])
async def list_watchlist(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(WatchlistItem).order_by(WatchlistItem.created_at.desc()))).scalars().all()
    return [WatchlistOut.model_validate(r) for r in rows]


@router.post("", response_model=WatchlistOut)
async def add_watchlist(item: WatchlistCreate, db: AsyncSession = Depends(get_db)):
    existing = (await db.execute(select(WatchlistItem).where(WatchlistItem.code == item.code))).scalar_one_or_none()
    if existing:
        return WatchlistOut.model_validate(existing)
    name = item.name
    if not name:
        try:
            name = get_quote(item.code).name
        except Exception:
            name = item.code
    row = WatchlistItem(code=item.code, name=name, note=item.note)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return WatchlistOut.model_validate(row)


@router.delete("/{code}")
async def remove_watchlist(code: str, db: AsyncSession = Depends(get_db)):
    row = (await db.execute(select(WatchlistItem).where(WatchlistItem.code == code))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "not found")
    await db.delete(row)
    await db.commit()
    return {"ok": True}
