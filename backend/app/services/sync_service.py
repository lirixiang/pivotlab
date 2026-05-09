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

from sqlalchemy import select, delete, text, func, Numeric
from sqlalchemy.orm import Session

from ..models import (
    Stock, FinancialSnapshot, StockConcept, SyncTask, DailyCandle,
    AnalystConsensus, FinancialHistory, ConceptBoard,
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
#  Concept heat / strength scoring (ported from SR platform)
# ═══════════════════════════════════════════════════════════════

_CONCEPT_STRENGTH_MAX_BOARD_CHANGE = 5.0
_CONCEPT_STRENGTH_MAX_AVG_CHANGE = 3.0
_CONCEPT_STRENGTH_MAX_LEADER_CHANGE = 10.0
_CONCEPT_STRENGTH_MAX_TURNOVER_AMOUNT = 15_000_000_000
_CONCEPT_STRENGTH_MAX_INFLOW_RATIO = 0.08

_CONCEPT_HEAT_LABELS = {
    "core": "强主线",
    "hot": "热点",
    "watch": "可跟踪",
    "observe": "观察",
}
_CONCEPT_HEAT_TONES = {
    "core": "concept-hot",
    "hot": "concept-hot",
    "watch": "concept-neutral",
    "observe": "concept-watch",
}

# Broad/index concepts to exclude from display
_CONCEPT_BLACKLIST = {
    "深股通", "沪股通", "融资融券", "富时罗素", "标准普尔", "MSCI中国",
    "深成500", "沪深300", "上证50", "上证180", "中证500", "中证1000",
    "国企改革", "西部大开发", "一带一路", "京津冀一体化",
    "创投", "高送转", "次新股", "举牌", "股权转让",
    "转融券标的", "含可转债", "标普道琼斯",
}
_CONCEPT_BLACKLIST_PATTERNS = ("年报预增", "年报预减", "年报扭亏", "年报续亏",
                                "季报预增", "季报预减", "季报扭亏", "中报预增")


def _normalize_positive(value: float | int | None, ceiling: float) -> float:
    if ceiling <= 0:
        return 0.0
    return max(0.0, min(float(value or 0), ceiling)) / ceiling


def _calculate_concept_strength(item: dict) -> float:
    """Composite strength score 0-100 for a concept board."""
    quoted = int(item.get("quoted") or 0)
    up_count = int(item.get("up_count") or 0)
    up_ratio = (up_count / quoted) if quoted > 0 else 0.0
    turnover_amount = float(item.get("turnover_amount") or 0)
    net_inflow = float(item.get("net_inflow") or 0)
    inflow_ratio = (net_inflow / turnover_amount) if turnover_amount > 0 and net_inflow > 0 else 0.0
    leader_changes = [
        float(ld["change_pct"])
        for ld in item.get("leaders") or []
        if ld.get("change_pct") is not None
    ]
    strongest_leader = max(leader_changes) if leader_changes else 0.0

    board_score = _normalize_positive(item.get("change_pct_1d"), _CONCEPT_STRENGTH_MAX_BOARD_CHANGE) * 25
    breadth_score = max(0.0, min(up_ratio, 1.0)) * 25
    avg_score = _normalize_positive(item.get("avg_change_pct"), _CONCEPT_STRENGTH_MAX_AVG_CHANGE) * 15
    leader_score = _normalize_positive(strongest_leader, _CONCEPT_STRENGTH_MAX_LEADER_CHANGE) * 15
    capital_score = (
        _normalize_positive(turnover_amount, _CONCEPT_STRENGTH_MAX_TURNOVER_AMOUNT) * 8
        + _normalize_positive(inflow_ratio, _CONCEPT_STRENGTH_MAX_INFLOW_RATIO) * 12
    )
    return round(board_score + breadth_score + avg_score + leader_score + capital_score, 1)


def build_concept_heat_fields(rank: int | None, strength_score: float | None = None) -> dict:
    """Determine heat_level / heat_label / heat_tone from rank + strength."""
    if rank is None:
        if strength_score is None:
            level = "observe"
        elif strength_score >= 90:
            level = "core"
        elif strength_score >= 75:
            level = "hot"
        elif strength_score >= 58:
            level = "watch"
        else:
            level = "observe"
    elif rank <= 10:
        level = "core"
    elif rank <= 20:
        level = "hot"
    elif rank <= 30:
        level = "hot" if (strength_score is not None and strength_score >= 55) else "watch"
    elif rank <= 50:
        level = "watch"
    else:
        level = "observe"

    # Adjust by strength
    if strength_score is not None:
        if strength_score >= 85:
            if level in {"core", "hot"}:
                level = "core"
            elif level == "watch":
                level = "hot"
        elif strength_score >= 72:
            if level == "watch":
                level = "hot"
        elif strength_score < 40:
            if level == "core":
                level = "hot"
            elif level == "hot":
                level = "watch"
            elif level == "watch":
                level = "observe"
        elif strength_score < 50 and level == "hot" and rank is not None and rank > 20:
            level = "watch"

    return {
        "heat_level": level,
        "heat_label": _CONCEPT_HEAT_LABELS[level],
        "heat_tone": _CONCEPT_HEAT_TONES[level],
        "is_hot_theme": level in {"core", "hot", "watch"},
    }


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
# ═══════════════════════════════════════════════════════════════
#  1. Stock list sync
# ═══════════════════════════════════════════════════════════════

def _source_for(task_type: str) -> str:
    """Get the configured data source id for a task type."""
    from .source_registry import get_source_for
    return get_source_for(task_type)


def sync_stock_list(_task_id: int = None) -> int:
    """Sync A-share stock list."""
    source = _source_for("stocks")
    if source == "em_api":
        return _sync_stock_list_em(_task_id)
    return _sync_stock_list_akshare(_task_id)


def _sync_stock_list_akshare(_task_id: int = None) -> int:
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
        logger.info("sync_stock_list[akshare]: fetched %d stocks", total)

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
        logger.info("sync_stock_list[akshare]: saved %d stocks", processed)
    except Exception as e:
        logger.error("sync_stock_list[akshare] error: %s", e)
        _finish_task(task_id, 0, 0, str(e))
    return task_id


def _sync_stock_list_em(_task_id: int = None) -> int:
    """Sync A-share stock list from East Money datacenter API."""
    import requests as _req

    task_id = _get_or_create_task("stocks", _task_id)
    if task_id == -1:
        logger.info("task already running, skipping")
        return -1
    try:
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        page_size = 500
        page = 1
        processed = 0
        total = 0

        with _get_session() as s:
            now = datetime.utcnow()
            while True:
                params = {
                    "reportName": "RPT_LICO_FN_CPD",
                    "columns": "SECURITY_CODE,SECURITY_NAME_ABBR",
                    "pageSize": page_size,
                    "pageNumber": page,
                    "filter": '(ISNEW="1")',
                }
                resp = _req.get(url, params=params, headers=headers, timeout=15)
                data = resp.json()
                result = data.get("result")
                if not result or not result.get("data"):
                    if page == 1:
                        _finish_task(task_id, 0, 0, "no data from EM API")
                        return task_id
                    break

                if page == 1:
                    total = result.get("count", 0)

                for item in result["data"]:
                    code = item.get("SECURITY_CODE", "").strip()
                    name = item.get("SECURITY_NAME_ABBR", "").strip()
                    if not code or len(code) != 6:
                        continue
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
                        continue
                    is_st = "ST" in name.upper()

                    existing = s.get(Stock, code)
                    if existing:
                        existing.name = name
                        existing.market = market
                        existing.is_st = is_st
                        existing.updated_at = now
                    else:
                        s.add(Stock(code=code, name=name, market=market, is_st=is_st, updated_at=now))
                    processed += 1

                s.commit()
                _update_task_progress(task_id, processed, total)
                logger.info("sync_stock_list[em]: page %d done (%d/%d)", page, processed, total)

                total_pages = result.get("pages", 1)
                if page >= total_pages:
                    break
                page += 1

        _finish_task(task_id, processed, total)
        logger.info("sync_stock_list[em]: saved %d stocks", processed)
    except Exception as e:
        logger.error("sync_stock_list[em] error: %s", e)
        _finish_task(task_id, 0, 0, str(e))
    return task_id


# ═══════════════════════════════════════════════════════════════
#  2. Real-time quote sync (Tencent batch)
# ═══════════════════════════════════════════════════════════════

def sync_quotes(_task_id: int = None) -> int:
    """Sync real-time quotes for all stocks via EM clist batch API.

    Fetches all A-share quotes in ~17s (56 pages × 100/page).
    Replaces old per-stock Tencent approach.
    """
    import requests

    task_id = _get_or_create_task("quotes", _task_id)
    if task_id == -1:
        logger.info("task already running, skipping")
        return -1
    try:
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        session = requests.Session()
        session.headers["User-Agent"] = "Mozilla/5.0"

        # f2=close, f3=chg%, f4=chg, f5=vol, f6=amt, f8=turnover, f9=PE
        # f12=code, f14=name, f15=high, f16=low, f17=open, f18=prevClose, f20=mktcap
        fields = "f2,f3,f4,f5,f6,f8,f9,f12,f14,f15,f16,f17,f18,f20"

        all_quotes = []
        page = 1
        total = 0

        while True:
            params = {
                "pn": page, "pz": 100, "po": 1, "np": 1,
                "fltt": 2, "invt": 2, "fid": "f12",
                "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
                "fields": fields,
            }
            try:
                resp = session.get(url, params=params, timeout=10)
                data = resp.json().get("data", {})
                items = data.get("diff", [])
            except Exception:
                break

            if not items:
                break
            if page == 1:
                total = data.get("total", 0)
                logger.info("sync_quotes: %d stocks via EM clist", total)

            for item in items:
                code = item.get("f12")
                close = item.get("f2")
                if not code or close is None or close == "-":
                    continue
                try:
                    all_quotes.append({
                        "code": code,
                        "name": str(item.get("f14") or ""),
                        "price": float(close),
                        "change_pct": float(item.get("f3") or 0),
                        "change_amt": float(item.get("f4") or 0),
                        "volume": float(item.get("f5") or 0),
                        "amount": float(item.get("f6") or 0),
                        "open": float(item.get("f17") or 0) if item.get("f17") != "-" else 0.0,
                        "high": float(item.get("f15") or 0) if item.get("f15") != "-" else 0.0,
                        "low": float(item.get("f16") or 0) if item.get("f16") != "-" else 0.0,
                        "prev_close": float(item.get("f18") or 0) if item.get("f18") != "-" else 0.0,
                        "turnover_rate": float(item.get("f8") or 0) if item.get("f8") != "-" else 0.0,
                        "pe_ratio": float(item.get("f9") or 0) if item.get("f9") != "-" else 0.0,
                        "market_cap": float(item.get("f20") or 0) if item.get("f20") != "-" else 0.0,
                    })
                except (ValueError, TypeError):
                    continue

            page += 1
            if page > 200:
                break

        session.close()

        if not all_quotes:
            _finish_task(task_id, 0, 0, "no quotes from EM clist API")
            return task_id

        # Bulk upsert into daily_candles (today's row)
        from datetime import date as _date
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        today = _date.today().strftime("%Y-%m-%d")
        now = datetime.utcnow()
        processed = 0

        with _get_session() as s:
            batch = []
            for q in all_quotes:
                batch.append({
                    "code": q["code"],
                    "trade_date": today,
                    "open": q["open"],
                    "high": q["high"],
                    "low": q["low"],
                    "close": q["price"],
                    "volume": q["volume"],
                    "amount": q["amount"],
                    "change_pct": q["change_pct"],
                    "change_amt": q["change_amt"],
                    "prev_close": q["prev_close"],
                    "turnover_rate": q["turnover_rate"],
                    "pe_ratio": q["pe_ratio"],
                    "market_cap": q["market_cap"],
                    "updated_at": now,
                })
                if len(batch) >= 1000:
                    stmt = pg_insert(DailyCandle).values(batch)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["code", "trade_date"],
                        set_={
                            "open": stmt.excluded.open,
                            "high": stmt.excluded.high,
                            "low": stmt.excluded.low,
                            "close": stmt.excluded.close,
                            "volume": stmt.excluded.volume,
                            "amount": stmt.excluded.amount,
                            "change_pct": stmt.excluded.change_pct,
                            "change_amt": stmt.excluded.change_amt,
                            "prev_close": stmt.excluded.prev_close,
                            "turnover_rate": stmt.excluded.turnover_rate,
                            "pe_ratio": stmt.excluded.pe_ratio,
                            "market_cap": stmt.excluded.market_cap,
                            "updated_at": stmt.excluded.updated_at,
                        },
                    )
                    s.execute(stmt)
                    s.commit()
                    processed += len(batch)
                    _update_task_progress(task_id, processed, total)
                    batch = []
            # flush remaining
            if batch:
                stmt = pg_insert(DailyCandle).values(batch)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["code", "trade_date"],
                    set_={
                        "open": stmt.excluded.open,
                        "high": stmt.excluded.high,
                        "low": stmt.excluded.low,
                        "close": stmt.excluded.close,
                        "volume": stmt.excluded.volume,
                        "amount": stmt.excluded.amount,
                        "change_pct": stmt.excluded.change_pct,
                        "change_amt": stmt.excluded.change_amt,
                        "prev_close": stmt.excluded.prev_close,
                        "turnover_rate": stmt.excluded.turnover_rate,
                        "pe_ratio": stmt.excluded.pe_ratio,
                        "market_cap": stmt.excluded.market_cap,
                        "updated_at": stmt.excluded.updated_at,
                    },
                )
                s.execute(stmt)
                s.commit()
                processed += len(batch)

        _finish_task(task_id, processed, total)
        logger.info("sync_quotes: saved %d quotes → daily_candles (today=%s)", processed, today)
    except Exception as e:
        logger.error("sync_quotes error: %s", e)
        _finish_task(task_id, 0, 0, str(e))
    return task_id


# ═══════════════════════════════════════════════════════════════
#  3. Financial snapshot sync
# ═══════════════════════════════════════════════════════════════

def _classify_fundamental(eps: float, roe: float, rev_yoy: float, np_yoy: float,
                          pe: float = 0, industry_pe: float = 0) -> tuple[str, str]:
    """Multi-metric scoring system (SR-platform style).

    Scoring:
      PE:      relative to industry PE if available, else absolute brackets
      EPS:     >0 → +1, ≤0 → -2
      ROE:     ≥15% → +2, ≥8% → +1, <0% → -2
      Revenue: ≥20% → +2, ≥5% → +1, <-10% → -2
      NetProf: ≥20% → +3, ≥5% → +2, <-20% → -3, <0% → -1

    Status: ≥5 healthy, ≥2 neutral, ≥1 weak (was ≥-1), <1 risk
    """
    score = 0
    tags = []
    available = 0

    # PE (industry-relative first, then absolute)
    if pe > 0:
        available += 1
        if industry_pe and industry_pe > 0:
            relative = pe / industry_pe
            if relative <= 0.8:
                score += 2
                tags.append("低于行业估值")
            elif relative <= 1.2:
                score += 1
            elif relative <= 1.5:
                score -= 1
                tags.append("高于行业估值")
            else:
                score -= 2
                tags.append("显著高于行业")
        else:
            if pe <= 25:
                score += 2
                tags.append(f"PE {pe:.0f}")
            elif pe <= 45:
                score += 1
            elif pe > 80:
                score -= 2
                tags.append(f"PE {pe:.0f}")
    elif pe < 0:
        available += 1
        score -= 2
        tags.append("PE异常")

    # EPS
    if eps != 0 or roe != 0:
        available += 1
        if eps > 0:
            score += 1
        else:
            score -= 2
            tags.append("EPS为负")

    # ROE
    if roe != 0 or eps != 0:
        available += 1
        if roe >= 15:
            score += 2
            tags.append(f"ROE {roe:.1f}%")
        elif roe >= 8:
            score += 1
            tags.append(f"ROE {roe:.1f}%")
        elif roe < 0:
            score -= 2
            tags.append(f"ROE为负")

    # Revenue YoY
    if rev_yoy != 0:
        available += 1
        if rev_yoy >= 20:
            score += 2
            tags.append(f"营收+{rev_yoy:.0f}%")
        elif rev_yoy >= 5:
            score += 1
        elif rev_yoy < -10:
            score -= 2
            tags.append(f"营收{rev_yoy:.0f}%")

    # Net Profit YoY (highest weight)
    if np_yoy != 0:
        available += 1
        if np_yoy >= 20:
            score += 3
            tags.append(f"净利+{np_yoy:.0f}%")
        elif np_yoy >= 5:
            score += 2
        elif np_yoy < -20:
            score -= 3
            tags.append(f"净利{np_yoy:.0f}%")
        elif np_yoy < 0:
            score -= 1

    # Not enough data
    if available < 3:
        return "unknown", "数据不足"

    # Classify
    if score >= 5:
        status = "healthy"
    elif score >= 2:
        status = "neutral"
    elif score >= -1:
        status = "weak"
    else:
        status = "risk"

    # Build summary
    tag_str = " · ".join(tags[:3]) if tags else ""
    label_map = {"healthy": "基本面偏强", "neutral": "基本面中性", "weak": "基本面偏弱", "risk": "基本面风险"}
    summary = f"{label_map[status]}({score}分)"
    if tag_str:
        summary += f" {tag_str}"

    return status, summary


def sync_financials(_task_id: int = None) -> int:
    """Sync financial snapshots for all stocks via East Money batch API.

    Uses RPT_LICO_FN_CPD with ISNEW=1 to get the latest report per stock
    in bulk (500 per page). Enriches with:
      - EPS TTM (computed from financial_history)
      - PE ratio (from today's daily_candles)
      - Industry average PE (for relative scoring)
    ~11000 stocks in ~60 seconds.
    """
    import requests as _req
    from datetime import date as _date

    task_id = _get_or_create_task("financials", _task_id)
    if task_id == -1:
        logger.info("task already running, skipping")
        return -1
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    page_size = 500
    columns = "SECURITY_CODE,BASIC_EPS,WEIGHTAVG_ROE,YSTZ,SJLTZ,TOTAL_OPERATE_INCOME,PARENT_NETPROFIT,REPORTDATE"

    try:
        # Pre-load: EPS history for TTM calculation
        eps_history: dict[str, dict[str, float]] = {}  # code → {period: eps}
        with _get_session() as s:
            hist_rows = s.execute(
                select(FinancialHistory.code, FinancialHistory.report_period, FinancialHistory.eps)
            ).all()
            for code, period, eps in hist_rows:
                eps_history.setdefault(code, {})[period] = eps
        logger.info("sync_financials: loaded EPS history for %d stocks", len(eps_history))

        # Pre-load: PE from today's daily_candles
        today = _date.today().strftime("%Y-%m-%d")
        pe_map: dict[str, float] = {}  # code → pe_ratio
        with _get_session() as s:
            pe_rows = s.execute(
                select(DailyCandle.code, DailyCandle.pe_ratio).where(
                    DailyCandle.trade_date == today,
                    DailyCandle.pe_ratio.isnot(None),
                )
            ).all()
            for code, pe in pe_rows:
                if pe and pe != 0:
                    pe_map[code] = pe
        logger.info("sync_financials: loaded PE for %d stocks from daily_candles", len(pe_map))

        # Pre-load: industry for each stock (for industry PE avg)
        industry_map: dict[str, str] = {}  # code → industry
        with _get_session() as s:
            stk_rows = s.execute(select(Stock.code, Stock.industry)).all()
            for code, ind in stk_rows:
                if ind:
                    industry_map[code] = ind

        # Compute industry average PE
        from collections import defaultdict
        industry_pe_sum = defaultdict(float)
        industry_pe_cnt = defaultdict(int)
        for code, pe in pe_map.items():
            ind = industry_map.get(code, "")
            if ind and 0 < pe < 300:  # filter outliers
                industry_pe_sum[ind] += pe
                industry_pe_cnt[ind] += 1
        industry_avg_pe: dict[str, float] = {}
        for ind in industry_pe_sum:
            if industry_pe_cnt[ind] >= 5:
                industry_avg_pe[ind] = industry_pe_sum[ind] / industry_pe_cnt[ind]
        logger.info("sync_financials: computed avg PE for %d industries", len(industry_avg_pe))

        # Helper: compute EPS TTM
        def _eps_ttm(code: str, period: str, current_eps: float) -> float:
            """TTM = current_cumulative + prev_annual - prev_same_period."""
            if not period or len(period) < 10:
                return current_eps
            # Q4 (annual report) — EPS is already full year
            if period[5:10] == "12-31":
                return current_eps
            hist = eps_history.get(code, {})
            prev_year = str(int(period[:4]) - 1)
            prev_annual = hist.get(f"{prev_year}-12-31")
            prev_same = hist.get(f"{prev_year}-{period[5:10]}")
            if prev_annual is not None and prev_same is not None:
                return current_eps + prev_annual - prev_same
            return current_eps

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
                    eps_raw = float(item.get("BASIC_EPS") or 0)
                    roe = float(item.get("WEIGHTAVG_ROE") or 0)
                    rev_yoy = float(item.get("YSTZ") or 0)
                    np_yoy = float(item.get("SJLTZ") or 0)
                    revenue = float(item.get("TOTAL_OPERATE_INCOME") or 0)
                    net_profit = float(item.get("PARENT_NETPROFIT") or 0)
                    period = str(item.get("REPORTDATE") or "")[:10]

                    # TTM EPS
                    eps = _eps_ttm(code, period, eps_raw)
                    # PE from daily_candles
                    pe = pe_map.get(code, 0.0)
                    # Industry PE
                    ind = industry_map.get(code, "")
                    ind_pe = industry_avg_pe.get(ind, 0.0)

                    status, summary = _classify_fundamental(eps, roe, rev_yoy, np_yoy, pe, ind_pe)

                    existing = s.get(FinancialSnapshot, code)
                    row_data = {
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
#  3b. Historical financial data sync (multi-quarter)
# ═══════════════════════════════════════════════════════════════

def sync_financial_history(_task_id: int = None, years: int = 5) -> int:
    """Sync historical quarterly financial reports for all stocks.

    Fetches last N years of quarterly data from East Money (RPT_LICO_FN_CPD).
    Uses batch pagination (5000/page) + bulk PostgreSQL upsert.
    5 years ≈ 177K records, ~2-3 minutes.
    """
    import requests as _req

    task_id = _get_or_create_task("financial_history", _task_id)
    if task_id == -1:
        logger.info("financial_history: task already running, skipping")
        return -1

    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    page_size = 500  # EM API hard caps at 500
    columns = "SECURITY_CODE,BASIC_EPS,WEIGHTAVG_ROE,YSTZ,SJLTZ,TOTAL_OPERATE_INCOME,PARENT_NETPROFIT,REPORTDATE"
    batch_commit_size = 5000  # commit every N rows to DB

    # Calculate cutoff date
    from datetime import date
    cutoff = date(date.today().year - years, 1, 1).isoformat()

    max_retries = 3
    session = _req.Session()
    session.headers.update(headers)

    def fetch_page(page_num):
        """Fetch a single page with retries."""
        params = {
            "reportName": "RPT_LICO_FN_CPD",
            "columns": columns,
            "pageSize": page_size,
            "pageNumber": page_num,
            "sortColumns": "REPORTDATE,SECURITY_CODE",
            "sortTypes": "-1,-1",
            "filter": f"(REPORTDATE>='{cutoff}')",
        }
        for attempt in range(max_retries):
            try:
                resp = session.get(url, params=params, timeout=30)
                return resp.json()
            except Exception as e:
                if attempt == max_retries - 1:
                    raise
                logger.warning("sync_financial_history: retry %d page %d: %s", attempt + 1, page_num, e)
                time.sleep(2 * (attempt + 1))

    def parse_rows(items):
        """Parse API items into row dicts."""
        now = datetime.utcnow()
        rows = []
        for item in items:
            code = str(item.get("SECURITY_CODE", ""))
            if not code:
                continue
            period = str(item.get("REPORTDATE") or "")[:10]
            if not period:
                continue
            rows.append({
                "code": code,
                "report_period": period,
                "eps": float(item.get("BASIC_EPS") or 0),
                "roe": float(item.get("WEIGHTAVG_ROE") or 0),
                "revenue": float(item.get("TOTAL_OPERATE_INCOME") or 0),
                "net_profit": float(item.get("PARENT_NETPROFIT") or 0),
                "revenue_yoy": float(item.get("YSTZ") or 0),
                "net_profit_yoy": float(item.get("SJLTZ") or 0),
                "updated_at": now,
            })
        return rows

    def dedup_rows(rows):
        """Remove duplicate (code, report_period) keeping last occurrence."""
        seen = {}
        for r in rows:
            seen[(r["code"], r["report_period"])] = r
        return list(seen.values())

    try:
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        # First request to get total count
        data = fetch_page(1)
        result = data.get("result")
        if not result or not result.get("data"):
            _finish_task(task_id, 0, 0, "no data from EM historical financial API")
            return task_id

        total = result.get("count", 0)
        total_pages = (total + page_size - 1) // page_size
        logger.info("sync_financial_history: %d records, %d pages (>=%s)", total, total_pages, cutoff)

        processed = 0
        pending_rows = parse_rows(result["data"])
        processed += len(pending_rows)
        _update_task_progress(task_id, processed, total)

        with _get_session() as s:
            # Sequential fetch (EM API pagination is unstable with parallel)
            page = 2
            while page <= total_pages:
                data = fetch_page(page)
                r = data.get("result")
                if r and r.get("data"):
                    pending_rows.extend(parse_rows(r["data"]))
                else:
                    break  # no more data

                page += 1
                processed = (page - 1) * page_size

                # Bulk upsert when pending rows exceed threshold
                if len(pending_rows) >= batch_commit_size or page > total_pages:
                    pending_rows = dedup_rows(pending_rows)
                    stmt = pg_insert(FinancialHistory).values(pending_rows)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["code", "report_period"],
                        set_={
                            "eps": stmt.excluded.eps,
                            "roe": stmt.excluded.roe,
                            "revenue": stmt.excluded.revenue,
                            "net_profit": stmt.excluded.net_profit,
                            "revenue_yoy": stmt.excluded.revenue_yoy,
                            "net_profit_yoy": stmt.excluded.net_profit_yoy,
                            "updated_at": stmt.excluded.updated_at,
                        },
                    )
                    s.execute(stmt)
                    s.commit()
                    pending_rows = []
                    _update_task_progress(task_id, min(processed, total), total)
                    logger.info("sync_financial_history: %d/%d (page %d/%d)", min(processed, total), total, page - 1, total_pages)

            # Final flush
            if pending_rows:
                pending_rows = dedup_rows(pending_rows)
                stmt = pg_insert(FinancialHistory).values(pending_rows)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["code", "report_period"],
                    set_={
                        "eps": stmt.excluded.eps,
                        "roe": stmt.excluded.roe,
                        "revenue": stmt.excluded.revenue,
                        "net_profit": stmt.excluded.net_profit,
                        "revenue_yoy": stmt.excluded.revenue_yoy,
                        "net_profit_yoy": stmt.excluded.net_profit_yoy,
                        "updated_at": stmt.excluded.updated_at,
                    },
                )
                s.execute(stmt)
                s.commit()

        _finish_task(task_id, total, total)
        logger.info("sync_financial_history: done %d records", total)
    except Exception as e:
        logger.error("sync_financial_history error: %s\n%s", e, traceback.format_exc())
        _finish_task(task_id, 0, 0, str(e))
    finally:
        session.close()
    return task_id


# ═══════════════════════════════════════════════════════════════
#  4. Concept / theme sync
# ═══════════════════════════════════════════════════════════════

def sync_concepts(_task_id: int = None) -> int:
    """Sync stock concepts/themes — same approach as SR platform.

    Phase 1: EM F10 board API (RPT_F10_CORETHEME_BOARDTYPE) — ~90k records,
             covers ALL A-shares, ~180 batch requests.
    Phase 2: 同花顺 concept boards — fetch board list with change%/rank (1 req)
             then constituent stocks per board (~362 reqs).
             Populates concept_boards table + links stock_concepts.board_code.
    Both phases use ON CONFLICT for idempotent upsert.
    """
    import re
    import requests

    task_id = _get_or_create_task("concepts", _task_id)
    if task_id == -1:
        logger.info("task already running, skipping")
        return -1

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    concept_count = 0

    try:
        # ── Phase 1: EM F10 batch API (primary, covers all A-shares) ──
        logger.info("sync_concepts: phase 1 — EM F10 batch API")

        f10_url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        f10_page = 1
        f10_added = 0
        PAGE_SIZE = 10000  # EM supports up to 10000 per page
        while True:
            params = {
                "reportName": "RPT_F10_CORETHEME_BOARDTYPE",
                "columns": "SECURITY_CODE,BOARD_NAME",
                "pageSize": PAGE_SIZE,
                "pageNumber": f10_page,
            }
            try:
                resp = requests.get(f10_url, params=params, headers=headers, timeout=30)
                data = resp.json()
                result = data.get("result")
                if not result or not result.get("data"):
                    if f10_page == 1:
                        logger.warning("sync_concepts: F10 API returned no data")
                    break
            except Exception as e:
                logger.warning("sync_concepts: F10 page %d error: %s", f10_page, e)
                break

            rows = result["data"]
            now = datetime.utcnow()
            with _get_session() as s:
                batch = []
                for item in rows:
                    code = item.get("SECURITY_CODE", "")
                    board = (item.get("BOARD_NAME") or "").strip()
                    if not code or not board:
                        continue
                    batch.append({"code": code, "concept": board, "ts": now})
                if batch:
                    s.execute(
                        text("INSERT INTO stock_concepts (code, concept, source, updated_at) "
                             "VALUES (:code, :concept, 'eastmoney', :ts) "
                             "ON CONFLICT (code, concept) DO NOTHING"),
                        batch,
                    )
                    f10_added += len(batch)
                s.commit()

            logger.info("sync_concepts: F10 page %d/%d, %d mappings",
                        f10_page, result.get("pages", 1), f10_added)
            _update_task_progress(task_id, f10_page, result.get("pages", 1))
            if f10_page >= result.get("pages", 1):
                break
            f10_page += 1
            time.sleep(0.3)

        concept_count += f10_added
        logger.info("sync_concepts: F10 done — %d pages, %d mappings", f10_page, f10_added)

        # ── Phase 2: 同花顺 concept boards (batch per board) ──
        logger.info("sync_concepts: phase 2 — 同花顺 concept boards")
        ths_headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "http://q.10jqka.com.cn/",
        }
        ths_added = 0
        boards = []
        try:
            # Step 1: get all concept boards with change% (1 request)
            resp = requests.get("http://q.10jqka.com.cn/gn/",
                                headers=ths_headers, timeout=15)
            resp.encoding = "gbk"
            pairs = re.findall(
                r'/gn/detail/code/(\d+)/["\'][^>]*>([^<]+)<', resp.text
            )
            seen = set()
            for code, name in pairs:
                name = name.strip()
                if code not in seen and name:
                    seen.add(code)
                    boards.append((code, name))
            logger.info("sync_concepts: THS found %d concept boards", len(boards))

            # Fetch change_pct for top boards from detail pages (top 50 only)
            board_changes: dict[str, float] = {}
            for board_code, board_name in boards[:50]:
                try:
                    detail_url = f"http://q.10jqka.com.cn/gn/detail/code/{board_code}/"
                    r = requests.get(detail_url, headers=ths_headers, timeout=10)
                    r.encoding = "gbk"
                    chg_match = re.search(r'class="board-zf"[^>]*>([+-]?\d+\.?\d*)%', r.text)
                    if chg_match:
                        board_changes[board_code] = float(chg_match.group(1))
                except Exception:
                    pass
                time.sleep(0.1)

            # Sort boards by change% desc and assign rank
            board_with_rank = []
            for bc, bn in boards:
                board_with_rank.append((bc, bn, board_changes.get(bc)))
            board_with_rank.sort(key=lambda x: (x[2] or -999), reverse=True)

            # Upsert into concept_boards table
            now = datetime.utcnow()
            with _get_session() as s:
                for rank_idx, (bc, bn, chg) in enumerate(board_with_rank, 1):
                    s.execute(
                        text("INSERT INTO concept_boards (board_code, concept, change_pct_1d, rank, updated_at) "
                             "VALUES (:bc, :concept, :chg, :rank, :ts) "
                             "ON CONFLICT (board_code) DO UPDATE SET "
                             "concept=:concept, change_pct_1d=:chg, rank=:rank, updated_at=:ts"),
                        {"bc": bc, "concept": bn, "chg": chg, "rank": rank_idx, "ts": now},
                    )
                s.commit()
            logger.info("sync_concepts: upserted %d concept_boards", len(board_with_rank))

            # Step 2: match existing stock_concepts to boards by concept name
            board_map = {bn: bc for bc, bn in boards}  # concept_name → board_code
            now = datetime.utcnow()
            with _get_session() as s:
                for concept_name, board_code in board_map.items():
                    result = s.execute(
                        text("UPDATE stock_concepts SET board_code = :bc, updated_at = :ts "
                             "WHERE concept = :concept AND (board_code IS NULL OR board_code != :bc)"),
                        {"bc": board_code, "concept": concept_name, "ts": now},
                    )
                    ths_added += result.rowcount
                s.commit()
            logger.info("sync_concepts: matched %d stock_concepts to %d boards by name",
                        ths_added, len(board_map))

        except Exception as e:
            logger.warning("sync_concepts: THS phase error: %s", e)

        concept_count += ths_added
        logger.info("sync_concepts: THS done — %d boards, %d mappings",
                     len(boards), ths_added)

        _finish_task(task_id, concept_count, concept_count)
        logger.info("sync_concepts: total %d mappings", concept_count)
    except Exception as e:
        logger.error("sync_concepts error: %s", e)
        _finish_task(task_id, 0, 0, str(e))
    return task_id


def _sync_concepts_ths_f10(task_id: int | None = None) -> int:
    """Phase 3: Fetch concepts per stock from 同花顺 F10 concept page.

    Uses basic.10jqka.com.cn/{code}/concept.html
    Only processes stocks that have NO concepts yet (gap-fill).
    Returns number of new mappings added.
    """
    import re
    import requests

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "http://basic.10jqka.com.cn/",
    }

    with _get_session() as s:
        # Find stocks without any concepts
        all_codes = [r[0] for r in s.execute(text("SELECT code FROM stocks ORDER BY code")).fetchall()]
        covered = {r[0] for r in s.execute(text("SELECT DISTINCT code FROM stock_concepts")).fetchall()}
        missing = [c for c in all_codes if c not in covered]

    logger.info("sync_concepts THS: %d stocks missing concepts (of %d total)", len(missing), len(all_codes))
    if not missing:
        return 0

    added = 0
    errors = 0
    now = datetime.utcnow()

    for i, code in enumerate(missing):
        url = f"http://basic.10jqka.com.cn/{code}/concept.html"
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            resp.encoding = "gbk"
            if resp.status_code != 200:
                errors += 1
                continue
            concepts = re.findall(r'gnName[^>]*>\s*([^\s<]+(?:\s[^\s<]+)*?)\s*<', resp.text)
            if not concepts:
                continue
            with _get_session() as s:
                for concept in concepts:
                    concept = concept.strip()
                    if not concept:
                        continue
                    s.execute(
                        text("INSERT INTO stock_concepts (code, concept, updated_at) "
                             "VALUES (:code, :concept, :ts) ON CONFLICT (code, concept) DO NOTHING"),
                        {"code": code, "concept": concept, "ts": now},
                    )
                s.commit()
                added += len(concepts)
        except Exception as e:
            errors += 1
            if errors <= 5:
                logger.warning("THS concept fetch %s failed: %s", code, e)

        if (i + 1) % 100 == 0:
            logger.info("sync_concepts THS: %d/%d stocks, %d mappings added, %d errors",
                        i + 1, len(missing), added, errors)
        # Rate limit: ~0.15s between requests
        time.sleep(0.15)

    logger.info("sync_concepts THS done: %d mappings from %d stocks (%d errors)",
                added, len(missing), errors)
    return added


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


def _fetch_candles_batch(session, code: str, beg: str, days: int, source: str = "dual") -> list[dict]:
    """Fetch daily candles from configured source.

    source: 'tencent', 'em_push', or 'dual' (tencent first, EM fallback).
    """
    if source == "tencent":
        return _fetch_candles_tencent(session, code, beg, days)
    elif source == "em_push":
        return _fetch_candles_em(session, code, beg, days)
    # dual: try tencent, fallback EM
    rows = _fetch_candles_tencent(session, code, beg, days)
    if rows:
        return rows
    return _fetch_candles_em(session, code, beg, days)


def _fetch_candles_tencent(session, code: str, beg: str, days: int) -> list[dict]:
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
    return []


def _fetch_candles_em(session, code: str, beg: str, days: int) -> list[dict]:
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


# ── EM clist batch API: fetch today's OHLCV for ALL stocks in ~17s ──
_EM_CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"


def _fetch_today_candles_clist(session) -> list[dict]:
    """Fetch today's OHLCV for all A-shares via EM clist pagination.

    Returns list of dicts ready for DB upsert (code, trade_date, open, high, low, close, volume).
    API caps at 100/page, ~56 pages for full market.
    """
    from datetime import date as _date
    today = _date.today().strftime("%Y-%m-%d")
    all_rows = []
    page = 1

    while True:
        params = {
            "pn": page, "pz": 100, "po": 1, "np": 1,
            "fltt": 2, "invt": 2,
            "fid": "f12",  # sort by code for stable pagination
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",  # 沪深A股
            "fields": "f12,f2,f15,f16,f17,f5",  # code,close,high,low,open,volume
        }
        try:
            resp = session.get(_EM_CLIST_URL, params=params, timeout=10)
            data = resp.json().get("data", {})
            items = data.get("diff", [])
        except Exception:
            break

        if not items:
            break

        for item in items:
            code = item.get("f12")
            close = item.get("f2")
            if not code or close is None or close == "-":
                continue
            o = item.get("f17")
            h = item.get("f15")
            l = item.get("f16")
            v = item.get("f5")
            # Skip stocks with any invalid field (suspended etc.)
            if any(x is None or x == "-" for x in (o, h, l, v)):
                continue
            try:
                all_rows.append({
                    "code": code,
                    "trade_date": today,
                    "open": float(o),
                    "high": float(h),
                    "low": float(l),
                    "close": float(close),
                    "volume": int(float(v)),
                })
            except (ValueError, TypeError):
                continue

        page += 1
        if page > 200:  # safety limit
            break

    return all_rows


def sync_candles(days: int = 365, _task_id: int = None) -> int:
    """Batch-sync historical daily candles for all stocks.

    Two-phase strategy:
      1) Batch phase: use EM clist API to grab today's candle for ALL stocks (~17s)
      2) Backfill phase: for stocks with no history, fetch full history per-stock

    This replaces the old all-per-stock approach (15 min → 20s for daily updates).
    """
    import requests
    from requests.adapters import HTTPAdapter
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading

    candle_source = _source_for("daily_candles")
    logger.info("sync_candles: using source '%s'", candle_source)

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
        today_str = _date.today().strftime("%Y-%m-%d")
        cutoff = (_date.today() - timedelta(days=3)).strftime("%Y-%m-%d")
        default_beg = (_date.today() - timedelta(days=days * 2)).strftime("%Y%m%d")

        # ═══════════════════════════════════════════════════════════
        # Phase 1: Batch fetch today's candle via clist API (~17s)
        # ═══════════════════════════════════════════════════════════
        http = requests.Session()
        http.headers["User-Agent"] = "Mozilla/5.0"
        adapter = HTTPAdapter(pool_connections=4, pool_maxsize=4)
        http.mount("https://", adapter)

        logger.info("sync_candles: Phase 1 — fetching today's candles via clist batch API")
        clist_rows = _fetch_today_candles_clist(http)
        logger.info("sync_candles: clist returned %d stocks for %s", len(clist_rows), today_str)

        # Upsert today's candles in bulk
        upsert_sql = text(
            "INSERT INTO daily_candles (code, trade_date, open, high, low, close, volume) "
            "VALUES (:code, :trade_date, :open, :high, :low, :close, :volume) "
            "ON CONFLICT (code, trade_date) DO UPDATE SET "
            "open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, "
            "close=EXCLUDED.close, volume=EXCLUDED.volume"
        )

        if clist_rows:
            # Write in chunks of 5000
            for i in range(0, len(clist_rows), 5000):
                chunk = clist_rows[i:i+5000]
                with _get_session() as s:
                    s.execute(upsert_sql, chunk)
                    s.commit()
            logger.info("sync_candles: Phase 1 done — %d today candles written", len(clist_rows))

        _update_task_progress(task_id, len(clist_rows), len(codes))

        # ═══════════════════════════════════════════════════════════
        # Phase 2: Backfill stocks that have NO history at all
        # ═══════════════════════════════════════════════════════════
        need_backfill: list[tuple[str, str]] = []
        for code in codes:
            last = latest_map.get(code)
            if not last:
                # No cached data at all — need full history
                need_backfill.append((code, default_beg))

        total_backfill = len(need_backfill)
        if total_backfill == 0:
            http.close()
            _finish_task(task_id, len(codes), len(codes))
            logger.info("sync_candles: done — all stocks up-to-date, no backfill needed")
            return task_id

        logger.info("sync_candles: Phase 2 — backfilling %d stocks with no history", total_backfill)

        # Per-stock fetch for backfill (keep existing throttled approach)
        _WORKERS = 8
        _req_lock = threading.Lock()
        _last_req_time = [0.0]

        def _throttled_fetch(code, beg):
            with _req_lock:
                elapsed = time.time() - _last_req_time[0]
                if elapsed < 0.02:
                    time.sleep(0.02 - elapsed)
                _last_req_time[0] = time.time()
            return _fetch_candles_batch(http, code, beg, days, source=candle_source)

        processed = 0
        errors = 0
        no_data = 0
        all_rows: list[dict] = []

        with ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="kline") as executor:
            futures = {
                executor.submit(_throttled_fetch, code, beg): code
                for code, beg in need_backfill
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
                        logger.warning("sync_candles backfill error for %s: %s", code, e)

                # Flush to DB in batches
                if len(all_rows) >= 10000:
                    with _get_session() as s:
                        s.execute(upsert_sql, all_rows)
                        s.commit()
                    logger.info("sync_candles: flushed %d backfill rows to DB", len(all_rows))
                    all_rows = []

                # Update progress periodically
                done = i + 1
                if done % 200 == 0 or done == total_backfill:
                    _update_task_progress(task_id, len(clist_rows) + processed, len(codes))
                    logger.info(
                        "sync_candles backfill: %d/%d (ok=%d, nodata=%d, err=%d)",
                        done, total_backfill, processed, no_data, errors,
                    )

        # Final flush
        if all_rows:
            with _get_session() as s:
                s.execute(upsert_sql, all_rows)
                s.commit()
            logger.info("sync_candles: flushed final %d backfill rows to DB", len(all_rows))

        http.close()
        _finish_task(task_id, len(clist_rows) + processed, len(codes))
        logger.info("sync_candles: done — clist=%d, backfill=%d/%d (nodata=%d, errors=%d)",
                     len(clist_rows), processed, total_backfill, no_data, errors)
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
    """Get latest quote data from daily_candles (today or most recent)."""
    with _get_session() as s:
        row = s.execute(
            select(DailyCandle).where(
                DailyCandle.code == code,
            ).order_by(DailyCandle.trade_date.desc()).limit(1)
        ).scalar_one_or_none()
        if not row:
            return None
        return {
            "open": row.open, "high": row.high, "low": row.low,
            "prev_close": row.prev_close or 0.0,
            "turnover_rate": row.turnover_rate or 0.0,
            "pe_ratio": row.pe_ratio or 0.0,
            "market_cap": row.market_cap or 0.0,
        }


def get_industry_pe(code: str) -> Optional[dict]:
    """Get industry average PE for a stock's industry."""
    with _get_session() as s:
        stock = s.get(Stock, code)
        if not stock or not stock.industry:
            return None
        # Get latest trade_date
        latest = s.execute(
            select(func.max(DailyCandle.trade_date))
        ).scalar()
        if not latest:
            return None
        # Compute industry average PE (filter outliers)
        result = s.execute(
            select(
                func.round(func.avg(DailyCandle.pe_ratio).cast(Numeric), 2),
                func.count(DailyCandle.code),
            ).join(Stock, Stock.code == DailyCandle.code)
            .where(
                Stock.industry == stock.industry,
                DailyCandle.trade_date == latest,
                DailyCandle.pe_ratio > 0,
                DailyCandle.pe_ratio < 300,
            )
        ).one()
        avg_pe = float(result[0]) if result[0] else None
        count = result[1]
        if not avg_pe or count < 3:
            return None
        return {
            "industry": stock.industry,
            "avg_pe": avg_pe,
            "stock_count": count,
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


def get_financial_history(code: str) -> list[dict]:
    """Get historical quarterly financial data for a stock, ordered by period."""
    with _get_session() as s:
        rows = s.execute(
            select(FinancialHistory)
            .where(FinancialHistory.code == code)
            .order_by(FinancialHistory.report_period.asc())
        ).scalars().all()
        return [
            {
                "period": r.report_period,
                "eps": r.eps,
                "roe": r.roe,
                "revenue": r.revenue,
                "net_profit": r.net_profit,
                "revenue_yoy": r.revenue_yoy,
                "net_profit_yoy": r.net_profit_yoy,
            }
            for r in rows
        ]


def get_stock_concepts(code: str, limit: int = 5) -> list[str]:
    """Legacy: return concept names only (for backwards compat)."""
    details = get_stock_concept_details(code, limit=limit)
    return [d["concept"] for d in details]


def get_stock_concept_details(code: str, limit: int = 5) -> list[dict]:
    """Get concept details with heat/strength scoring (like SR platform).

    Joins stock_concepts → concept_boards to get rank, then computes
    heat_level/heat_label/heat_tone for each concept.
    Returns top `limit` concepts sorted by board rank (most relevant first).
    """
    with _get_session() as s:
        rows = s.execute(
            text("""
                SELECT sc.concept, sc.board_code, cb.rank, cb.change_pct_1d
                FROM stock_concepts sc
                LEFT JOIN concept_boards cb ON cb.board_code = sc.board_code
                WHERE sc.code = :code
                ORDER BY cb.rank ASC NULLS LAST, sc.concept ASC
            """),
            {"code": code},
        ).fetchall()

        result = []
        for concept, board_code, rank, change_pct_1d in rows:
            if concept in _CONCEPT_BLACKLIST:
                continue
            if any(concept.endswith(p) for p in _CONCEPT_BLACKLIST_PATTERNS):
                continue
            heat = build_concept_heat_fields(rank)
            result.append({
                "concept": concept,
                "board_code": board_code,
                "rank": rank,
                "change_pct_1d": round(float(change_pct_1d), 2) if change_pct_1d is not None else None,
                **heat,
            })
            if len(result) >= limit:
                break
        return result


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
    from .screener import PATTERN_DETECTORS, clear_sr_cache

    task_id = _get_or_create_task("screener", _task_id)
    if task_id == -1:
        logger.info("task already running, skipping")
        return -1

    # Clear SR cache before scan (each stock gets computed once)
    clear_sr_cache()

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
        # Batch load today's candles into memory
        from datetime import date as _date
        today = _date.today().strftime("%Y-%m-%d")
        with _get_session() as s:
            rows = s.execute(
                select(DailyCandle).where(DailyCandle.trade_date == today)
            ).scalars().all()
            for q in rows:
                if q.close and q.close > 0 and q.open and q.open > 0:
                    quote_map[q.code] = {
                        "open": q.open, "high": q.high, "low": q.low,
                        "close": q.close, "volume": q.volume or 0,
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

    # Load concept + fundamental data for enrichment
    concept_map: dict[str, str] = {}  # code → top concept
    fundamental_map: dict[str, tuple[str, str]] = {}  # code → (status, summary)
    with _get_session() as s:
        from ..models import StockConcept, FinancialSnapshot
        # Concepts: pick the first concept per stock (ordered by id)
        from sqlalchemy import func
        concept_rows = s.query(StockConcept.code, func.min(StockConcept.concept)).group_by(StockConcept.code).all()
        for row in concept_rows:
            concept_map[row[0]] = row[1]
        # Financials
        fin_rows = s.query(FinancialSnapshot.code, FinancialSnapshot.fundamental_status, FinancialSnapshot.fundamental_summary).all()
        for row in fin_rows:
            fundamental_map[row[0]] = (row[1] or "", row[2] or "")
    logger.info("screener: loaded %d concepts, %d financials", len(concept_map), len(fundamental_map))

    universe = list_universe()
    # Filter: main board only (沪A/深A, i.e. 60xxxx and 00xxxx)
    universe = [(c, n, ind) for c, n, ind in universe if c.startswith("6") or c.startswith("0")]
    total_work = len(universe) * len(PATTERN_DETECTORS)
    processed = 0
    logger.info("screener: scanning %d stocks × %d patterns (main board only)", len(universe), len(PATTERN_DETECTORS))

    # Update task total early so the frontend can show progress
    _update_task_progress(task_id, 0, total_work)

    def _get_candles_with_live(code: str) -> list[Candle]:
        candles = get_candles(code, days=180)
        if not candles:
            return []
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

    # Results per pattern
    all_items: dict[str, list] = {p: [] for p in PATTERN_DETECTORS}

    for idx, (code, name, _ind) in enumerate(universe):
        try:
            candles = _get_candles_with_live(code)
            if not candles or len(candles) < 120:
                processed += len(PATTERN_DETECTORS)
                continue

            # Weekly candles: fetch once per stock
            try:
                weekly = get_candles(code, period="weekly", days=80) or None
            except Exception:
                weekly = None

            # Run all patterns for this stock (SR cache valid for one stock)
            clear_sr_cache()
            for pattern, detector in PATTERN_DETECTORS.items():
                r = detector(code, name, candles, weekly_candles=weekly)
                if r:
                    q = quote_map.get(code)
                    r.industry = _ind or ""
                    if code.startswith("6"):
                        r.market = "沪A"
                    elif code.startswith("0"):
                        r.market = "深A"
                    elif code.startswith("3"):
                        r.market = "创业板"
                    else:
                        r.market = ""
                    if q:
                        r.amount = round(q.get("close", 0) * q.get("volume", 0) / 10000, 1)
                    r.concept = concept_map.get(code, "")
                    fin = fundamental_map.get(code)
                    if fin:
                        r.fundamental_status = fin[0]
                        r.fundamental_summary = fin[1]
                    all_items[pattern].append(r)
                processed += 1
        except Exception:
            processed += len(PATTERN_DETECTORS)
        if idx % 50 == 0:
            _update_task_progress(task_id, processed, total_work)

    # Save results per pattern
    for pattern in PATTERN_DETECTORS:
        items = sorted(all_items[pattern], key=lambda x: x.score, reverse=True)
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
                    "market": it.market, "industry": it.industry,
                    "market_cap": it.market_cap, "amount": it.amount,
                    "rr_ratio": it.rr_ratio, "support_score": it.support_score,
                    "concept": it.concept, "fundamental_status": it.fundamental_status,
                    "fundamental_summary": it.fundamental_summary,
                }
                for it in items
            ],
        }
        path = _os.path.join(cache_dir, f"{pattern}.json")
        with open(path, "w") as f:
            _json.dump(result, f, ensure_ascii=False)
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        hist_path = _os.path.join(cache_dir, f"{pattern}_{ts}.json")
        with open(hist_path, "w") as f:
            _json.dump(result, f, ensure_ascii=False)
        logger.info("screener: %s → %d items (saved history %s)", pattern, len(items), ts)

    # Cleanup old history files (keep last 30 per pattern)
    for pattern in PATTERN_DETECTORS:
        import glob
        hist_files = sorted(glob.glob(_os.path.join(cache_dir, f"{pattern}_*.json")), reverse=True)
        for old in hist_files[30:]:
            try:
                _os.remove(old)
            except OSError:
                pass

    _finish_task(task_id, total_work, total_work)
