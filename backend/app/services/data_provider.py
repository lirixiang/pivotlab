"""Data provider: multi-source K-line and quote provider with DB cache.

Data source priority:
  1. Database cache (SQLAlchemy — supports SQLite and PostgreSQL)
  2. Tencent Finance (qt.gtimg.cn / web.ifzq.gtimg.cn) — primary source
  3. East Money direct HTTP — fallback for K-lines

Key design patterns:
  - DB-first: return cached data immediately, refresh in background
  - Dedicated thread pools: isolate network I/O from API handlers
  - Per-day refresh tracking: avoid redundant fetches
  - Strategy pattern for multi-database support (SQLite / PostgreSQL)
  - No akshare dependency: all data via direct HTTP
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Iterable, Optional

import requests as _req
from sqlalchemy import create_engine, select, delete, text
from sqlalchemy.orm import Session

from ..schemas import Candle, IndexQuote, StockQuote
from ..database import DATABASE_URL
from . import tencent_provider

logger = logging.getLogger(__name__)


# ---------- Dedicated thread pools ----------
_net_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="net")
_sync_executor = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="sync")

_HTTP_TIMEOUT = 10
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def _is_trade_hours() -> bool:
    """Return True if within A-share trading window (weekday 09:15–15:10 CST)."""
    now = datetime.now()
    if now.weekday() >= 5:          # Saturday / Sunday
        return False
    t = now.hour * 100 + now.minute
    return 915 <= t <= 1510


def _market_closed_today() -> bool:
    """Return True if today is a weekday and market already closed (after 15:10)."""
    now = datetime.now()
    if now.weekday() >= 5:
        return False  # weekend — not 'closed today', just non-trading
    return now.hour * 100 + now.minute > 1510


async def _run_in_net_executor(fn, *args, timeout: float = _HTTP_TIMEOUT):
    """Run a sync function in the dedicated network thread pool with timeout."""
    loop = asyncio.get_running_loop()
    fut = loop.run_in_executor(_net_executor, fn, *args)
    return await asyncio.wait_for(fut, timeout=timeout)


# ---------- Candle cache DB (SQLAlchemy sync — works with SQLite & PostgreSQL) ----------
# We use a synchronous engine for cache ops because get_candles is called from
# sync threads. The async main engine handles the rest (watchlist, scan_results).
_candle_refresh_done: set[str] = set()


def _build_sync_url(async_url: str) -> str:
    """Convert async DB URL to sync equivalent."""
    if async_url.startswith("sqlite+aiosqlite"):
        sync_url = async_url.replace("sqlite+aiosqlite", "sqlite", 1)
        # Ensure data directory exists for SQLite
        parts = sync_url.split("///", 1)
        if len(parts) == 2:
            db_path = parts[1]
            if not os.path.isabs(db_path):
                base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                db_path = os.path.join(base, db_path)
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            sync_url = f"sqlite:///{db_path}"
        return sync_url
    if async_url.startswith("postgresql+asyncpg"):
        return async_url.replace("postgresql+asyncpg", "postgresql+psycopg2", 1)
    return async_url


_sync_engine = create_engine(_build_sync_url(DATABASE_URL), echo=False, pool_pre_ping=True)

# Ensure DailyCandle table exists (import model so metadata has it)
from ..models import DailyCandle  # noqa: E402
DailyCandle.metadata.create_all(_sync_engine)


def _cache_read(code: str, limit: int = 500) -> list[Candle]:
    stmt = (
        select(DailyCandle)
        .where(DailyCandle.code == code)
        .order_by(DailyCandle.trade_date.desc())
        .limit(limit)
    )
    with Session(_sync_engine) as session:
        rows = session.execute(stmt).scalars().all()
    if not rows:
        return []
    rows = sorted(rows, key=lambda r: r.trade_date)
    return [Candle(date=r.trade_date, open=r.open, high=r.high, low=r.low, close=r.close, volume=r.volume) for r in rows]


def _cache_save(code: str, candles: list[Candle]) -> None:
    if not candles:
        return
    with Session(_sync_engine) as session:
        is_pg = str(_sync_engine.url).startswith("postgresql")
        if is_pg:
            # PostgreSQL: use ON CONFLICT upsert
            sql = text(
                "INSERT INTO daily_candles (code, trade_date, open, high, low, close, volume) "
                "VALUES (:code, :trade_date, :open, :high, :low, :close, :volume) "
                "ON CONFLICT (code, trade_date) DO UPDATE SET "
                "open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, "
                "close=EXCLUDED.close, volume=EXCLUDED.volume"
            )
        else:
            # SQLite: use INSERT OR REPLACE
            sql = text(
                "INSERT OR REPLACE INTO daily_candles (code, trade_date, open, high, low, close, volume) "
                "VALUES (:code, :trade_date, :open, :high, :low, :close, :volume)"
            )
        for c in candles:
            session.execute(sql, {
                "code": code, "trade_date": c.date,
                "open": c.open, "high": c.high, "low": c.low,
                "close": c.close, "volume": c.volume,
            })
        session.commit()


def _cache_delete(code: str) -> None:
    """Remove all cached candles for a stock (used by full refresh)."""
    with Session(_sync_engine) as session:
        session.execute(delete(DailyCandle).where(DailyCandle.code == code))
        session.commit()


# ---------- Multi-source candle fetchers (direct HTTP, no akshare) ----------

_TX_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
_EM_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"


def _tx_symbol(code: str) -> str:
    """Convert 6-digit code to Tencent symbol."""
    return f"sh{code}" if code[:1] in ("5", "6", "9") else f"sz{code}"


def _em_secid(code: str) -> str:
    """Convert 6-digit code to EM secid (market.code)."""
    if code.startswith(("6", "5", "9")):
        return f"1.{code}"
    return f"0.{code}"


def _fetch_candles_tencent(code: str, start: str, days: int) -> list[Candle]:
    """Fetch daily candles from Tencent direct HTTP API."""
    try:
        symbol = _tx_symbol(code)
        beg = f"{start[:4]}-{start[4:6]}-{start[6:]}" if len(start) == 8 else start
        r = _req.get(
            _TX_KLINE_URL,
            params={"param": f"{symbol},day,{beg},2050-12-31,{days * 2},qfq"},
            headers=_HEADERS, timeout=_HTTP_TIMEOUT,
        )
        if r.status_code == 200:
            data = r.json().get("data", {})
            stock = data.get(symbol.lower(), data.get(symbol, {}))
            klines = stock.get("qfqday") or stock.get("day") or []
            if klines:
                return [
                    Candle(date=k[0], open=float(k[1]), close=float(k[2]),
                           high=float(k[3]), low=float(k[4]),
                           volume=int(float(k[5])))
                    for k in klines if len(k) >= 6
                ]
    except Exception as e:
        logger.debug("tencent kline failed for %s: %s", code, e)
    return []


def _fetch_candles_em(code: str, start: str, days: int, period: str = "101") -> list[Candle]:
    """Fetch candles from East Money direct HTTP API.
    
    period: 101=daily, 102=weekly, 103=monthly
    """
    try:
        beg = start.replace("-", "")[:8]
        r = _req.get(
            _EM_KLINE_URL,
            params={
                "secid": _em_secid(code),
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "klt": period, "fqt": "1",
                "beg": beg, "end": "20501231",
            },
            headers=_HEADERS, timeout=_HTTP_TIMEOUT,
        )
        if r.status_code == 200:
            data = r.json().get("data")
            if data and data.get("klines"):
                return [
                    Candle(date=parts[0], open=float(parts[1]), close=float(parts[2]),
                           high=float(parts[3]), low=float(parts[4]),
                           volume=int(float(parts[5])))
                    for line in data["klines"]
                    for parts in [line.split(",")]
                    if len(parts) >= 6
                ]
    except Exception as e:
        logger.debug("EM kline failed for %s: %s", code, e)
    return []


def _resample_candles(candles: list[Candle], period: str) -> list[Candle]:
    """Resample daily candles to weekly, monthly, or quarterly."""
    if not candles:
        return []
    result: list[Candle] = []
    bucket: list[Candle] = []

    def flush():
        if not bucket:
            return
        result.append(Candle(
            date=bucket[-1].date,  # use last day's date as the period label
            open=bucket[0].open,
            high=max(c.high for c in bucket),
            low=min(c.low for c in bucket),
            close=bucket[-1].close,
            volume=sum(c.volume for c in bucket),
        ))

    for c in candles:
        try:
            d = datetime.strptime(c.date[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if period == "weekly":
            # Group by ISO week: flush when week changes
            if bucket:
                prev_d = datetime.strptime(bucket[-1].date[:10], "%Y-%m-%d").date()
                if d.isocalendar()[1] != prev_d.isocalendar()[1] or d.year != prev_d.year:
                    flush()
                    bucket = []
        elif period == "monthly":
            if bucket:
                prev_d = datetime.strptime(bucket[-1].date[:10], "%Y-%m-%d").date()
                if d.month != prev_d.month or d.year != prev_d.year:
                    flush()
                    bucket = []
        elif period == "quarterly":
            if bucket:
                prev_d = datetime.strptime(bucket[-1].date[:10], "%Y-%m-%d").date()
                if (d.month - 1) // 3 != (prev_d.month - 1) // 3 or d.year != prev_d.year:
                    flush()
                    bucket = []
        bucket.append(c)
    flush()
    return result


def _fetch_candles_sync(code: str, days: int = 240) -> list[Candle]:
    """Fetch daily candles: try Tencent first, then East Money. Direct HTTP, no akshare."""
    start = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")

    # --- Source 1: Tencent (fast + stable) ---
    result = _fetch_candles_tencent(code, start, days)
    if result:
        return result[-days:]

    # --- Source 2: East Money fallback ---
    result = _fetch_candles_em(code, start, days)
    if result:
        return result[-days:]

    return []


def _normalize_code(code: str) -> str:
    code = code.strip().upper()
    if "." in code:
        code = code.split(".")[0]
    return code


def _get_stock_info_from_db(code: str) -> tuple[str, str]:
    """Return (name, industry) from stocks table."""
    from ..models import Stock
    try:
        with Session(_sync_engine) as s:
            stock = s.get(Stock, code)
            if stock:
                return stock.name or "", stock.industry or ""
    except Exception:
        pass
    return "", ""


def list_universe() -> list[tuple[str, str, str]]:
    """Return all stocks from the DB stocks table."""
    from ..models import Stock
    try:
        with Session(_sync_engine) as s:
            rows = s.query(Stock.code, Stock.name, Stock.industry).all()
            return [(r.code, r.name or "", r.industry or "") for r in rows]
    except Exception as e:
        logger.warning("list_universe from DB failed: %s", e)
        return []


# ---------- TTL snapshot caches ----------
# Whole-market quote/index calls are heavy (1-3s, ~5000 rows each).
# Cache them in-process so per-stock quote calls reuse the snapshot within `_TTL`.
_TTL = 60.0  # seconds
_quote_snapshot: dict[str, StockQuote] = {}
_quote_snapshot_at: float = 0.0
_index_snapshot: list[IndexQuote] = []
_index_snapshot_at: float = 0.0


def _get_watched_codes() -> list[str]:
    """Return codes from watchlist table."""
    from ..models import WatchlistItem
    try:
        with Session(_sync_engine) as s:
            rows = s.query(WatchlistItem.code).all()
            return [r.code for r in rows]
    except Exception:
        return []


def _refresh_quote_snapshot() -> None:
    """Populate `_quote_snapshot` from Tencent Finance."""
    global _quote_snapshot, _quote_snapshot_at
    snap: dict[str, StockQuote] = {}

    # Determine which codes to fetch: watchlist items
    codes = _get_watched_codes()
    if not codes:
        _quote_snapshot = snap
        _quote_snapshot_at = time.time()
        return

    # Build name/industry lookup from DB
    from ..models import Stock
    name_by_code: dict[str, str] = {}
    industry_by_code: dict[str, str] = {}
    try:
        with Session(_sync_engine) as s:
            for stock in s.query(Stock).filter(Stock.code.in_(codes)).all():
                name_by_code[stock.code] = stock.name or ""
                industry_by_code[stock.code] = stock.industry or ""
    except Exception:
        pass

    # --- Tencent Finance (fast, reliable, only source needed) ---
    try:
        tencent_quotes = tencent_provider.fetch_quotes(codes)
        for q in tencent_quotes:
            code = q["code"]
            snap[code] = StockQuote(
                code=code,
                name=q.get("name") or name_by_code.get(code, code),
                price=q.get("price", 0),
                change=q.get("change_amt", 0),
                change_pct=q.get("change_pct", 0),
                volume=q.get("volume", 0),
                amount=q.get("amount", 0),
                volume_ratio=0.0,
                turnover=q.get("turnover_rate", 0),
                industry=industry_by_code.get(code, ""),
                open=q.get("open", 0),
                high=q.get("high", 0),
                low=q.get("low", 0),
                prev_close=q.get("prev_close", 0),
            )
    except Exception as e:
        logger.info("tencent quotes failed: %s", e)

    _quote_snapshot = snap
    _quote_snapshot_at = time.time()


def _ensure_quote_snapshot() -> None:
    # Outside trading hours, don't refresh if we already have data
    if _quote_snapshot and not _is_trade_hours():
        return
    if not _quote_snapshot or time.time() - _quote_snapshot_at > _TTL:
        _refresh_quote_snapshot()


def get_candles(code: str, period: str = "daily", days: int = 240) -> list[Candle]:
    """Return candles with DB-first strategy + background refresh.

    Logic:
    1. Read from DB cache
    2. If cache has data but may be stale → return immediately, fire background refresh
    3. If cache is empty → wait for network fetch, then return
    """
    code = _normalize_code(code)

    # Non-daily periods: resample from DB daily cache first, then network
    if period != "daily":
        fetch_days = days * 4 if period == "quarterly" else days * 2

        # 1. Try DB-cached daily candles first (fastest, no network)
        cached_daily = _cache_read(code, limit=fetch_days)
        if cached_daily:
            resampled = _resample_candles(cached_daily, period)
            if resampled:
                return resampled[-days:]

        # 2. Try Tencent daily → resample
        start = (datetime.now() - timedelta(days=fetch_days)).strftime("%Y%m%d")
        daily = _fetch_candles_tencent(code, start, fetch_days)
        if daily:
            resampled = _resample_candles(daily, period)
            if resampled:
                return resampled[-days:]

        # 3. EM natively supports weekly (102) / monthly (103)
        em_period = {"weekly": "102", "monthly": "103"}.get(period)
        if em_period:
            result = _fetch_candles_em(code, start, days, period=em_period)
            if result:
                return result[-days:]
        return []

    # --- Daily candles: DB cache + background refresh ---
    cached = _cache_read(code, limit=days)
    today = datetime.now().strftime("%Y%m%d")
    cache_key = f"{code}:{today}"

    if cached and cache_key in _candle_refresh_done:
        # Already refreshed today, return cached
        return cached[-days:]

    # Outside trading hours with cached data → no refresh needed
    if cached and not _is_trade_hours():
        _candle_refresh_done.add(cache_key)
        return cached[-days:]

    if cached:
        # Have data but may be stale — do inline refresh (fast, ~1s)
        # After market close, only refresh once to get final closing data
        if cache_key not in _candle_refresh_done:
            _candle_refresh_done.add(cache_key)
            try:
                new_candles = _fetch_candles_sync(code, days)
                if new_candles:
                    _cache_save(code, new_candles)
                    return new_candles[-days:]
            except Exception as e:
                logger.debug("inline refresh failed for %s: %s, using cache", code, e)
        return cached[-days:]

    # No data at all — must wait for network fetch
    new_candles = _fetch_candles_sync(code, days)
    if new_candles:
        _cache_save(code, new_candles)
        _candle_refresh_done.add(cache_key)
        return new_candles[-days:]

    _candle_refresh_done.add(cache_key)
    return []


def _bg_refresh_candles(code: str, days: int = 240) -> None:
    """Background task to refresh candle data without blocking the response."""
    try:
        new_candles = _fetch_candles_sync(code, days)
        if new_candles:
            _cache_save(code, new_candles)
            logger.debug("bg refresh candles done for %s (%d rows)", code, len(new_candles))
    except Exception as e:
        logger.debug("bg refresh candles failed for %s: %s", code, e)


def refresh_candles_full(code: str, days: int = 500) -> int:
    """Force full re-fetch of candles for a stock. Clears cache first."""
    code = _normalize_code(code)
    _cache_delete(code)
    today = datetime.now().strftime("%Y%m%d")
    _candle_refresh_done.discard(f"{code}:{today}")

    new_candles = _fetch_candles_sync(code, days)
    if new_candles:
        _cache_save(code, new_candles)
        _candle_refresh_done.add(f"{code}:{today}")
        return len(new_candles)
    return 0


def refresh_candles_latest(code: str) -> int:
    """Re-fetch latest candles for a stock (incremental)."""
    code = _normalize_code(code)
    today = datetime.now().strftime("%Y%m%d")
    _candle_refresh_done.discard(f"{code}:{today}")

    new_candles = _fetch_candles_sync(code, 240)
    if new_candles:
        _cache_save(code, new_candles)
        _candle_refresh_done.add(f"{code}:{today}")
        return len(new_candles)
    return 0


def _fetch_single_quote(code: str) -> Optional[StockQuote]:
    """Fetch a single stock quote from Tencent when not in snapshot."""
    name, industry = _get_stock_info_from_db(code)
    try:
        quotes = tencent_provider.fetch_quotes([code])
        if quotes:
            q = quotes[0]
            return StockQuote(
                code=code,
                name=q.get("name") or name or code,
                price=q.get("price", 0),
                change=q.get("change_amt", 0),
                change_pct=q.get("change_pct", 0),
                volume=q.get("volume", 0),
                amount=q.get("amount", 0),
                volume_ratio=0.0,
                turnover=q.get("turnover_rate", 0),
                industry=industry,
                open=q.get("open", 0),
                high=q.get("high", 0),
                low=q.get("low", 0),
                prev_close=q.get("prev_close", 0),
            )
    except Exception as e:
        logger.debug("tencent single quote failed for %s: %s", code, e)
    return None


def get_quote(code: str) -> StockQuote:
    code = _normalize_code(code)
    _ensure_quote_snapshot()
    if code in _quote_snapshot:
        return _quote_snapshot[code]
    # Not in snapshot — fetch directly from Tencent
    sq = _fetch_single_quote(code)
    if sq:
        _quote_snapshot[code] = sq
        return sq
    # Final fallback: return stub with DB name
    name, industry = _get_stock_info_from_db(code)
    return StockQuote(
        code=code, name=name or code, price=0, change=0, change_pct=0,
        volume=0, amount=0, volume_ratio=0, turnover=0, industry=industry,
    )


def get_quotes_bulk(codes: Iterable[str]) -> dict[str, StockQuote]:
    """Resolve many codes against the shared snapshot in one shot."""
    _ensure_quote_snapshot()
    out: dict[str, StockQuote] = {}
    for code in codes:
        c = _normalize_code(code)
        if c in _quote_snapshot:
            out[c] = _quote_snapshot[c]
    return out


def get_indices() -> list[IndexQuote]:
    global _index_snapshot, _index_snapshot_at
    if _index_snapshot and time.time() - _index_snapshot_at <= _TTL:
        return _index_snapshot
    items: list[tuple[str, str]] = [
        ("000001", "上证指数"),
        ("399001", "深证成指"),
        ("399006", "创业板指"),
    ]

    # --- Tencent Finance (only source needed) ---
    try:
        tq = tencent_provider.fetch_index_quotes()
        if tq:
            results = [
                IndexQuote(
                    code=q["code"], name=q["name"],
                    price=q["price"], change_pct=q["change_pct"],
                )
                for q in tq
            ]
            if results:
                _index_snapshot = results
                _index_snapshot_at = time.time()
                return results
    except Exception as e:
        logger.info("tencent indices failed: %s", e)

    return []


def preload_candles(days: int = 240) -> None:
    """Pre-fetch candles for watchlist stocks. Called by background scheduler."""
    codes = _get_watched_codes()
    logger.info("preload_candles: starting for %d stocks", len(codes))
    for code in codes:
        try:
            get_candles(code, days=days)
        except Exception as e:
            logger.warning("preload_candles: %s failed: %s", code, e)
    logger.info("preload_candles: done")
