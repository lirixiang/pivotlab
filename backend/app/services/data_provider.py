"""Data provider: multi-source K-line and quote provider with DB cache.

Data source priority:
  1. Database cache (SQLAlchemy — supports SQLite and PostgreSQL)
  2. Tencent Finance (qt.gtimg.cn) — reliable real-time source
  3. akshare / East Money — fallback with circuit breaker

Key design patterns (borrowed from stock-sr-platform):
  - DB-first: return cached data immediately, refresh in background
  - Circuit breaker: skip akshare after repeated failures
  - Dedicated thread pools: isolate network I/O from API handlers
  - Per-day refresh tracking: avoid redundant fetches
  - Strategy pattern for multi-database support (SQLite / PostgreSQL)
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Iterable, Optional

import pandas as pd
from sqlalchemy import create_engine, select, delete, text
from sqlalchemy.orm import Session

from ..schemas import Candle, IndexQuote, StockQuote
from ..database import DATABASE_URL
from . import tencent_provider

logger = logging.getLogger(__name__)

try:
    import akshare as ak  # type: ignore
    _HAS_AK = True
except Exception as e:  # pragma: no cover
    logger.warning("akshare unavailable: %s", e)
    ak = None  # type: ignore
    _HAS_AK = False


# ---------- Dedicated thread pools ----------
# Isolate slow network calls so they can't starve the default asyncio executor
_net_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="net")
_sync_executor = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="sync")

# ---------- Circuit breaker for akshare/EM ----------
_EM_TIMEOUT = 8.0
_em_fail_count = 0
_em_last_fail_time: Optional[datetime] = None
_EM_CIRCUIT_BREAK_SECONDS = 300  # 5 min cooldown after 2+ consecutive failures


def _em_is_available() -> bool:
    global _em_fail_count, _em_last_fail_time
    if _em_fail_count < 2:
        return True
    if _em_last_fail_time and (datetime.utcnow() - _em_last_fail_time).total_seconds() > _EM_CIRCUIT_BREAK_SECONDS:
        _em_fail_count = 0
        return True
    return False


def _em_record_failure():
    global _em_fail_count, _em_last_fail_time
    _em_fail_count += 1
    _em_last_fail_time = datetime.utcnow()


def _em_record_success():
    global _em_fail_count
    _em_fail_count = 0


async def _run_in_net_executor(fn, *args, timeout: float = _EM_TIMEOUT):
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


# ---------- Multi-source candle fetchers ----------

def _tx_symbol(code: str) -> str:
    """Convert 6-digit code to Tencent akshare symbol."""
    return f"sh{code}" if code[:1] in ("5", "6", "9") else f"sz{code}"


def _normalize_candle_df(df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    """Normalize Tencent-format columns to standard 日期/开盘/最高/最低/收盘/成交量."""
    if df is None or df.empty:
        return df
    if "日期" in df.columns:
        return df
    if "date" not in df.columns:
        return df
    normalized = df.copy()
    normalized["日期"] = pd.to_datetime(normalized["date"]).dt.strftime("%Y-%m-%d")
    normalized["开盘"] = normalized.get("open", 0)
    normalized["最高"] = normalized.get("high", 0)
    normalized["最低"] = normalized.get("low", 0)
    normalized["收盘"] = normalized.get("close", 0)
    normalized["成交量"] = normalized.get("volume", normalized.get("amount", 0))
    return normalized


def _df_to_candles(df: pd.DataFrame) -> list[Candle]:
    """Convert a normalized DataFrame to Candle list."""
    out: list[Candle] = []
    for _, row in df.iterrows():
        out.append(Candle(
            date=str(row.get("日期")),
            open=float(row.get("开盘", 0)),
            high=float(row.get("最高", 0)),
            low=float(row.get("最低", 0)),
            close=float(row.get("收盘", 0)),
            volume=float(row.get("成交量", 0)),
        ))
    return out


def _resample_candles(candles: list[Candle], period: str) -> list[Candle]:
    """Resample daily candles to weekly or monthly."""
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
        bucket.append(c)
    flush()
    return result


def _fetch_candles_sync(code: str, days: int = 240) -> list[Candle]:
    """Fetch daily candles: try Tencent first, then East Money. Runs in thread pool."""
    start = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
    end = datetime.now().strftime("%Y%m%d")

    # --- Source 1: Tencent (via akshare wrapper, more reliable connectivity) ---
    if _HAS_AK:
        try:
            df = ak.stock_zh_a_hist_tx(
                symbol=_tx_symbol(code),
                start_date=start,
                end_date=end,
                adjust="qfq",
                timeout=_EM_TIMEOUT,
            )
            df = _normalize_candle_df(df)
            if df is not None and not df.empty:
                result = _df_to_candles(df)
                if result:
                    return result[-days:]
        except Exception as e:
            logger.debug("tencent candles failed for %s: %s", code, e)

    # --- Source 2: East Money (with circuit breaker) ---
    if _HAS_AK and _em_is_available():
        try:
            df = ak.stock_zh_a_hist(
                symbol=code, period="daily",
                start_date=start, end_date=end, adjust="qfq",
            )
            if df is not None and not df.empty:
                _em_record_success()
                result = _df_to_candles(df)
                if result:
                    return result[-days:]
        except Exception as e:
            _em_record_failure()
            logger.debug("EM candles failed for %s: %s", code, e)

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
# Whole-market quote/index calls (akshare) are heavy (1-3s, ~5000 rows each).
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
    """Populate `_quote_snapshot` from Tencent (primary) or akshare (fallback)."""
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

    # --- Source 1: Tencent Finance (fast, reliable) ---
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
            )
    except Exception as e:
        logger.info("tencent quotes failed: %s", e)

    # --- Source 2: akshare/EM fallback ---
    if not snap and _HAS_AK and _em_is_available():
        try:
            df = ak.stock_zh_a_spot_em()
            _em_record_success()
            for _, r in df.iterrows():
                code = str(r.get("代码"))
                if code in set(codes):
                    snap[code] = StockQuote(
                        code=code,
                        name=str(r.get("名称") or name_by_code.get(code, code)),
                        price=float(r.get("最新价", 0) or 0),
                        change=float(r.get("涨跌额", 0) or 0),
                        change_pct=float(r.get("涨跌幅", 0) or 0),
                        volume=float(r.get("成交量", 0) or 0),
                        amount=float(r.get("成交额", 0) or 0),
                        volume_ratio=float(r.get("量比", 0) or 0),
                        turnover=float(r.get("换手率", 0) or 0),
                        industry=industry_by_code.get(code, ""),
                    )
        except Exception as e:
            _em_record_failure()
            logger.info("akshare snapshot failed: %s", e)
    _quote_snapshot = snap
    _quote_snapshot_at = time.time()


def _ensure_quote_snapshot() -> None:
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

    # Non-daily periods bypass cache
    if period != "daily":
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
        # Try Tencent first (via akshare wrapper, more reliable)
        if _HAS_AK:
            try:
                tx_period = {"weekly": "week", "monthly": "month"}.get(period)
                if tx_period:
                    df = ak.stock_zh_a_hist_tx(
                        symbol=_tx_symbol(code),
                        start_date=start,
                        end_date=end,
                        adjust="qfq",
                        timeout=_EM_TIMEOUT,
                    )
                    if df is not None and not df.empty:
                        # Tencent returns daily data; resample to weekly/monthly
                        df = _normalize_candle_df(df)
                        if df is not None and not df.empty:
                            result = _df_to_candles(df)
                            if result:
                                resampled = _resample_candles(result, period)
                                if resampled:
                                    return resampled[-days:]
            except Exception as e:
                logger.debug("tencent %s candles failed for %s: %s", period, code, e)
        # Fallback: EM natively supports weekly/monthly
        if _HAS_AK and _em_is_available():
            try:
                df = ak.stock_zh_a_hist(
                    symbol=code, period=period,
                    start_date=start, end_date=end, adjust="qfq",
                )
                if df is not None and not df.empty:
                    _em_record_success()
                    result = _df_to_candles(df)
                    if result:
                        return result[-days:]
            except Exception as e:
                _em_record_failure()
                logger.debug("EM %s candles failed for %s: %s", period, code, e)
        return []

    # --- Daily candles: DB cache + background refresh ---
    cached = _cache_read(code, limit=days)
    today = datetime.now().strftime("%Y%m%d")
    cache_key = f"{code}:{today}"

    if cached and cache_key in _candle_refresh_done:
        # Already refreshed today, return cached
        return cached[-days:]

    if cached:
        # Have data but may be stale — return immediately, refresh in background
        if cache_key not in _candle_refresh_done:
            _candle_refresh_done.add(cache_key)
            _sync_executor.submit(_bg_refresh_candles, code, days)
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

    # --- Source 1: Tencent Finance ---
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

    # --- Source 2: akshare fallback ---
    if _HAS_AK and _em_is_available():
        try:
            df = ak.stock_zh_index_spot_em(symbol="沪深重要指数")
            _em_record_success()
            results: list[IndexQuote] = []
            for code, name in items:
                row = df[df["代码"] == code]
                if len(row) > 0:
                    r = row.iloc[0]
                    results.append(IndexQuote(
                        code=code, name=name,
                        price=float(r.get("最新价", 0)),
                        change_pct=float(r.get("涨跌幅", 0)),
                    ))
            if results:
                _index_snapshot = results
                _index_snapshot_at = time.time()
                return results
        except Exception as e:
            _em_record_failure()
            logger.info("akshare indices failed: %s", e)
    # Return empty list if all sources fail
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
