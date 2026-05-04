from datetime import datetime

from fastapi import APIRouter, Query

from ..schemas import ScreenerResponse
from ..services.data_provider import get_candles, list_universe
from ..services.screener import PATTERN_DETECTORS

router = APIRouter(prefix="/api/screener", tags=["screener"])


@router.get("/{pattern}", response_model=ScreenerResponse)
async def run_screener(
    pattern: str,
    limit: int = Query(50, ge=1, le=200),
    min_score: float = Query(0, ge=0, le=100),
):
    detector = PATTERN_DETECTORS.get(pattern)
    if not detector:
        return ScreenerResponse(
            pattern=pattern, total=0, scanned=0,
            scanned_at=datetime.now(), items=[],
        )
    universe = list_universe()
    items = []
    for code, name, _ind in universe:
        try:
            candles = get_candles(code, days=180)
            r = detector(code, name, candles)
            if r and r.score >= min_score:
                items.append(r)
        except Exception:
            continue
    items.sort(key=lambda x: x.score, reverse=True)
    return ScreenerResponse(
        pattern=pattern,
        total=len(items),
        scanned=len(universe),
        scanned_at=datetime.now(),
        items=items[:limit],
    )


@router.get("/")
async def summary():
    """Counts per pattern (uses default thresholds)."""
    universe = list_universe()
    counts: dict[str, int] = {}
    for pattern, detector in PATTERN_DETECTORS.items():
        n = 0
        for code, name, _ in universe:
            candles = get_candles(code, days=180)
            r = detector(code, name, candles)
            if r:
                n += 1
        counts[pattern] = n
    return {"scanned": len(universe), "counts": counts, "scanned_at": datetime.now()}
