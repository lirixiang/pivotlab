import asyncio
import json

from fastapi import APIRouter, HTTPException, Query, Body

from ..schemas import StockDetail, Candle
from ..services.data_provider import (
    get_candles, get_quote, list_universe,
    refresh_candles_full, refresh_candles_latest, _run_in_net_executor,
    _now_cst,
)
from ..services.levels import detect_levels
from ..services.levels_multifactor import detect_levels_multifactor, get_available_factors
from ..services import sync_service

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


def _trade_minutes_elapsed(now=None) -> int:
    """A股已开盘累计分钟(0~240)。9:30-11:30 + 13:00-15:00,周末/盘前/盘后会饱和到边界。"""
    from datetime import time as _time
    n = now or _now_cst()
    if n.weekday() >= 5:
        return 240  # 周末按全日计,即不放大
    t = n.time()
    am_open, am_close = _time(9, 30), _time(11, 30)
    pm_open, pm_close = _time(13, 0), _time(15, 0)
    mins = 0
    if t >= am_open:
        end = min(t, am_close)
        mins += max(0, (end.hour - 9) * 60 + (end.minute - 30))
    if t >= pm_open:
        end = min(t, pm_close)
        mins += max(0, (end.hour - 13) * 60 + end.minute)
    return min(240, max(0, mins))


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
    min_score: float = Query(20, ge=0, le=100, description="Minimum score threshold for SR levels"),
):
    try:
        candles, quote = await asyncio.wait_for(
            asyncio.gather(
                asyncio.to_thread(get_candles, code, period,
                                  days * 10 if period == "quarterly" else days * 5 if period == "monthly" else days * 2 if period == "weekly" else days),
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
        from ..services.data_provider import _is_trade_hours
        now_cst = _now_cst()
        today_str = now_cst.strftime("%Y-%m-%d")
        is_weekday = now_cst.weekday() < 5
        last_date = candles[-1].date[:10] if candles else ""
        # quote.volume 来自 EM clist f5,单位「股」;daily_candles 与历史 K 线
        # 数据现已统一以「股」存储,无需再换算。
        live_vol = float(quote.volume or 0)
        # 盘中按已开盘分钟数线性外推到全日,让今日量柱可以直接和历史比较。
        # 收盘后(elapsed=240)倍数=1,等于不放大。
        elapsed = _trade_minutes_elapsed()
        if elapsed > 0 and elapsed < 240 and live_vol > 0:
            live_vol = live_vol * (240.0 / elapsed)
        if is_weekday and last_date != today_str and quote.open > 0:
            candles.append(Candle(
                date=today_str,
                open=quote.open,
                high=quote.high if quote.high > 0 else quote.price,
                low=quote.low if quote.low > 0 else quote.price,
                close=quote.price,
                volume=live_vol,
                estimated=(0 < elapsed < 240),
            ))
        elif last_date == today_str and _is_trade_hours():
            # Update today's candle with latest quote data
            candles[-1] = Candle(
                date=today_str,
                open=quote.open if quote.open > 0 else candles[-1].open,
                high=max(candles[-1].high, quote.high if quote.high > 0 else quote.price),
                low=min(candles[-1].low, quote.low if quote.low > 0 else quote.price) if candles[-1].low > 0 else quote.price,
                close=quote.price,
                volume=live_vol if live_vol > 0 else candles[-1].volume,
                estimated=(0 < elapsed < 240),
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
            factor_weights=weights, min_score=min_score,
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

    quote.industry_pe = sync_service.get_industry_pe(code)
    quote.concepts = sync_service.get_stock_concepts(code)
    quote.concept_details = sync_service.get_stock_concept_details(code)
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


@router.get("/{code}/financial-history")
async def get_financial_history(code: str):
    """Get historical quarterly financial data for a stock."""
    data = sync_service.get_financial_history(code)
    return {"code": code, "history": data}
