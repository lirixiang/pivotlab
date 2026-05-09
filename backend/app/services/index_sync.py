"""Sync daily k-lines for major A-share indices from Tencent kline API.

Endpoint:  http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh000001,day,,,640,
Response:  {"code":0,"data":{"sh000001":{"day":[["2024-01-02",O,C,H,L,Vol], ...]}}}
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Iterable

import requests
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..database import DATABASE_URL
from ..models import IndexCandle

logger = logging.getLogger(__name__)

INDEX_CODES: list[tuple[str, str]] = [
    ("sh000001", "上证指数"),
    ("sz399001", "深证成指"),
    ("sz399006", "创业板指"),
    ("sh000300", "沪深300"),
    ("sh000905", "中证500"),
]

_TIMEOUT = 10


def _sync_url() -> str:
    return (
        str(DATABASE_URL)
        .replace("sqlite+aiosqlite", "sqlite")
        .replace("postgresql+asyncpg", "postgresql+psycopg2")
    )


_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(_sync_url(), echo=False, pool_pre_ping=True)
    return _engine


def fetch_index_kline(code: str, n: int = 800) -> list[dict]:
    """Fetch up to `n` daily candles for an index from Tencent."""
    url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,{n},"
    try:
        r = requests.get(url, timeout=_TIMEOUT)
        r.raise_for_status()
        payload = r.json()
    except Exception as e:
        logger.warning("index kline fetch %s failed: %s", code, e)
        return []
    data = (payload or {}).get("data", {}).get(code, {})
    rows = data.get("day") or data.get("qfqday") or []
    out: list[dict] = []
    for row in rows:
        if len(row) < 6:
            continue
        try:
            d, o, c, h, l, v = row[0], float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])
        except Exception:
            continue
        amount = float(row[6]) if len(row) > 6 else 0.0
        out.append({
            "code": code,
            "trade_date": d,
            "open": o, "high": h, "low": l, "close": c,
            "volume": v, "amount": amount,
        })
    # compute pct_change vs prior close
    for i in range(1, len(out)):
        prev = out[i - 1]["close"]
        if prev > 0:
            out[i]["pct_change"] = (out[i]["close"] / prev - 1) * 100
    return out


def sync_indices(progress_cb=None) -> dict:
    """Pull k-lines for all major indices and upsert into index_candles."""
    eng = _get_engine()
    counts: dict[str, int] = {}
    with Session(eng) as session:
        for i, (code, name) in enumerate(INDEX_CODES):
            if progress_cb:
                progress_cb({"phase": "fetching", "code": code,
                             "pct": int(i / len(INDEX_CODES) * 100)})
            rows = fetch_index_kline(code, n=800)
            if not rows:
                counts[code] = 0
                continue
            stmt = pg_insert(IndexCandle).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["code", "trade_date"],
                set_={
                    "open": stmt.excluded.open,
                    "high": stmt.excluded.high,
                    "low": stmt.excluded.low,
                    "close": stmt.excluded.close,
                    "volume": stmt.excluded.volume,
                    "amount": stmt.excluded.amount,
                    "pct_change": stmt.excluded.pct_change,
                    "updated_at": datetime.utcnow(),
                },
            )
            session.execute(stmt)
            session.commit()
            counts[code] = len(rows)
            logger.info("index_sync: %s (%s) → %d rows", code, name, len(rows))
    if progress_cb:
        progress_cb({"phase": "done", "pct": 100, "counts": counts})
    return counts


def get_recent_closes(code: str = "sh000001", days: int = 30) -> list[tuple[str, float]]:
    eng = _get_engine()
    with Session(eng) as session:
        rows = session.execute(
            select(IndexCandle.trade_date, IndexCandle.close)
            .where(IndexCandle.code == code)
            .order_by(IndexCandle.trade_date.desc())
            .limit(days)
        ).all()
    return [(r[0], float(r[1])) for r in reversed(rows)]


def market_environment_from_index(code: str = "sh000001") -> tuple[float, float]:
    """Compute (trend, atr_pct) from real index k-lines.

    trend ∈ [-1, +1], based on:
      - sign of (close vs MA5, MA10, MA20)
      - 5-day return magnitude
    """
    closes = get_recent_closes(code, days=60)
    if len(closes) < 25:
        return 0.0, 0.0
    cl = [c for _, c in closes]
    last = cl[-1]
    ma5 = sum(cl[-5:]) / 5
    ma10 = sum(cl[-10:]) / 10
    ma20 = sum(cl[-20:]) / 20
    score = 0
    if last > ma5: score += 1
    else: score -= 1
    if last > ma10: score += 1
    else: score -= 1
    if last > ma20: score += 1
    else: score -= 1
    if ma5 > ma10: score += 1
    else: score -= 1
    if ma10 > ma20: score += 1
    else: score -= 1
    # 5-day return push
    ret5 = (cl[-1] / cl[-6] - 1) if cl[-6] > 0 else 0
    if ret5 > 0.03: score += 1
    elif ret5 < -0.03: score -= 1
    trend = max(-1.0, min(1.0, score / 6.0))

    # ATR% proxy (last 14 closes)
    diffs = [abs(cl[i] - cl[i - 1]) for i in range(-14, 0)]
    atr = sum(diffs) / max(1, len(diffs))
    atr_pct = atr / last if last > 0 else 0.0
    return trend, atr_pct
