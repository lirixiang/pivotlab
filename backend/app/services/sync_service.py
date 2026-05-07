"""Sync service – pulls market data from akshare & tencent into the DB.

Task types:
  - stocks:     A-share stock list (code, name, industry, market)
  - quotes:     Real-time quote snapshot via Tencent
  - candles:    Historical daily K-lines (existing preload logic)
  - financials: EPS, ROE, revenue/profit growth, PE
  - concepts:   Stock → concept/theme mapping
"""
from __future__ import annotations

import logging
import time
import traceback
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select, delete, text
from sqlalchemy.orm import Session

from ..models import (
    Stock, QuoteCache, FinancialSnapshot, StockConcept, SyncTask, DailyCandle,
    AnalystConsensus,
)
from ..database import DATABASE_URL
from . import tencent_provider

logger = logging.getLogger(__name__)

try:
    import akshare as ak
    _HAS_AK = True
except Exception:
    ak = None  # type: ignore
    _HAS_AK = False

# ── Sync engine (reuse data_provider's sync engine pattern) ──
from sqlalchemy import create_engine as _create_engine

def _build_sync_url() -> str:
    url = str(DATABASE_URL)
    url = url.replace("sqlite+aiosqlite", "sqlite")
    url = url.replace("postgresql+asyncpg", "postgresql+psycopg2")
    return url

_sync_engine = None

def _get_sync_engine():
    global _sync_engine
    if _sync_engine is None:
        _sync_engine = _create_engine(
            _build_sync_url(),
            pool_pre_ping=True,
            echo=False,
        )
    return _sync_engine


def _get_session() -> Session:
    return Session(_get_sync_engine())


# ═══════════════════════════════════════════════════════════════
#  Task tracking helpers
# ═══════════════════════════════════════════════════════════════

def _is_task_running(task_type: str) -> bool:
    """Check if a task of this type is already running."""
    with _get_session() as s:
        existing = s.execute(
            select(SyncTask).where(
                SyncTask.task_type == task_type,
                SyncTask.status == "running",
            )
        ).scalar_one_or_none()
        return existing is not None


def _create_task(task_type: str) -> int:
    with _get_session() as s:
        # Abort stale running tasks (>2 hours) for this type
        stale = s.execute(
            select(SyncTask).where(
                SyncTask.task_type == task_type,
                SyncTask.status == "running",
            )
        ).scalars().all()
        now = datetime.utcnow()
        for old in stale:
            if old.started_at and (now - old.started_at).total_seconds() > 7200:
                old.status = "error"
                old.error_msg = "stale: timed out after 2h"
                old.finished_at = now
        s.commit()

        # Check if there's still a running task
        running = s.execute(
            select(SyncTask).where(
                SyncTask.task_type == task_type,
                SyncTask.status == "running",
            )
        ).scalar_one_or_none()
        if running:
            return -1  # signal: already running

        t = SyncTask(
            task_type=task_type,
            status="running",
            started_at=now,
        )
        s.add(t)
        s.commit()
        return t.id


def _get_or_create_task(task_type: str, _task_id: int | None = None) -> int:
    """Use pre-created task_id from spawn_sync, or create new one."""
    if _task_id is not None and _task_id > 0:
        return _task_id
    return _create_task(task_type)


def _finish_task(task_id: int, processed: int, total: int, error: str = ""):
    with _get_session() as s:
        t = s.get(SyncTask, task_id)
        if t:
            t.status = "error" if error else "done"
            t.processed = processed
            t.total = total
            t.error_msg = error
            t.finished_at = datetime.utcnow()
            s.commit()


def _update_task_progress(task_id: int, processed: int, total: int):
    with _get_session() as s:
        t = s.get(SyncTask, task_id)
        if t:
            t.processed = processed
            t.total = total
            s.commit()


# ═══════════════════════════════════════════════════════════════
#  1. Stock list sync
# ═══════════════════════════════════════════════════════════════

def sync_stock_list(_task_id: int = None) -> int:
    """Sync A-share stock list from akshare."""
    task_id = _get_or_create_task("stocks", _task_id)
    if task_id == -1:
        logger.info("task already running, skipping")
        return -1
    if not _HAS_AK:
        _finish_task(task_id, 0, 0, "akshare unavailable")
        return task_id
    try:
        df = ak.stock_info_a_code_name()
        total = len(df)
        logger.info("sync_stock_list: fetched %d stocks", total)

        with _get_session() as s:
            now = datetime.utcnow()
            batch = []
            for _, row in df.iterrows():
                code = str(row.get("code", "")).strip()
                name = str(row.get("name", "")).strip()
                if not code or len(code) != 6:
                    continue
                # Determine market
                market = ""
                if code.startswith("60"):
                    market = "沪A"
                elif code.startswith("00"):
                    market = "深A"
                elif code.startswith("30"):
                    market = "创业板"
                elif code.startswith("68"):
                    market = "科创板"
                else:
                    continue  # skip non-equity
                is_st = "ST" in name.upper() or "*ST" in name.upper()
                batch.append({
                    "code": code, "name": name, "market": market,
                    "is_st": is_st, "updated_at": now,
                })

            # Upsert
            for item in batch:
                existing = s.get(Stock, item["code"])
                if existing:
                    existing.name = item["name"]
                    existing.market = item["market"]
                    existing.is_st = item["is_st"]
                    existing.updated_at = item["updated_at"]
                else:
                    s.add(Stock(**item))
            s.commit()
            processed = len(batch)

        _finish_task(task_id, processed, total)
        logger.info("sync_stock_list: saved %d stocks", processed)
    except Exception as e:
        logger.error("sync_stock_list error: %s", e)
        _finish_task(task_id, 0, 0, str(e))
    return task_id


# ═══════════════════════════════════════════════════════════════
#  2. Real-time quote sync (Tencent batch)
# ═══════════════════════════════════════════════════════════════

def sync_quotes(_task_id: int = None) -> int:
    """Sync real-time quotes for all stocks in DB via Tencent."""
    task_id = _get_or_create_task("quotes", _task_id)
    if task_id == -1:
        logger.info("task already running, skipping")
        return -1
    try:
        with _get_session() as s:
            codes = [r[0] for r in s.execute(select(Stock.code)).fetchall()]
        if not codes:
            _finish_task(task_id, 0, 0, "no stocks in DB — run stock list sync first")
            return task_id

        total = len(codes)
        logger.info("sync_quotes: fetching %d stocks", total)

        # Batch fetch via tencent (100 per batch)
        all_quotes = tencent_provider.fetch_quotes(codes)
        processed = 0
        now = datetime.utcnow()

        with _get_session() as s:
            for q in all_quotes:
                code = q.get("code", "")
                if not code:
                    continue
                existing = s.get(QuoteCache, code)
                data = {
                    "code": code,
                    "name": q.get("name", ""),
                    "price": q.get("price", 0.0),
                    "change_pct": q.get("change_pct", 0.0),
                    "change_amt": q.get("change_amt", 0.0),
                    "volume": q.get("volume", 0.0),
                    "amount": q.get("amount", 0.0),
                    "open": q.get("open", 0.0),
                    "high": q.get("high", 0.0),
                    "low": q.get("low", 0.0),
                    "prev_close": q.get("prev_close", 0.0),
                    "turnover_rate": q.get("turnover_rate", 0.0),
                    "pe_ratio": q.get("pe_ratio", 0.0),
                    "market_cap": q.get("market_cap", 0.0),
                    "cached_at": now,
                }
                if existing:
                    for k, v in data.items():
                        setattr(existing, k, v)
                else:
                    s.add(QuoteCache(**data))
                processed += 1
                if processed % 500 == 0:
                    s.commit()
                    _update_task_progress(task_id, processed, total)
            s.commit()

        _finish_task(task_id, processed, total)
        logger.info("sync_quotes: saved %d quotes", processed)
    except Exception as e:
        logger.error("sync_quotes error: %s", e)
        _finish_task(task_id, 0, 0, str(e))
    return task_id


# ═══════════════════════════════════════════════════════════════
#  3. Financial snapshot sync
# ═══════════════════════════════════════════════════════════════

def _classify_fundamental(eps: float, roe: float, rev_yoy: float, np_yoy: float) -> tuple[str, str]:
    """Return (status, summary) based on financial metrics."""
    if roe > 15 and np_yoy > 10 and eps > 0:
        return "healthy", f"ROE {roe:.1f}% 净利润增 {np_yoy:.1f}% 盈利质量良好"
    if roe > 8 and np_yoy > 0:
        return "neutral", f"ROE {roe:.1f}% 盈利稳定"
    if roe < 0 or np_yoy < -20:
        return "risk", f"ROE {roe:.1f}% 净利润增 {np_yoy:.1f}% 业绩承压"
    if np_yoy < 0 or roe < 5:
        return "weak", f"ROE {roe:.1f}% 净利润增 {np_yoy:.1f}% 基本面偏弱"
    return "neutral", f"ROE {roe:.1f}% 基本面中性"


def sync_financials(_task_id: int = None) -> int:
    """Sync financial snapshots for all stocks via East Money batch API.

    Uses RPT_LICO_FN_CPD with ISNEW=1 to get the latest report per stock
    in bulk (500 per page), instead of calling akshare per-stock.
    ~11000 stocks in ~60 seconds.
    """
    import requests as _req

    task_id = _get_or_create_task("financials", _task_id)
    if task_id == -1:
        logger.info("task already running, skipping")
        return -1
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    page_size = 500
    columns = "SECURITY_CODE,BASIC_EPS,WEIGHTAVG_ROE,YSTZ,SJLTZ,TOTAL_OPERATE_INCOME,PARENT_NETPROFIT,REPORTDATE"

    try:
        page = 1
        processed = 0
        total = 0

        with _get_session() as s:
            while True:
                params = {
                    "reportName": "RPT_LICO_FN_CPD",
                    "columns": columns,
                    "pageSize": page_size,
                    "pageNumber": page,
                    "filter": "(ISNEW=1)",
                }
                resp = _req.get(url, params=params, headers=headers, timeout=15)
                data = resp.json()
                result = data.get("result")
                if not result or not result.get("data"):
                    if page == 1:
                        _finish_task(task_id, 0, 0, "no data from EM financial API")
                        return task_id
                    break

                if page == 1:
                    total = result.get("count", 0)
                    logger.info("sync_financials: %d stocks from EM batch API", total)

                now = datetime.utcnow()
                for item in result["data"]:
                    code = str(item.get("SECURITY_CODE", ""))
                    if not code:
                        continue
                    eps = float(item.get("BASIC_EPS") or 0)
                    roe = float(item.get("WEIGHTAVG_ROE") or 0)
                    rev_yoy = float(item.get("YSTZ") or 0)
                    np_yoy = float(item.get("SJLTZ") or 0)
                    revenue = float(item.get("TOTAL_OPERATE_INCOME") or 0)
                    net_profit = float(item.get("PARENT_NETPROFIT") or 0)
                    period = str(item.get("REPORTDATE") or "")[:10]

                    status, summary = _classify_fundamental(eps, roe, rev_yoy, np_yoy)

                    existing = s.get(FinancialSnapshot, code)
                    row_data = {
                        "code": code, "report_period": period,
                        "eps_ttm": eps, "roe": roe,
                        "revenue_yoy": rev_yoy, "net_profit_yoy": np_yoy,
                        "pe_ratio_ttm": 0.0, "total_revenue": revenue,
                        "net_profit": net_profit,
                        "fundamental_status": status,
                        "fundamental_summary": summary,
                        "updated_at": now,
                    }
                    if existing:
                        for k, v in row_data.items():
                            setattr(existing, k, v)
                    else:
                        s.add(FinancialSnapshot(**row_data))
                    processed += 1

                s.commit()
                _update_task_progress(task_id, processed, total)
                logger.info("sync_financials: %d/%d (page %d)", processed, total, page)
                page += 1

        _finish_task(task_id, processed, total)
        logger.info("sync_financials: done %d/%d", processed, total)
    except Exception as e:
        logger.error("sync_financials error: %s\n%s", e, traceback.format_exc())
        _finish_task(task_id, 0, 0, str(e))
    return task_id


# ═══════════════════════════════════════════════════════════════
#  4. Concept / theme sync
# ═══════════════════════════════════════════════════════════════

def sync_concepts(_task_id: int = None) -> int:
    """Sync stock concepts/themes from East Money datacenter API.

    Uses RPT_WEB_RESPREDICT which provides CONCEPTINDEX_BOARD per stock.
    Covers ~2700 stocks (those with analyst coverage).
    """
    import requests

    task_id = _get_or_create_task("concepts", _task_id)
    if task_id == -1:
        logger.info("task already running, skipping")
        return -1
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    page_size = 200
    page = 1
    processed = 0
    concept_count = 0

    try:
        with _get_session() as s:
            s.execute(delete(StockConcept))
            s.commit()
            now = datetime.utcnow()

            while True:
                params = {
                    "reportName": "RPT_WEB_RESPREDICT",
                    "columns": "SECURITY_CODE,CONCEPTINDEX_BOARD",
                    "pageSize": page_size,
                    "pageNumber": page,
                }
                resp = requests.get(url, params=params, headers=headers, timeout=15)
                data = resp.json()
                result = data.get("result")
                if not result or not result.get("data"):
                    if page == 1:
                        _finish_task(task_id, 0, 0, "no data from EM API")
                        return task_id
                    break

                total = result.get("count", 0)
                for item in result["data"]:
                    code = item.get("SECURITY_CODE", "")
                    concepts_str = item.get("CONCEPTINDEX_BOARD") or ""
                    if not code or not concepts_str:
                        continue
                    for concept in concepts_str.split(","):
                        concept = concept.strip().rstrip("_")
                        if not concept:
                            continue
                        s.add(StockConcept(code=code, concept=concept, updated_at=now))
                        concept_count += 1
                    processed += 1

                s.commit()
                _update_task_progress(task_id, processed, total)
                logger.info("sync_concepts: page %d, %d stocks, %d mappings", page, processed, concept_count)

                if page >= result.get("pages", 1):
                    break
                page += 1
                time.sleep(0.3)

        _finish_task(task_id, processed, concept_count)
        logger.info("sync_concepts: done %d stocks, %d mappings", processed, concept_count)
    except Exception as e:
        logger.error("sync_concepts error: %s", e)
        _finish_task(task_id, 0, 0, str(e))
    return task_id


# ═══════════════════════════════════════════════════════════════
#  5. Industry data sync (fill industry column in stocks table)
# ═══════════════════════════════════════════════════════════════

def sync_industry(_task_id: int = None) -> int:
    """Fill industry info for all stocks from East Money datacenter API.

    Uses RPT_WEB_RESPREDICT which provides INDUSTRY_BOARD per stock.
    Covers ~2700 stocks (those with analyst coverage).
    """
    import requests

    task_id = _get_or_create_task("industry", _task_id)
    if task_id == -1:
        logger.info("task already running, skipping")
        return -1
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    page_size = 200
    page = 1
    processed = 0
    stock_updated = 0

    try:
        with _get_session() as s:
            while True:
                params = {
                    "reportName": "RPT_WEB_RESPREDICT",
                    "columns": "SECURITY_CODE,INDUSTRY_BOARD",
                    "pageSize": page_size,
                    "pageNumber": page,
                }
                resp = requests.get(url, params=params, headers=headers, timeout=15)
                data = resp.json()
                result = data.get("result")
                if not result or not result.get("data"):
                    if page == 1:
                        _finish_task(task_id, 0, 0, "no data from EM API")
                        return task_id
                    break

                total = result.get("count", 0)
                for item in result["data"]:
                    code = item.get("SECURITY_CODE", "")
                    industry = item.get("INDUSTRY_BOARD") or ""
                    if not code or not industry:
                        continue
                    stock = s.get(Stock, code)
                    if stock:
                        stock.industry = industry
                        stock_updated += 1
                    processed += 1

                s.commit()
                _update_task_progress(task_id, processed, total)
                logger.info("sync_industry: page %d, %d/%d, updated %d", page, processed, total, stock_updated)

                if page >= result.get("pages", 1):
                    break
                page += 1
                time.sleep(0.3)

        _finish_task(task_id, stock_updated, processed)
        logger.info("sync_industry: done %d stocks updated out of %d", stock_updated, processed)
    except Exception as e:
        logger.error("sync_industry error: %s", e)
        _finish_task(task_id, 0, 0, str(e))
    return task_id


# ═══════════════════════════════════════════════════════════════
#  6. Historical daily candles batch sync
# ═══════════════════════════════════════════════════════════════

_EM_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_TX_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


def _em_secid(code: str) -> str:
    """Convert 6-digit code to EM secid (market.code)."""
    if code.startswith(("6", "5", "9")):
        return f"1.{code}"  # 上海
    return f"0.{code}"  # 深圳


def _tx_kline_symbol(code: str) -> str:
    """Convert 6-digit code to Tencent symbol."""
    if code.startswith(("6", "5", "9")):
        return f"sh{code}"
    return f"sz{code}"


def _fetch_candles_batch(session, code: str, beg: str, days: int) -> list[dict]:
    """Fetch daily candles — try Tencent first (fast + stable), fallback to EM."""
    # --- Tencent ---
    try:
        symbol = _tx_kline_symbol(code)
        r = session.get(
            _TX_KLINE_URL,
            params={"param": f"{symbol},day,{beg[:4]}-{beg[4:6]}-{beg[6:]},2050-12-31,{days*2},qfq"},
            timeout=8,
        )
        if r.status_code == 200:
            data = r.json().get("data", {})
            stock = data.get(symbol.lower(), data.get(symbol, {}))
            klines = stock.get("qfqday") or stock.get("day") or []
            if klines:
                rows = []
                for k in klines:
                    if len(k) < 6:
                        continue
                    rows.append({
                        "code": code,
                        "trade_date": k[0],
                        "open": float(k[1]),
                        "close": float(k[2]),
                        "high": float(k[3]),
                        "low": float(k[4]),
                        "volume": int(float(k[5])),
                    })
                return rows
    except Exception:
        pass

    # --- EM fallback ---
    try:
        params = {
            "secid": _em_secid(code),
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "101", "fqt": "1",
            "beg": beg, "end": "20501231",
        }
        r = session.get(_EM_KLINE_URL, params=params, timeout=8)
        if r.status_code == 200:
            data = r.json().get("data")
            if data and data.get("klines"):
                rows = []
                for line in data["klines"]:
                    parts = line.split(",")
                    if len(parts) < 6:
                        continue
                    rows.append({
                        "code": code,
                        "trade_date": parts[0],
                        "open": float(parts[1]),
                        "close": float(parts[2]),
                        "high": float(parts[3]),
                        "low": float(parts[4]),
                        "volume": int(float(parts[5])),
                    })
                return rows
    except Exception:
        pass

    return []


def sync_candles(days: int = 365, _task_id: int = None) -> int:
    """Batch-sync historical daily candles for all stocks using EM direct HTTP API.

    Incremental: only fetches data after each stock's latest cached date.
    Uses 4 workers with connection pooling + throttling to avoid rate limits.
    """
    import requests
    from requests.adapters import HTTPAdapter
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading

    task_id = _get_or_create_task("daily_candles", _task_id)
    if task_id == -1:
        logger.info("task already running, skipping")
        return -1
    try:
        with _get_session() as s:
            codes = [r[0] for r in s.execute(select(Stock.code)).fetchall()]
        if not codes:
            _finish_task(task_id, 0, 0, "no stocks in DB — run stock list sync first")
            return task_id

        # Query latest cached date per stock for incremental sync
        with _get_session() as s:
            from sqlalchemy import func as sa_func
            latest_rows = s.execute(
                select(DailyCandle.code, sa_func.max(DailyCandle.trade_date))
                .group_by(DailyCandle.code)
            ).fetchall()
        latest_map = {r[0]: r[1] for r in latest_rows}

        from datetime import date as _date
        cutoff = (_date.today() - timedelta(days=3)).strftime("%Y-%m-%d")
        default_beg = (_date.today() - timedelta(days=days * 2)).strftime("%Y%m%d")

        need_sync: list[tuple[str, str]] = []  # (code, beg_date)
        skip_count = 0
        for code in codes:
            last = latest_map.get(code)
            if last and last >= cutoff:
                skip_count += 1
            else:
                if last:
                    beg = (datetime.strptime(last, "%Y-%m-%d") - timedelta(days=3)).strftime("%Y%m%d")
                else:
                    beg = default_beg
                need_sync.append((code, beg))

        total = len(need_sync)
        logger.info(
            "sync_candles: %d stocks need sync, %d skipped (up-to-date), days=%d",
            total, skip_count, days,
        )
        if total == 0:
            _finish_task(task_id, skip_count, len(codes))
            return task_id

        # requests.Session with connection pool matching worker count
        _WORKERS = 4
        http = requests.Session()
        http.headers["User-Agent"] = "Mozilla/5.0"
        adapter = HTTPAdapter(pool_connections=_WORKERS, pool_maxsize=_WORKERS)
        http.mount("https://", adapter)

        # Throttle: use a lock to enforce minimum 50ms between requests
        _req_lock = threading.Lock()
        _last_req_time = [0.0]

        def _throttled_fetch(code, beg):
            with _req_lock:
                elapsed = time.time() - _last_req_time[0]
                if elapsed < 0.05:
                    time.sleep(0.05 - elapsed)
                _last_req_time[0] = time.time()
            return _fetch_candles_batch(http, code, beg, days)

        processed = 0
        errors = 0
        no_data = 0  # stocks that legitimately have no kline data

        # Build upsert SQL once
        upsert_sql = text(
            "INSERT INTO daily_candles (code, trade_date, open, high, low, close, volume) "
            "VALUES (:code, :trade_date, :open, :high, :low, :close, :volume) "
            "ON CONFLICT (code, trade_date) DO UPDATE SET "
            "open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, "
            "close=EXCLUDED.close, volume=EXCLUDED.volume"
        )

        all_rows: list[dict] = []

        with ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="kline") as executor:
            futures = {
                executor.submit(_throttled_fetch, code, beg): code
                for code, beg in need_sync
            }
            for i, future in enumerate(as_completed(futures)):
                code = futures[future]
                try:
                    rows = future.result(timeout=30)
                    if rows:
                        all_rows.extend(rows)
                        processed += 1
                    else:
                        no_data += 1
                except Exception as e:
                    errors += 1
                    if errors <= 20:
                        logger.warning("sync_candles error for %s: %s", code, e)

                # Flush to DB in batches
                if len(all_rows) >= 10000:
                    with _get_session() as s:
                        s.execute(upsert_sql, all_rows)
                        s.commit()
                    logger.info("sync_candles: flushed %d rows to DB", len(all_rows))
                    all_rows = []

                # Update progress periodically
                done = i + 1
                if done % 200 == 0 or done == total:
                    _update_task_progress(task_id, processed, total)
                    logger.info(
                        "sync_candles: %d/%d fetched (ok=%d, nodata=%d, err=%d)",
                        done, total, processed, no_data, errors,
                    )

        # Final flush
        if all_rows:
            with _get_session() as s:
                s.execute(upsert_sql, all_rows)
                s.commit()
            logger.info("sync_candles: flushed final %d rows to DB", len(all_rows))

        http.close()
        _finish_task(task_id, processed + skip_count, len(codes))
        logger.info("sync_candles: done %d/%d (nodata=%d, errors=%d, skipped=%d)",
                     processed, total, no_data, errors, skip_count)
    except Exception as e:
        logger.error("sync_candles error: %s", e, exc_info=True)
        _finish_task(task_id, 0, 0, str(e))
    return task_id


# ── Analyst consensus via East Money datacenter API ──

_EM_CONSENSUS_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"

def sync_analyst_consensus(_task_id: int = None) -> int:
    """Sync analyst consensus (target price / ratings) from East Money.

    Uses RPT_WEB_RESPREDICT for all stocks with analyst coverage (~2700).
    """
    import requests

    task_id = _get_or_create_task("analyst_consensus", _task_id)
    if task_id == -1:
        logger.info("task already running, skipping")
        return -1
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        page = 1
        page_size = 500
        processed = 0
        errors = 0
        total_count = 0

        with _get_session() as s:
            while True:
                params = {
                    "reportName": "RPT_WEB_RESPREDICT",
                    "columns": "ALL",
                    "pageSize": page_size,
                    "pageNumber": page,
                    "sortColumns": "RATING_ORG_NUM",
                    "sortTypes": -1,
                }
                try:
                    resp = requests.get(_EM_CONSENSUS_URL, params=params, headers=headers, timeout=15)
                    data = resp.json()
                except Exception as e:
                    logger.error("analyst_consensus HTTP error page %d: %s", page, e)
                    errors += 1
                    break

                result = data.get("result")
                if not result or not result.get("data"):
                    break

                if page == 1:
                    total_count = result.get("count", 0)
                    logger.info("sync_analyst_consensus: total %d stocks with coverage", total_count)

                for item in result["data"]:
                    try:
                        code = item.get("SECURITY_CODE", "")
                        if not code:
                            continue
                        now = datetime.utcnow()

                        existing = s.get(AnalystConsensus, code)
                        row_data = {
                            "code": code,
                            "name": item.get("SECURITY_NAME_ABBR", ""),
                            "target_price_high": _to_float(item.get("DEC_AIMPRICEMAX")),
                            "target_price_low": _to_float(item.get("DEC_AIMPRICEMIN")),
                            "analyst_count": _to_int(item.get("RATING_ORG_NUM")),
                            "buy_count": _to_int(item.get("RATING_BUY_NUM")),
                            "overweight_count": _to_int(item.get("RATING_ADD_NUM")),
                            "neutral_count": _to_int(item.get("RATING_NEUTRAL_NUM")),
                            "underweight_count": _to_int(item.get("RATING_REDUCE_NUM")),
                            "sell_count": _to_int(item.get("RATING_SALE_NUM")),
                            "eps_current_year": _to_float(item.get("EPS1")),
                            "eps_next_year": _to_float(item.get("EPS2")),
                            "updated_at": now,
                        }
                        if existing:
                            for k, v in row_data.items():
                                setattr(existing, k, v)
                        else:
                            s.add(AnalystConsensus(**row_data))
                        processed += 1
                    except Exception as e:
                        errors += 1
                        logger.debug("analyst_consensus item error: %s", e)

                s.commit()
                _update_task_progress(task_id, processed, total_count)
                logger.info("sync_analyst_consensus: page %d done (%d/%d)", page, processed, total_count)

                # Check if we've fetched all pages
                total_pages = result.get("pages", 1)
                if page >= total_pages:
                    break
                page += 1

        _finish_task(task_id, processed, total_count)
        logger.info("sync_analyst_consensus: done %d/%d (errors: %d)", processed, total_count, errors)
    except Exception as e:
        logger.error("sync_analyst_consensus error: %s", e)
        _finish_task(task_id, 0, 0, str(e))
    return task_id


def _to_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _to_int(v) -> int:
    if v is None:
        return 0
    try:
        return int(v)
    except (ValueError, TypeError):
        return 0


# ═══════════════════════════════════════════════════════════════
#  Query helpers (used by API endpoints)
# ═══════════════════════════════════════════════════════════════

def get_stock_info(code: str) -> Optional[dict]:
    """Get enriched stock info from DB."""
    with _get_session() as s:
        stock = s.get(Stock, code)
        if not stock:
            return None
        return {
            "code": stock.code,
            "name": stock.name,
            "industry": stock.industry,
            "market": stock.market,
            "is_st": stock.is_st,
        }


def get_quote_cache(code: str) -> Optional[dict]:
    """Get cached quote data."""
    with _get_session() as s:
        q = s.get(QuoteCache, code)
        if not q:
            return None
        return {
            "open": q.open, "high": q.high, "low": q.low,
            "prev_close": q.prev_close,
            "turnover_rate": q.turnover_rate,
            "pe_ratio": q.pe_ratio,
            "market_cap": q.market_cap,
        }


def get_financial_snapshot(code: str) -> Optional[dict]:
    """Get fundamental data."""
    with _get_session() as s:
        f = s.get(FinancialSnapshot, code)
        if not f:
            return None
        return {
            "report_period": f.report_period,
            "eps_ttm": f.eps_ttm,
            "roe": f.roe,
            "revenue_yoy": f.revenue_yoy,
            "net_profit_yoy": f.net_profit_yoy,
            "pe_ratio_ttm": f.pe_ratio_ttm,
            "total_revenue": f.total_revenue,
            "net_profit": f.net_profit,
            "fundamental_status": f.fundamental_status,
            "fundamental_summary": f.fundamental_summary,
        }


def get_stock_concepts(code: str) -> list[str]:
    """Get concept/theme tags for a stock."""
    with _get_session() as s:
        rows = s.execute(
            select(StockConcept.concept).where(StockConcept.code == code)
        ).fetchall()
        return [r[0] for r in rows]


def get_analyst_consensus(code: str) -> Optional[dict]:
    """Get analyst consensus data (target price, rating, etc.)."""
    with _get_session() as s:
        ac = s.get(AnalystConsensus, code)
        if not ac:
            return None
        hi = ac.target_price_high
        lo = ac.target_price_low
        avg = round((hi + lo) / 2, 2) if hi is not None and lo is not None else None
        return {
            "consensus_target": avg,
            "target_high": hi,
            "target_low": lo,
            "analyst_count": ac.analyst_count,
            "buy_count": ac.buy_count,
            "overweight_count": ac.overweight_count,
            "neutral_count": ac.neutral_count,
            "underweight_count": ac.underweight_count,
            "sell_count": ac.sell_count,
            "eps_current_year": ac.eps_current_year,
            "eps_next_year": ac.eps_next_year,
        }


def get_sync_tasks() -> list[dict]:
    """Get all sync tasks for display."""
    with _get_session() as s:
        tasks = s.execute(
            select(SyncTask).order_by(SyncTask.id.desc()).limit(20)
        ).scalars().all()
        return [
            {
                "id": t.id,
                "task_type": t.task_type,
                "status": t.status,
                "total": t.total,
                "processed": t.processed,
                "error_msg": t.error_msg,
                "started_at": t.started_at.isoformat() if t.started_at else None,
                "finished_at": t.finished_at.isoformat() if t.finished_at else None,
            }
            for t in tasks
        ]


def get_db_stats() -> dict:
    """Get row counts for all tables."""
    with _get_session() as s:
        candle_min = s.execute(text("SELECT MIN(trade_date) FROM daily_candles")).scalar() or ""
        candle_max = s.execute(text("SELECT MAX(trade_date) FROM daily_candles")).scalar() or ""
        candle_codes = s.execute(text("SELECT COUNT(DISTINCT code) FROM daily_candles")).scalar() or 0
        return {
            "stocks": s.execute(text("SELECT COUNT(*) FROM stocks")).scalar() or 0,
            "daily_candles": s.execute(text("SELECT COUNT(*) FROM daily_candles")).scalar() or 0,
            "candle_codes": candle_codes,
            "candle_min_date": candle_min,
            "candle_max_date": candle_max,
            "quote_cache": s.execute(text("SELECT COUNT(*) FROM quote_cache")).scalar() or 0,
            "financial_snapshots": s.execute(text("SELECT COUNT(*) FROM financial_snapshots")).scalar() or 0,
            "stock_concepts": s.execute(text("SELECT COUNT(*) FROM stock_concepts")).scalar() or 0,
            "analyst_consensus": s.execute(text("SELECT COUNT(*) FROM analyst_consensus")).scalar() or 0,
            "sync_tasks": s.execute(text("SELECT COUNT(*) FROM sync_tasks")).scalar() or 0,
        }


# ── Helpers ──

def _safe_float(row, col: str, default: float = 0.0) -> float:
    try:
        v = row.get(col)
        if v is None:
            return default
        return float(v)
    except (ValueError, TypeError):
        return default


# ═══════════════════════════════════════════════════════════════
#  Screener (runs in subprocess, saves results to JSON cache)
# ═══════════════════════════════════════════════════════════════

def _candles_are_stale() -> bool:
    """Check if daily candles are stale (latest date is not today).
    Only considers trading hours — before 9:30 uses yesterday's date."""
    from datetime import date as _date
    from sqlalchemy import func as sa_func
    now = datetime.now()
    # Before 9:30 AM, don't consider candles stale (market hasn't opened)
    if now.hour < 9 or (now.hour == 9 and now.minute < 30):
        return False
    today = _date.today().strftime("%Y-%m-%d")
    try:
        with _get_session() as s:
            latest = s.execute(
                select(sa_func.max(DailyCandle.trade_date))
            ).scalar()
        if not latest:
            return True
        return latest < today
    except Exception:
        return False


def _is_market_hours() -> bool:
    """Return True if current time is during A-share trading hours (9:30-15:00 weekdays)."""
    now = datetime.now()
    if now.weekday() >= 5:  # Saturday/Sunday
        return False
    t = now.hour * 100 + now.minute
    return 930 <= t <= 1500


def run_screener(_task_id: int = None):
    """Run all pattern detectors and save results to .screener_cache/*.json.

    Automatically syncs daily candles first if data is stale.
    During market hours, also syncs quotes and appends live candles.
    """
    import json as _json
    import os as _os
    from .data_provider import get_candles, list_universe
    from ..schemas import Candle
    from .screener import PATTERN_DETECTORS

    task_id = _get_or_create_task("screener", _task_id)
    if task_id == -1:
        logger.info("task already running, skipping")
        return -1

    # Auto-sync candles if stale
    if _candles_are_stale():
        logger.info("screener: candles are stale, syncing first...")
        sync_candles(days=365)
        logger.info("screener: candle sync done")

    # During market hours, sync quotes and build live candle map
    intraday = _is_market_hours()
    quote_map: dict[str, dict] = {}
    if intraday:
        logger.info("screener: market hours — syncing quotes for live candles...")
        sync_quotes()
        # Batch load all quotes into memory
        with _get_session() as s:
            rows = s.execute(select(QuoteCache)).scalars().all()
            for q in rows:
                if q.price and q.price > 0 and q.open and q.open > 0:
                    quote_map[q.code] = {
                        "open": q.open, "high": q.high, "low": q.low,
                        "close": q.price, "volume": q.volume or 0,
                    }
        logger.info("screener: loaded %d live quotes, starting scan", len(quote_map))
    else:
        logger.info("screener: after hours, using cached candles")

    today_str = datetime.now().strftime("%Y-%m-%d")

    cache_dir = _os.path.join(
        _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))),
        ".screener_cache",
    )
    _os.makedirs(cache_dir, exist_ok=True)

    universe = list_universe()
    logger.info("screener: scanning %d stocks", len(universe))

    def _get_candles_with_live(code: str) -> list[Candle]:
        candles = get_candles(code, days=180)
        if not candles:
            return []
        # Append/update today's live candle from quote_map
        live = quote_map.get(code)
        if live:
            last_date = candles[-1].date[:10] if candles else ""
            if last_date != today_str:
                candles.append(Candle(
                    date=today_str,
                    open=live["open"],
                    high=live["high"] if live["high"] > 0 else live["close"],
                    low=live["low"] if live["low"] > 0 else live["close"],
                    close=live["close"],
                    volume=live["volume"],
                ))
            elif last_date == today_str:
                candles[-1] = Candle(
                    date=today_str,
                    open=live["open"],
                    high=max(candles[-1].high, live["high"]) if live["high"] > 0 else candles[-1].high,
                    low=min(candles[-1].low, live["low"]) if live["low"] > 0 else candles[-1].low,
                    close=live["close"],
                    volume=live["volume"] if live["volume"] > 0 else candles[-1].volume,
                )
        return candles

    for pattern, detector in PATTERN_DETECTORS.items():
        items = []
        for code, name, _ind in universe:
            try:
                candles = _get_candles_with_live(code)
                if not candles:
                    continue
                r = detector(code, name, candles)
                if r:
                    items.append(r)
            except Exception:
                continue
        items.sort(key=lambda x: x.score, reverse=True)
        result = {
            "pattern": pattern,
            "total": len(items),
            "scanned": len(universe),
            "scanned_at": datetime.now().isoformat(),
            "items": [
                {
                    "code": it.code, "name": it.name, "pattern": it.pattern,
                    "score": it.score, "price": it.price,
                    "change_pct": it.change_pct, "volume_ratio": it.volume_ratio,
                    "breakout_price": it.breakout_price,
                    "pullback_price": it.pullback_price,
                    "distance_to_support_pct": it.distance_to_support_pct,
                    "triggers": it.triggers,
                }
                for it in items
            ],
        }
        path = _os.path.join(cache_dir, f"{pattern}.json")
        with open(path, "w") as f:
            _json.dump(result, f, ensure_ascii=False)
        logger.info("screener: %s → %d items", pattern, len(items))

    _finish_task(task_id, len(universe), len(universe))
