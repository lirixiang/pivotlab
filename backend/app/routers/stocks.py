from fastapi import APIRouter, HTTPException, Query

from ..schemas import StockDetail
from ..services.data_provider import get_candles, get_quote, list_universe
from ..services.levels import detect_levels

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


@router.get("/universe")
async def universe():
    return [{"code": c, "name": n, "industry": ind} for c, n, ind in list_universe()]


@router.get("/{code}", response_model=StockDetail)
async def stock_detail(
    code: str,
    period: str = Query("daily"),
    days: int = Query(180, ge=30, le=500),
    lookback: int = Query(120, ge=30, le=240),
    sensitivity: int = Query(5, ge=2, le=20),
):
    candles = get_candles(code, period=period, days=days)
    if not candles:
        raise HTTPException(404, "no candles")
    quote = get_quote(code)
    levels = detect_levels(candles, lookback=lookback, sensitivity=sensitivity)
    return StockDetail(quote=quote, candles=candles, levels=levels)
