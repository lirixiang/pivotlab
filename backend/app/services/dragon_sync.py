"""Dragon strategy data sync — 涨停池 / 龙虎榜 / 板块热度历史.

All functions follow the same task-tracking pattern as sync_service.py:
  - _get_or_create_task(task_type, _task_id)
  - _finish_task(task_id, processed, total, error)
  - Use _get_session() for DB writes

Data sources (all via direct HTTP, no akshare dependency):
  - 涨停池/炸板池: https://push2ex.eastmoney.com/getTopicZTPool / getTopicZBPool
  - 龙虎榜:       https://datacenter-web.eastmoney.com/api/data/v1/get
                  reportName=RPT_DAILYBILLBOARD_DETAILSNEW (summary)
                  reportName=RPT_BILLBOARD_TRADEDETAIL    (seat detail)
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, date, timedelta
from typing import Any

import requests
from sqlalchemy import select, delete

from ..models import (
    ZtPoolDaily, LhbRecord, LhbSeatDetail, ConceptHeatHistory,
    DailyCandle, ConceptBoard, StockConcept, Stock,
)
from .sync_service import (
    _get_session, _get_or_create_task, _finish_task, _update_task_progress,
)
from .hot_money_seats import classify_seat

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/",
}
_HTTP_TIMEOUT = 15


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def _yyyymmdd(d: str | None = None) -> str:
    """Return YYYYMMDD for today or the given YYYY-MM-DD string."""
    if d:
        return d.replace("-", "")
    return datetime.now().strftime("%Y%m%d")


def _yyyy_mm_dd(d: str | None = None) -> str:
    if d:
        if len(d) == 8 and d.isdigit():
            return f"{d[:4]}-{d[4:6]}-{d[6:]}"
        return d
    return datetime.now().strftime("%Y-%m-%d")


def _safe_float(v: Any, default: float | None = None) -> float | None:
    try:
        if v is None or v == "" or v == "-":
            return default
        return float(v)
    except (ValueError, TypeError):
        return default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None or v == "" or v == "-":
            return default
        return int(float(v))
    except (ValueError, TypeError):
        return default


# ═══════════════════════════════════════════════════════════════
# 1. 涨停池 / 炸板池 sync
# ═══════════════════════════════════════════════════════════════

def _fetch_zt_pool(date_str: str) -> list[dict]:
    """Fetch 涨停池 from EastMoney.

    Returns list of dicts with normalized fields.
    """
    url = "https://push2ex.eastmoney.com/getTopicZTPool"
    params = {
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
        "dpt": "wz.ztzt",
        "Pageindex": 0,
        "pagesize": 500,
        "sort": "fbt:asc",
        "date": date_str,
        "_": int(time.time() * 1000),
    }
    resp = requests.get(url, params=params, headers=_HEADERS, timeout=_HTTP_TIMEOUT)
    js = resp.json() or {}
    data = (js.get("data") or {}).get("pool") or []
    out = []
    for it in data:
        out.append({
            "code": it.get("c", "").strip(),
            "name": it.get("n", "").strip(),
            "close": _safe_float(it.get("p")),  # in 0.001 yuan units actually? EM uses raw
            "change_pct": _safe_float(it.get("zdp")),
            "amount": _safe_float(it.get("amount")),         # 成交额
            "market_cap": _safe_float(it.get("ltsz")),       # 流通市值
            "turnover_rate": _safe_float(it.get("hs")),      # 换手率
            "first_zt_time": _format_time(it.get("fbt")),
            "last_zt_time": _format_time(it.get("lbt")),
            "open_count": _safe_int(it.get("zbc")),          # 开板次数
            "seal_amount": _safe_float(it.get("fund")),      # 封单资金
            "consecutive": _safe_int(it.get("lbc"), 1),      # 连板数
            "industry": (it.get("hybk") or "").strip(),
        })
    # EM returns price * 1000 sometimes (e.g. p=12345 -> 12.345). Normalize.
    for r in out:
        if r["close"] and r["close"] > 1000:
            r["close"] = round(r["close"] / 1000.0, 2)
    return out


def _fetch_zb_pool(date_str: str) -> list[dict]:
    """Fetch 炸板池 (failed limit-up)."""
    url = "https://push2ex.eastmoney.com/getTopicZBPool"
    params = {
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
        "dpt": "wz.ztzt",
        "Pageindex": 0,
        "pagesize": 500,
        "sort": "fbt:asc",
        "date": date_str,
        "_": int(time.time() * 1000),
    }
    try:
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=_HTTP_TIMEOUT)
        js = resp.json() or {}
        data = (js.get("data") or {}).get("pool") or []
    except Exception:
        return []
    out = []
    for it in data:
        out.append({
            "code": it.get("c", "").strip(),
            "name": it.get("n", "").strip(),
            "close": _safe_float(it.get("p")),
            "change_pct": _safe_float(it.get("zdp")),
            "amount": _safe_float(it.get("amount")),
            "market_cap": _safe_float(it.get("ltsz")),
            "turnover_rate": _safe_float(it.get("hs")),
            "first_zt_time": _format_time(it.get("fbt")),
            "open_count": _safe_int(it.get("zbc")),
            "industry": (it.get("hybk") or "").strip(),
        })
    for r in out:
        if r["close"] and r["close"] > 1000:
            r["close"] = round(r["close"] / 1000.0, 2)
    return out


def _format_time(v: Any) -> str:
    """EM time as HHMMSS int -> HH:MM:SS."""
    if not v:
        return ""
    s = str(v).zfill(6)
    return f"{s[:2]}:{s[2:4]}:{s[4:6]}"


def _enrich_with_concept(records: list[dict]) -> None:
    """Fill concept field from stock_concepts table for each record."""
    if not records:
        return
    codes = [r["code"] for r in records]
    with _get_session() as s:
        rows = s.execute(
            select(StockConcept.code, StockConcept.concept)
            .where(StockConcept.code.in_(codes))
        ).all()
    by_code: dict[str, list[str]] = {}
    for code, concept in rows:
        by_code.setdefault(code, []).append(concept)
    for r in records:
        cs = by_code.get(r["code"], [])
        # Filter generic concepts
        cs = [c for c in cs if c and len(c) <= 12][:6]
        r["concept"] = "/".join(cs)


def sync_zt_pool(_task_id: int = None, date_str: str | None = None) -> int:
    """Sync 涨停池 + 炸板池 for the given date (default: today)."""
    task_id = _get_or_create_task("zt_pool", _task_id)
    if task_id == -1:
        logger.info("zt_pool: already running")
        return -1

    try:
        d8 = _yyyymmdd(date_str)
        d10 = _yyyy_mm_dd(d8)
        zt = _fetch_zt_pool(d8)
        zb = _fetch_zb_pool(d8)
        _enrich_with_concept(zt)
        _enrich_with_concept(zb)
        total = len(zt) + len(zb)
        logger.info("zt_pool[%s]: zt=%d, zb=%d", d10, len(zt), len(zb))

        with _get_session() as s:
            # Wipe old records for this date
            s.execute(delete(ZtPoolDaily).where(ZtPoolDaily.trade_date == d10))
            now = datetime.utcnow()
            for r in zt:
                if not r["code"] or len(r["code"]) != 6:
                    continue
                s.add(ZtPoolDaily(
                    trade_date=d10, code=r["code"], name=r["name"],
                    pool_type="zt",
                    change_pct=r.get("change_pct"),
                    close=r.get("close"),
                    amount=r.get("amount"),
                    market_cap=r.get("market_cap"),
                    turnover_rate=r.get("turnover_rate"),
                    first_zt_time=r.get("first_zt_time", ""),
                    last_zt_time=r.get("last_zt_time", ""),
                    open_count=r.get("open_count", 0),
                    seal_amount=r.get("seal_amount"),
                    zt_status="封板" if r.get("open_count", 0) == 0 else "开板封回",
                    consecutive=r.get("consecutive", 1),
                    concept=r.get("concept", ""),
                    industry=r.get("industry", ""),
                    updated_at=now,
                ))
            for r in zb:
                if not r["code"] or len(r["code"]) != 6:
                    continue
                s.add(ZtPoolDaily(
                    trade_date=d10, code=r["code"], name=r["name"],
                    pool_type="zb",
                    change_pct=r.get("change_pct"),
                    close=r.get("close"),
                    amount=r.get("amount"),
                    market_cap=r.get("market_cap"),
                    turnover_rate=r.get("turnover_rate"),
                    first_zt_time=r.get("first_zt_time", ""),
                    open_count=r.get("open_count", 0),
                    zt_status="炸板",
                    consecutive=0,
                    concept=r.get("concept", ""),
                    industry=r.get("industry", ""),
                    updated_at=now,
                ))
            s.commit()

        _finish_task(task_id, total, total)
        logger.info("zt_pool[%s]: saved %d records", d10, total)
    except Exception as e:
        logger.exception("zt_pool sync failed")
        _finish_task(task_id, 0, 0, str(e))
    return task_id


# ═══════════════════════════════════════════════════════════════
# 2. 龙虎榜 sync
# ═══════════════════════════════════════════════════════════════

def _fetch_lhb_summary(date_str: str) -> list[dict]:
    """Fetch 龙虎榜 daily summary (one row per stock)."""
    d10 = _yyyy_mm_dd(date_str)
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    params = {
        "sortColumns": "SECURITY_CODE,TRADE_DATE",
        "sortTypes": "1,-1",
        "pageSize": 500,
        "pageNumber": 1,
        "reportName": "RPT_DAILYBILLBOARD_DETAILSNEW",
        "columns": "ALL",
        "source": "WEB",
        "client": "WEB",
        "filter": f"(TRADE_DATE>='{d10}')(TRADE_DATE<='{d10}')",
    }
    out = []
    page = 1
    while True:
        params["pageNumber"] = page
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=_HTTP_TIMEOUT)
        js = resp.json() or {}
        result = js.get("result") or {}
        rows = result.get("data") or []
        if not rows:
            break
        for r in rows:
            out.append({
                "code": (r.get("SECURITY_CODE") or "").strip(),
                "name": (r.get("SECURITY_NAME_ABBR") or "").strip(),
                "reason": (r.get("EXPLANATION") or "").strip(),
                "close": _safe_float(r.get("CLOSE_PRICE")),
                "change_pct": _safe_float(r.get("CHANGE_RATE")),
                "turnover": _safe_float(r.get("ACCUM_AMOUNT") or r.get("TURNOVERVALUE")),
                "buy_total": _safe_float(r.get("BILLBOARD_BUY_AMT")) or 0.0,
                "sell_total": _safe_float(r.get("BILLBOARD_SELL_AMT")) or 0.0,
                "net_amount": _safe_float(r.get("BILLBOARD_NET_AMT")) or 0.0,
                "net_rate": _safe_float(r.get("BILLBOARD_NET_AMT_RATE")),
            })
        total_pages = result.get("pages", 1)
        if page >= total_pages:
            break
        page += 1
    return out


def _fetch_lhb_seats(date_str: str) -> list[dict]:
    """Fetch per-seat trade details for the given day."""
    d10 = _yyyy_mm_dd(date_str)
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    params = {
        "sortColumns": "SECURITY_CODE,RANK",
        "sortTypes": "1,1",
        "pageSize": 1000,
        "pageNumber": 1,
        "reportName": "RPT_BILLBOARD_TRADEDETAILS",
        "columns": "ALL",
        "source": "WEB",
        "client": "WEB",
        "filter": f"(TRADE_DATE='{d10}')",
    }
    out = []
    page = 1
    while True:
        params["pageNumber"] = page
        try:
            resp = requests.get(url, params=params, headers=_HEADERS, timeout=_HTTP_TIMEOUT)
            js = resp.json() or {}
        except Exception as e:
            logger.warning("lhb seats fetch failed: %s", e)
            break
        result = js.get("result") or {}
        rows = result.get("data") or []
        if not rows:
            break
        for r in rows:
            buy_amt = _safe_float(r.get("BUY")) or 0.0
            sell_amt = _safe_float(r.get("SELL")) or 0.0
            side = "buy" if buy_amt >= sell_amt else "sell"
            out.append({
                "code": (r.get("SECURITY_CODE") or "").strip(),
                "rank": _safe_int(r.get("RANK")),
                "side": side,
                "seat_name": (r.get("OPERATEDEPT_NAME") or "").strip(),
                "buy_amount": buy_amt,
                "sell_amount": sell_amt,
                "net_amount": buy_amt - sell_amt,
            })
        total_pages = result.get("pages", 1)
        if page >= total_pages:
            break
        page += 1
    return out


def sync_lhb(_task_id: int = None, date_str: str | None = None) -> int:
    """Sync 龙虎榜 (records + seat details) for the given date."""
    task_id = _get_or_create_task("lhb", _task_id)
    if task_id == -1:
        logger.info("lhb: already running")
        return -1

    try:
        d10 = _yyyy_mm_dd(date_str) if date_str else datetime.now().strftime("%Y-%m-%d")
        records = _fetch_lhb_summary(d10)
        seats = _fetch_lhb_seats(d10)
        logger.info("lhb[%s]: records=%d, seats=%d", d10, len(records), len(seats))

        with _get_session() as s:
            s.execute(delete(LhbRecord).where(LhbRecord.trade_date == d10))
            s.execute(delete(LhbSeatDetail).where(LhbSeatDetail.trade_date == d10))
            now = datetime.utcnow()
            for r in records:
                if not r["code"] or len(r["code"]) != 6:
                    continue
                s.add(LhbRecord(
                    trade_date=d10, code=r["code"], name=r["name"],
                    reason=r["reason"], close=r["close"],
                    change_pct=r["change_pct"], turnover=r["turnover"],
                    buy_total=r["buy_total"], sell_total=r["sell_total"],
                    net_amount=r["net_amount"], net_rate=r["net_rate"],
                    updated_at=now,
                ))
            for r in seats:
                if not r["code"] or len(r["code"]) != 6:
                    continue
                tag = classify_seat(r["seat_name"])
                s.add(LhbSeatDetail(
                    trade_date=d10, code=r["code"], rank=r["rank"],
                    side=r["side"], seat_name=r["seat_name"],
                    buy_amount=r["buy_amount"], sell_amount=r["sell_amount"],
                    net_amount=r["net_amount"],
                    is_known_hot=bool(tag), hot_money_tag=tag or "",
                    updated_at=now,
                ))
            s.commit()

        total = len(records) + len(seats)
        _finish_task(task_id, total, total)
        logger.info("lhb[%s]: saved", d10)
    except Exception as e:
        logger.exception("lhb sync failed")
        _finish_task(task_id, 0, 0, str(e))
    return task_id


# ═══════════════════════════════════════════════════════════════
# 3. 板块热度历史快照
# ═══════════════════════════════════════════════════════════════

def sync_concept_heat_history(_task_id: int = None, date_str: str | None = None) -> int:
    """Snapshot today's concept_boards + leader info into concept_heat_history.

    Computes leader code/change/consecutive from zt_pool_daily.
    """
    task_id = _get_or_create_task("concept_heat_history", _task_id)
    if task_id == -1:
        logger.info("concept_heat_history: already running")
        return -1

    try:
        d10 = _yyyy_mm_dd(date_str) if date_str else datetime.now().strftime("%Y-%m-%d")

        with _get_session() as s:
            boards = s.execute(select(ConceptBoard)).scalars().all()
            if not boards:
                _finish_task(task_id, 0, 0, "no concept_boards data")
                return task_id

            # Map: concept -> [stock codes]
            cm = s.execute(select(StockConcept.code, StockConcept.concept)).all()
            concept_codes: dict[str, list[str]] = {}
            for code, concept in cm:
                concept_codes.setdefault(concept, []).append(code)

            # Today's ZT pool indexed by code
            zt_rows = s.execute(
                select(ZtPoolDaily).where(
                    ZtPoolDaily.trade_date == d10,
                    ZtPoolDaily.pool_type == "zt",
                )
            ).scalars().all()
            zt_by_code = {r.code: r for r in zt_rows}

            # Today's quote: change_pct from daily_candles
            candle_rows = s.execute(
                select(DailyCandle.code, DailyCandle.change_pct)
                .where(DailyCandle.trade_date == d10)
            ).all()
            change_by_code = {c: pct for c, pct in candle_rows if pct is not None}

            # Stock name lookup
            stock_rows = s.execute(select(Stock.code, Stock.name)).all()
            name_by_code = {c: n for c, n in stock_rows}

            s.execute(delete(ConceptHeatHistory).where(ConceptHeatHistory.trade_date == d10))
            now = datetime.utcnow()
            saved = 0
            for b in boards:
                codes = concept_codes.get(b.concept, [])
                zt_count = sum(1 for c in codes if c in zt_by_code)
                up_count = sum(1 for c in codes if change_by_code.get(c, 0) > 0)
                up_ratio = (up_count / len(codes)) if codes else 0.0

                # Leader: highest consecutive in ZT pool, tiebreak by change_pct
                leader_code = ""
                leader_change = 0.0
                leader_cons = 0
                best_score = -1.0
                for c in codes:
                    z = zt_by_code.get(c)
                    cons = z.consecutive if z else 0
                    chg = (z.change_pct if z else change_by_code.get(c, 0.0)) or 0.0
                    score = cons * 100 + chg
                    if score > best_score:
                        best_score = score
                        leader_code = c
                        leader_change = chg
                        leader_cons = cons

                # Heat score: re-derive from existing fields
                heat_score = 0.0
                if b.change_pct_1d is not None:
                    heat_score += min(max(b.change_pct_1d, 0), 5) / 5 * 30
                heat_score += min(zt_count / 5.0, 1.0) * 25
                heat_score += min(up_ratio, 1.0) * 25
                heat_score += min(max(leader_change, 0), 10) / 10 * 20
                heat_score = round(heat_score, 1)

                if heat_score >= 75 or (b.rank and b.rank <= 10):
                    heat_level = "core"
                elif heat_score >= 55 or (b.rank and b.rank <= 25):
                    heat_level = "hot"
                elif heat_score >= 35 or (b.rank and b.rank <= 50):
                    heat_level = "watch"
                else:
                    heat_level = "observe"

                s.add(ConceptHeatHistory(
                    trade_date=d10, board_code=b.board_code, concept=b.concept,
                    change_pct=b.change_pct_1d, net_inflow=b.net_inflow,
                    heat_score=heat_score, heat_level=heat_level,
                    rank=b.rank, zt_count=zt_count, up_ratio=round(up_ratio, 4),
                    leader_code=leader_code,
                    leader_name=name_by_code.get(leader_code, ""),
                    leader_change=leader_change,
                    leader_consecutive=leader_cons,
                    updated_at=now,
                ))
                saved += 1
            s.commit()

        _finish_task(task_id, saved, saved)
        logger.info("concept_heat_history[%s]: saved %d", d10, saved)
    except Exception as e:
        logger.exception("concept_heat_history sync failed")
        _finish_task(task_id, 0, 0, str(e))
    return task_id


# ═══════════════════════════════════════════════════════════════
# Combined: run all dragon syncs in sequence
# ═══════════════════════════════════════════════════════════════

def sync_dragon_all(_task_id: int = None, date_str: str | None = None) -> int:
    """Run zt_pool + lhb + concept_heat_history in sequence."""
    task_id = _get_or_create_task("dragon_all", _task_id)
    if task_id == -1:
        return -1
    try:
        sync_zt_pool(date_str=date_str)
        sync_lhb(date_str=date_str)
        sync_concept_heat_history(date_str=date_str)
        _finish_task(task_id, 3, 3)
    except Exception as e:
        logger.exception("dragon_all failed")
        _finish_task(task_id, 0, 0, str(e))
    return task_id


# ═══════════════════════════════════════════════════════════════
# Backfill: iterate over a historical date range
# ═══════════════════════════════════════════════════════════════

def _iter_trading_days(start: str, end: str):
    """Yield YYYY-MM-DD dates between start and end (inclusive), skipping weekends."""
    s = datetime.strptime(start.replace("-", ""), "%Y%m%d").date()
    e = datetime.strptime(end.replace("-", ""), "%Y%m%d").date()
    if s > e:
        s, e = e, s
    cur = s
    while cur <= e:
        if cur.weekday() < 5:  # Mon–Fri
            yield cur.strftime("%Y-%m-%d")
        cur += timedelta(days=1)


def backfill_dragon_history(
    _task_id: int = None,
    start_date: str | None = None,
    end_date: str | None = None,
    days: int | None = None,
    include_lhb: bool = True,
    include_zt: bool = True,
    include_concept: bool = True,
    sleep_sec: float = 0.3,
) -> int:
    """Backfill dragon-strategy historical data over a date range.

    Either pass (start_date, end_date) as YYYY-MM-DD, or `days` (last N calendar days
    counting back from today). Weekends are skipped automatically; non-trading days
    return zero rows and are silently no-op.
    """
    task_id = _get_or_create_task("dragon_backfill", _task_id)
    if task_id == -1:
        logger.info("dragon_backfill: already running")
        return -1

    try:
        if not start_date or not end_date:
            n = days or 60
            today = datetime.now().date()
            end_date = today.strftime("%Y-%m-%d")
            start_date = (today - timedelta(days=n)).strftime("%Y-%m-%d")

        dates = list(_iter_trading_days(start_date, end_date))
        total = len(dates)
        logger.info(
            "dragon_backfill: %s → %s (%d trading days, zt=%s lhb=%s concept=%s)",
            start_date, end_date, total, include_zt, include_lhb, include_concept,
        )
        _update_task_progress(task_id, 0, total)

        processed = 0
        errors: list[str] = []
        for d in dates:
            try:
                if include_zt:
                    sync_zt_pool(date_str=d)
                    time.sleep(sleep_sec)
                if include_lhb:
                    sync_lhb(date_str=d)
                    time.sleep(sleep_sec)
                if include_concept:
                    sync_concept_heat_history(date_str=d)
                    time.sleep(sleep_sec)
            except Exception as e:
                logger.warning("dragon_backfill: %s failed: %s", d, e)
                errors.append(f"{d}:{e}")
            processed += 1
            _update_task_progress(task_id, processed, total)

        err_msg = ("; ".join(errors[:5]) + (f" (+{len(errors)-5} more)" if len(errors) > 5 else "")) if errors else None
        _finish_task(task_id, processed, total, err_msg)
    except Exception as e:
        logger.exception("dragon_backfill failed")
        _finish_task(task_id, 0, 0, str(e))
    return task_id
