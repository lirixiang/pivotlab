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
from datetime import datetime
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

def _create_task(task_type: str) -> int:
    with _get_session() as s:
        t = SyncTask(
            task_type=task_type,
            status="running",
            started_at=datetime.utcnow(),
        )
        s.add(t)
        s.commit()
        return t.id


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

def sync_stock_list() -> int:
    """Sync A-share stock list from akshare."""
    task_id = _create_task("stocks")
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

def sync_quotes() -> int:
    """Sync real-time quotes for all stocks in DB via Tencent."""
    task_id = _create_task("quotes")
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


def sync_financials() -> int:
    """Sync financial snapshots for all stocks via akshare."""
    task_id = _create_task("financials")
    if not _HAS_AK:
        _finish_task(task_id, 0, 0, "akshare unavailable")
        return task_id
    try:
        with _get_session() as s:
            codes = [r[0] for r in s.execute(select(Stock.code)).fetchall()]
        if not codes:
            _finish_task(task_id, 0, 0, "no stocks in DB")
            return task_id

        total = len(codes)
        logger.info("sync_financials: processing %d stocks", total)
        processed = 0
        errors = 0

        with _get_session() as s:
            for i, code in enumerate(codes):
                try:
                    # akshare individual stock financial abstract
                    symbol = f"{code}"
                    df = ak.stock_financial_abstract(symbol=symbol)
                    if df is None or df.empty:
                        continue

                    # Use the latest row
                    row = df.iloc[0]
                    eps = _safe_float(row, "摊薄每股收益")
                    roe = _safe_float(row, "净资产收益率")
                    rev_yoy = _safe_float(row, "营业总收入同比增长率")
                    np_yoy = _safe_float(row, "归属净利润同比增长率")
                    pe = _safe_float(row, "市盈率")
                    revenue = _safe_float(row, "营业总收入")
                    net_profit = _safe_float(row, "归属净利润")
                    period = str(row.get("报告期", ""))

                    status, summary = _classify_fundamental(eps, roe, rev_yoy, np_yoy)
                    now = datetime.utcnow()

                    existing = s.get(FinancialSnapshot, code)
                    data = {
                        "code": code, "report_period": period,
                        "eps_ttm": eps, "roe": roe,
                        "revenue_yoy": rev_yoy, "net_profit_yoy": np_yoy,
                        "pe_ratio_ttm": pe, "total_revenue": revenue,
                        "net_profit": net_profit,
                        "fundamental_status": status,
                        "fundamental_summary": summary,
                        "updated_at": now,
                    }
                    if existing:
                        for k, v in data.items():
                            setattr(existing, k, v)
                    else:
                        s.add(FinancialSnapshot(**data))
                    processed += 1

                    if processed % 50 == 0:
                        s.commit()
                        _update_task_progress(task_id, processed, total)
                        logger.info("sync_financials: %d/%d", processed, total)

                except Exception as e:
                    errors += 1
                    if errors <= 5:
                        logger.warning("sync_financials error for %s: %s", code, e)
                    continue

                # Throttle to avoid rate limiting
                if (i + 1) % 20 == 0:
                    time.sleep(0.5)

            s.commit()

        _finish_task(task_id, processed, total)
        logger.info("sync_financials: done %d/%d (errors: %d)", processed, total, errors)
    except Exception as e:
        logger.error("sync_financials error: %s", e)
        _finish_task(task_id, 0, 0, str(e))
    return task_id


# ═══════════════════════════════════════════════════════════════
#  4. Concept / theme sync
# ═══════════════════════════════════════════════════════════════

def sync_concepts() -> int:
    """Sync stock concepts/themes from akshare."""
    task_id = _create_task("concepts")
    if not _HAS_AK:
        _finish_task(task_id, 0, 0, "akshare unavailable")
        return task_id
    try:
        # Get all concept board names
        boards = ak.stock_board_concept_name_em()
        if boards is None or boards.empty:
            _finish_task(task_id, 0, 0, "no concept boards returned")
            return task_id

        total = len(boards)
        logger.info("sync_concepts: processing %d concept boards", total)
        processed = 0
        concept_count = 0

        with _get_session() as s:
            # Clear old concepts
            s.execute(delete(StockConcept))
            s.commit()

            now = datetime.utcnow()
            for i, (_, board_row) in enumerate(boards.iterrows()):
                concept_name = str(board_row.get("板块名称", "")).strip()
                if not concept_name:
                    continue
                try:
                    members = ak.stock_board_concept_cons_em(symbol=concept_name)
                    if members is None or members.empty:
                        continue
                    for _, m in members.iterrows():
                        code = str(m.get("代码", "")).strip()
                        if not code or len(code) != 6:
                            continue
                        s.add(StockConcept(
                            code=code, concept=concept_name, updated_at=now,
                        ))
                        concept_count += 1
                    processed += 1

                    if processed % 10 == 0:
                        s.commit()
                        _update_task_progress(task_id, processed, total)
                        logger.info("sync_concepts: %d/%d boards, %d mappings", processed, total, concept_count)

                except Exception as e:
                    logger.warning("sync_concepts error for '%s': %s", concept_name, e)
                    continue

                # Throttle
                if (i + 1) % 5 == 0:
                    time.sleep(1.0)

            s.commit()

        _finish_task(task_id, processed, total)
        logger.info("sync_concepts: done %d boards, %d mappings", processed, concept_count)
    except Exception as e:
        logger.error("sync_concepts error: %s", e)
        _finish_task(task_id, 0, 0, str(e))
    return task_id


# ═══════════════════════════════════════════════════════════════
#  5. Industry data sync (fill industry column in stocks table)
# ═══════════════════════════════════════════════════════════════

def sync_industry() -> int:
    """Fill industry info for all stocks from akshare."""
    task_id = _create_task("industry")
    if not _HAS_AK:
        _finish_task(task_id, 0, 0, "akshare unavailable")
        return task_id
    try:
        df = ak.stock_board_industry_cons_em(symbol="全部")
        if df is None or df.empty:
            # Try alternative: iterate over industry boards
            _finish_task(task_id, 0, 0, "no industry data returned")
            return task_id
    except Exception:
        # Alternative approach: get industry list then members
        try:
            return _sync_industry_via_boards(task_id)
        except Exception as e2:
            _finish_task(task_id, 0, 0, str(e2))
            return task_id

    # Direct approach with full table
    total = len(df)
    processed = 0
    with _get_session() as s:
        for _, row in df.iterrows():
            code = str(row.get("代码", "")).strip()
            industry = str(row.get("板块名称", "")).strip()
            if not code or not industry:
                continue
            stock = s.get(Stock, code)
            if stock:
                stock.industry = industry
                processed += 1
        s.commit()

    _finish_task(task_id, processed, total)
    logger.info("sync_industry: updated %d stocks", processed)
    return task_id


def _sync_industry_via_boards(task_id: int) -> int:
    """Fallback: iterate industry boards to fill stock industry."""
    boards = ak.stock_board_industry_name_em()
    if boards is None or boards.empty:
        _finish_task(task_id, 0, 0, "no industry boards")
        return task_id

    total = len(boards)
    processed = 0
    stock_updated = 0

    with _get_session() as s:
        for i, (_, row) in enumerate(boards.iterrows()):
            name = str(row.get("板块名称", "")).strip()
            if not name:
                continue
            try:
                members = ak.stock_board_industry_cons_em(symbol=name)
                if members is None or members.empty:
                    continue
                for _, m in members.iterrows():
                    code = str(m.get("代码", "")).strip()
                    stock = s.get(Stock, code)
                    if stock:
                        stock.industry = name
                        stock_updated += 1
                processed += 1
            except Exception:
                continue

            if (i + 1) % 5 == 0:
                s.commit()
                _update_task_progress(task_id, processed, total)
                time.sleep(0.5)

        s.commit()

    _finish_task(task_id, processed, total)
    logger.info("sync_industry: %d boards, %d stocks updated", processed, stock_updated)
    return task_id


# ═══════════════════════════════════════════════════════════════
#  6. Historical daily candles batch sync
# ═══════════════════════════════════════════════════════════════

def sync_candles(days: int = 365) -> int:
    """Batch-sync historical daily candles for all stocks in DB.

    days controls how far back to fetch (default 1 year).
    Uses akshare Tencent / East Money sources with throttling.
    """
    task_id = _create_task("daily_candles")
    if not _HAS_AK:
        _finish_task(task_id, 0, 0, "akshare unavailable")
        return task_id
    try:
        with _get_session() as s:
            codes = [r[0] for r in s.execute(select(Stock.code)).fetchall()]
        if not codes:
            _finish_task(task_id, 0, 0, "no stocks in DB — run stock list sync first")
            return task_id

        total = len(codes)
        logger.info("sync_candles: fetching %d stocks, days=%d", total, days)
        processed = 0
        errors = 0

        from .data_provider import _fetch_candles_sync, _cache_save
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

        # Use a separate thread with timeout per stock to prevent hanging
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="candle-fetch") as executor:
            for i, code in enumerate(codes):
                try:
                    future = executor.submit(_fetch_candles_sync, code, days)
                    candles = future.result(timeout=15)  # 15s timeout per stock
                    if candles:
                        _cache_save(code, candles)
                        processed += 1
                    else:
                        errors += 1
                except (FuturesTimeout, TimeoutError):
                    errors += 1
                    if errors <= 10:
                        logger.warning("sync_candles timeout for %s", code)
                except Exception as e:
                    errors += 1
                    if errors <= 10:
                        logger.warning("sync_candles error for %s: %s", code, e)

                if (i + 1) % 20 == 0:
                    _update_task_progress(task_id, processed, total)
                    logger.info("sync_candles: %d/%d (errors: %d)", processed, total, errors)

                # Throttle to avoid rate limiting
                if (i + 1) % 10 == 0:
                    time.sleep(0.3)

        _finish_task(task_id, processed, total)
        logger.info("sync_candles: done %d/%d (errors: %d)", processed, total, errors)
    except Exception as e:
        logger.error("sync_candles error: %s", e)
        _finish_task(task_id, 0, 0, str(e))
    return task_id


# ── Analyst consensus via East Money datacenter API ──

_EM_CONSENSUS_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"

def sync_analyst_consensus() -> int:
    """Sync analyst consensus (target price / ratings) from East Money.

    Uses RPT_WEB_RESPREDICT for all stocks with analyst coverage (~2700).
    """
    import requests

    task_id = _create_task("analyst_consensus")
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        page = 1
        page_size = 200
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
                logger.info("sync_analyst_consensus: page %d done (%d so far)", page, processed)

                # Check if we've fetched all pages
                total_pages = result.get("pages", 1)
                if page >= total_pages:
                    break
                page += 1
                time.sleep(0.3)  # rate limit

        _finish_task(task_id, processed, errors)
        logger.info("sync_analyst_consensus: done %d stocks (errors: %d)", processed, errors)
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
