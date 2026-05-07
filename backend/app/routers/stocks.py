import asyncio
import json

from fastapi import APIRouter, HTTPException, Query, Body

from ..schemas import StockDetail, Candle
from ..services.data_provider import (
    get_candles, get_quote, list_universe,
    refresh_candles_full, refresh_candles_latest, _run_in_net_executor,
)
from ..services.levels import detect_levels
from ..services.levels_multifactor import detect_levels_multifactor, get_available_factors
from ..services import sync_service

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


@router.get("/universe")
async def universe():
    return [{"code": c, "name": n, "industry": ind} for c, n, ind in list_universe()]


@router.get("/search")
async def search_stocks(q: str = Query("", min_length=1, max_length=20), limit: int = Query(15, ge=1, le=50)):
    """Search stocks from DB by code/name/industry (全量 A 股)."""
    from sqlalchemy import or_
    from ..models import Stock
    from ..services.sync_service import _get_session

    keyword = q.strip()
    if not keyword:
        return []
    with _get_session() as s:
        stmt = (
            s.query(Stock)
            .filter(
                or_(
                    Stock.code.contains(keyword),
                    Stock.name.contains(keyword),
                    Stock.industry.contains(keyword),
                )
            )
            .order_by(
                # Exact code match first, then name match
                Stock.code.asc(),
            )
            .limit(limit)
        )
        rows = stmt.all()
        return [
            {"code": r.code, "name": r.name, "industry": r.industry or "", "market": r.market or ""}
            for r in rows
        ]


@router.get("/meta/sr-factors")
async def sr_factors():
    """Return available multi-factor scoring factors and their default weights."""
    return get_available_factors()


@router.get("/{code}", response_model=StockDetail)
async def stock_detail(
    code: str,
    period: str = Query("daily"),
    days: int = Query(180, ge=30, le=500),
    lookback: int = Query(120, ge=30, le=240),
    sensitivity: int = Query(5, ge=2, le=20),
    algorithm: str = Query("multifactor", regex="^(classic|multifactor)$"),
    factor_weights: str = Query("", description="JSON dict of factor weights override"),
):
    try:
        candles, quote = await asyncio.wait_for(
            asyncio.gather(
                asyncio.to_thread(get_candles, code, period,
                                  days * 5 if period == "monthly" else days * 2 if period == "weekly" else days),
                asyncio.to_thread(get_quote, code),
            ),
            timeout=15.0,
        )
    except asyncio.TimeoutError:
        raise HTTPException(504, "data fetch timeout")
    if not candles:
        raise HTTPException(404, "no candles")

    # Append today's live candle from quote if not already in candles
    if quote and quote.price > 0 and period == "daily":
        from datetime import date as _date
        today_str = _date.today().strftime("%Y-%m-%d")
        last_date = candles[-1].date[:10] if candles else ""
        if last_date != today_str and quote.open > 0:
            candles.append(Candle(
                date=today_str,
                open=quote.open,
                high=quote.high if quote.high > 0 else quote.price,
                low=quote.low if quote.low > 0 else quote.price,
                close=quote.price,
                volume=quote.volume,
            ))
        elif last_date == today_str:
            # Update today's candle with latest quote data
            candles[-1] = Candle(
                date=today_str,
                open=quote.open if quote.open > 0 else candles[-1].open,
                high=max(candles[-1].high, quote.high if quote.high > 0 else quote.price),
                low=min(candles[-1].low, quote.low if quote.low > 0 else quote.price) if candles[-1].low > 0 else quote.price,
                close=quote.price,
                volume=quote.volume if quote.volume > 0 else candles[-1].volume,
            )

    if algorithm == "multifactor":
        weights = None
        if factor_weights:
            try:
                weights = json.loads(factor_weights)
            except (json.JSONDecodeError, TypeError):
                pass
        levels = detect_levels_multifactor(
            candles, lookback=lookback, sensitivity=sensitivity,
            factor_weights=weights,
        )
    else:
        levels = detect_levels(candles, lookback=lookback, sensitivity=sensitivity)

    # Enrich quote with DB data (stock info, fundamentals, concepts)
    stock_info = sync_service.get_stock_info(code)
    if stock_info:
        if stock_info.get("industry"):
            quote.industry = stock_info["industry"]
        if stock_info.get("market"):
            quote.market = stock_info["market"]

    quote_cache = sync_service.get_quote_cache(code)
    if quote_cache:
        quote.open = quote_cache.get("open", 0.0)
        quote.high = quote_cache.get("high", 0.0)
        quote.low = quote_cache.get("low", 0.0)
        quote.prev_close = quote_cache.get("prev_close", 0.0)
        quote.turnover_rate = quote_cache.get("turnover_rate", 0.0)
        quote.pe_ratio = quote_cache.get("pe_ratio", 0.0)
        quote.market_cap = quote_cache.get("market_cap", 0.0)

    quote.concepts = sync_service.get_stock_concepts(code)
    quote.fundamentals = sync_service.get_financial_snapshot(code)
    quote.analyst_consensus = sync_service.get_analyst_consensus(code)

    return StockDetail(quote=quote, candles=candles, levels=levels)


@router.post("/{code}/refresh-candles")
async def refresh_candles(
    code: str,
    mode: str = Body("latest", embed=True),
):
    """Refresh candle data for a single stock.
    mode='latest': re-fetch recent data.
    mode='full': clear cache and sync full history.
    """
    try:
        if mode == "full":
            count = await _run_in_net_executor(refresh_candles_full, code, timeout=30.0)
        else:
            count = await _run_in_net_executor(refresh_candles_latest, code, timeout=30.0)
    except asyncio.TimeoutError:
        raise HTTPException(504, "数据源请求超时，请稍后重试")
    except Exception as e:
        raise HTTPException(500, f"刷新失败: {e}")
    return {"code": code, "mode": mode, "updated_count": count}
