import asyncio
import json
import logging
import os
import time
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import WatchlistItem, DailyCandle, Stock, FinancialSnapshot, StockConcept
from ..schemas import WatchlistCreate
from ..services.data_provider import get_candles, get_quote
from ..services.levels_multifactor import (
    compute_decision_score,
    detect_levels_multifactor,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])

# In-memory cache for decision scores (code -> (score, label, ts))
_score_cache: dict[str, tuple[float, str, float]] = {}
_SCORE_TTL = 300  # 5 min cache


# Screener cache directory (relative to backend root)
_SCREENER_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ".screener_cache",
)
_PATTERN_LABELS = {
    "breakout_pullback": "突破回踩",
    "bottom_stabilize": "下跌企稳",
    "stabilize": "下跌企稳",
    "box_support": "箱体支撑",
    "volume_breakout": "放量突破",
    "macd_divergence": "MACD背离",
    "near_support": "靠近支撑",
}


def _load_screener_hits() -> dict[str, dict]:
    """Return code -> best screener hit across all patterns (highest score wins)."""
    out: dict[str, dict] = {}
    if not os.path.isdir(_SCREENER_CACHE_DIR):
        return out
    for fname in os.listdir(_SCREENER_CACHE_DIR):
        if not fname.endswith(".json") or "_2" in fname:  # skip history snapshots
            continue
        pattern = fname[:-5]
        try:
            with open(os.path.join(_SCREENER_CACHE_DIR, fname)) as f:
                data = json.load(f)
            for it in data.get("items", []):
                code = it.get("code", "")
                if not code:
                    continue
                score = it.get("score", 0) or 0
                prev = out.get(code)
                if prev is None or score > prev.get("score", 0):
                    out[code] = {**it, "pattern": pattern,
                                 "pattern_label": _PATTERN_LABELS.get(pattern, pattern)}
        except Exception:
            continue
    return out


@router.get("")
async def list_watchlist(db: AsyncSession = Depends(get_db)):
    """Return watchlist items enriched with latest quote data."""
    rows = (
        await db.execute(
            select(WatchlistItem).order_by(WatchlistItem.created_at.desc())
        )
    ).scalars().all()
    if not rows:
        return []

    codes = [r.code for r in rows]
    # Batch-load today's candle data (replaces quote_cache)
    from datetime import date as _date
    today = _date.today().strftime("%Y-%m-%d")
    quotes = {}
    qrows = (await db.execute(
        select(DailyCandle).where(DailyCandle.code.in_(codes), DailyCandle.trade_date == today)
    )).scalars().all()
    for q in qrows:
        quotes[q.code] = q

    stocks = {}
    srows = (await db.execute(select(Stock).where(Stock.code.in_(codes)))).scalars().all()
    for s in srows:
        stocks[s.code] = s

    # Financial snapshots
    fins: dict[str, FinancialSnapshot] = {}
    frows = (await db.execute(
        select(FinancialSnapshot).where(FinancialSnapshot.code.in_(codes))
    )).scalars().all()
    for f in frows:
        fins[f.code] = f

    # Concepts: top 3 per code
    concepts_map: dict[str, list[str]] = {}
    crows = (await db.execute(
        select(StockConcept).where(StockConcept.code.in_(codes)).order_by(StockConcept.code, StockConcept.id)
    )).scalars().all()
    for c in crows:
        lst = concepts_map.setdefault(c.code, [])
        if len(lst) < 3 and c.concept:
            lst.append(c.concept)

    # Screener best-hit per code
    hits = _load_screener_hits()

    # Sparkline: last 30 closes per code
    sparks: dict[str, list[float]] = {}
    spark_rows = (await db.execute(
        select(DailyCandle.code, DailyCandle.trade_date, DailyCandle.close)
        .where(DailyCandle.code.in_(codes))
        .order_by(DailyCandle.code, DailyCandle.trade_date.desc())
    )).all()
    for code, _td, close in spark_rows:
        lst = sparks.setdefault(code, [])
        if len(lst) < 30 and close is not None:
            lst.append(round(float(close), 3))
    for code in sparks:
        sparks[code].reverse()

    result = []
    for r in rows:
        q = quotes.get(r.code)
        s = stocks.get(r.code)
        f = fins.get(r.code)
        hit = hits.get(r.code)
        result.append({
            "id": r.id,
            "code": r.code,
            "name": (s.name if s else "") or r.name,
            "note": r.note,
            "industry": s.industry if s else "",
            "market": s.market if s else "",
            "price": (q.close if q else None) or 0.0,
            "change_pct": (q.change_pct if q else None) or 0.0,
            "volume": (q.volume if q else None) or 0.0,
            "amount": (q.amount if q else None) or 0.0,
            "turnover_rate": (q.turnover_rate if q else None) or 0.0,
            "pe": (q.pe_ratio if q and q.pe_ratio else (f.pe_ratio_ttm if f else None)),
            "market_cap": (q.market_cap if q else None) or 0.0,
            "roe": f.roe if f else None,
            "fundamental_status": (f.fundamental_status if f else "unknown") or "unknown",
            "fundamental_summary": (f.fundamental_summary if f else "") or "",
            "concepts": concepts_map.get(r.code, []),
            "sparkline": sparks.get(r.code, []),
            # Screener-derived fields (best hit across patterns)
            "score": hit.get("score") if hit else None,
            "pattern": hit.get("pattern") if hit else None,
            "pattern_label": hit.get("pattern_label") if hit else None,
            "triggers": hit.get("triggers", []) if hit else [],
            "distance_to_support_pct": hit.get("distance_to_support_pct") if hit else None,
            "rr_ratio": hit.get("rr_ratio") if hit else None,
            "support_score": hit.get("support_score") if hit else None,
            "volume_ratio": hit.get("volume_ratio") if hit else None,
            "created_at": r.created_at.isoformat() if r.created_at else "",
        })
    return result


@router.post("")
async def add_watchlist(item: WatchlistCreate, db: AsyncSession = Depends(get_db)):
    existing = (
        await db.execute(
            select(WatchlistItem).where(WatchlistItem.code == item.code)
        )
    ).scalar_one_or_none()
    if existing:
        return {"id": existing.id, "code": existing.code, "name": existing.name, "ok": True}
    name = item.name
    if not name:
        stock = (await db.execute(select(Stock).where(Stock.code == item.code))).scalar_one_or_none()
        if stock:
            name = stock.name
        else:
            try:
                name = get_quote(item.code).name
            except Exception:
                name = item.code
    row = WatchlistItem(code=item.code, name=name, note=item.note)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"id": row.id, "code": row.code, "name": row.name, "ok": True}


@router.delete("/{code}")
async def remove_watchlist(code: str, db: AsyncSession = Depends(get_db)):
    row = (
        await db.execute(
            select(WatchlistItem).where(WatchlistItem.code == code)
        )
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "not found")
    await db.delete(row)
    await db.commit()
    return {"ok": True}


def _compute_one(code: str) -> tuple[str, float, str]:
    """Compute decision score for a single stock (runs in thread)."""
    try:
        cached = _score_cache.get(code)
        if cached and time.time() - cached[2] < _SCORE_TTL:
            return code, cached[0], cached[1]

        candles = get_candles(code, period="daily", days=240)
        if not candles:
            return code, 0.0, "—"

        levels = detect_levels_multifactor(candles)
        price = candles[-1].close
        score, label = compute_decision_score(candles, levels, price)
        _score_cache[code] = (score, label, time.time())
        return code, score, label
    except Exception as exc:
        logger.warning("decision score failed for %s: %s", code, exc)
        return code, 0.0, "—"


@router.get("/scores")
async def watchlist_scores(db: AsyncSession = Depends(get_db)):
    """Return decision scores for all watchlist stocks."""
    rows = (
        await db.execute(
            select(WatchlistItem).order_by(WatchlistItem.created_at.desc())
        )
    ).scalars().all()
    if not rows:
        return []

    codes = [r.code for r in rows]
    loop = asyncio.get_running_loop()
    tasks = [loop.run_in_executor(None, _compute_one, c) for c in codes]
    results = await asyncio.gather(*tasks)

    return [
        {"code": code, "decision_score": score, "decision_label": label}
        for code, score, label in results
    ]


def _scan_watchlist_patterns_sync(items: list[tuple[str, str]]) -> dict:
    """Run all pattern detectors against the given watchlist codes, then merge
    results into `.screener_cache/{pattern}.json` so the regular GET /watchlist
    enrichment picks them up."""
    import os as _os
    import json as _json
    from datetime import datetime as _dt

    from ..services.screener import (
        PATTERN_DETECTORS,
        MODEL_LABELS,
        clear_sr_cache,
    )
    from ..services.data_provider import get_candles

    cache_dir = _SCREENER_CACHE_DIR
    _os.makedirs(cache_dir, exist_ok=True)

    # Run detectors in-memory; collect new hits per pattern
    new_hits: dict[str, list[dict]] = {p: [] for p in PATTERN_DETECTORS}
    counts: dict[str, int] = {p: 0 for p in PATTERN_DETECTORS}
    scanned = 0
    for code, name in items:
        try:
            candles = get_candles(code, days=180)
            if not candles or len(candles) < 120:
                continue
            try:
                weekly = get_candles(code, period="weekly", days=80) or None
            except Exception:
                weekly = None
            scanned += 1
            clear_sr_cache()
            for pat, detector in PATTERN_DETECTORS.items():
                try:
                    r = detector(code, name, candles, weekly_candles=weekly)
                except Exception:
                    r = None
                if not r:
                    continue
                counts[pat] += 1
                # Serialise the same fields the bulk screener produces.
                new_hits[pat].append({
                    "code": r.code, "name": r.name, "pattern": r.pattern,
                    "score": r.score, "price": r.price,
                    "change_pct": getattr(r, "change_pct", None),
                    "volume_ratio": getattr(r, "volume_ratio", None),
                    "breakout_price": getattr(r, "breakout_price", None),
                    "pullback_price": getattr(r, "pullback_price", None),
                    "distance_to_support_pct": getattr(r, "distance_to_support_pct", None),
                    "triggers": getattr(r, "triggers", []) or [],
                    "market": getattr(r, "market", "") or "",
                    "industry": getattr(r, "industry", "") or "",
                    "market_cap": getattr(r, "market_cap", None),
                    "amount": getattr(r, "amount", None),
                    "rr_ratio": getattr(r, "rr_ratio", None),
                    "support_score": getattr(r, "support_score", None),
                    "concept": getattr(r, "concept", "") or "",
                    "fundamental_status": getattr(r, "fundamental_status", "") or "",
                    "fundamental_summary": getattr(r, "fundamental_summary", "") or "",
                })
        except Exception as exc:
            logger.warning("watchlist scan failed for %s: %s", code, exc)

    scanned_codes = {c for c, _ in items}
    scanned_at = _dt.now().isoformat()

    # Merge into existing cache files: drop any prior entries for these codes,
    # then append the fresh hits. This keeps full-market hits intact while
    # refreshing the watchlist view.
    for pat in PATTERN_DETECTORS:
        path = _os.path.join(cache_dir, f"{pat}.json")
        existing: dict = {"pattern": pat, "total": 0, "scanned": 0,
                          "scanned_at": scanned_at, "items": []}
        if _os.path.exists(path):
            try:
                with open(path) as f:
                    existing = _json.load(f)
            except Exception:
                pass
        kept = [it for it in existing.get("items", []) if it.get("code") not in scanned_codes]
        merged = kept + new_hits[pat]
        merged.sort(key=lambda x: x.get("score") or 0, reverse=True)
        existing["items"] = merged
        existing["total"] = len(merged)
        existing["scanned_at"] = scanned_at
        try:
            with open(path, "w") as f:
                _json.dump(existing, f, ensure_ascii=False)
        except Exception as exc:
            logger.warning("watchlist scan: write %s failed: %s", path, exc)

    return {
        "scanned": scanned,
        "total_hits": sum(counts.values()),
        "counts": {p: counts[p] for p in counts if counts[p] > 0},
        "labels": {p: MODEL_LABELS.get(p, p) for p in PATTERN_DETECTORS},
        "scanned_at": scanned_at,
    }


@router.post("/scan-patterns")
async def scan_watchlist_patterns(db: AsyncSession = Depends(get_db)):
    """Run all pattern detectors against just the watchlist codes (fast, sync).
    Updates `.screener_cache/{pattern}.json` so the regular list endpoint
    immediately reflects the new pattern tags."""
    rows = (await db.execute(select(WatchlistItem))).scalars().all()
    if not rows:
        return {"scanned": 0, "total_hits": 0, "counts": {}, "labels": {}, "scanned_at": ""}
    items = [(r.code, r.name or r.code) for r in rows]
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _scan_watchlist_patterns_sync, items)
