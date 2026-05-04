"""Pattern screener: breakout-pullback and bottom-stabilize."""
from __future__ import annotations

from datetime import datetime
from typing import Iterable

import numpy as np

from ..schemas import Candle, ScreenerItem
from .levels import detect_levels


def _vol_ratio(candles: list[Candle], window: int = 5) -> float:
    if len(candles) < window + 1:
        return 0.0
    recent = candles[-1].volume
    prev = np.mean([c.volume for c in candles[-window - 1:-1]])
    return float(recent / prev) if prev > 0 else 0.0


def _max_drawdown_pct(values: list[float]) -> float:
    if not values:
        return 0.0
    arr = np.array(values)
    return float((arr.max() - arr.min()) / arr.max() * 100)


def detect_breakout_pullback(code: str, name: str, candles: list[Candle]) -> ScreenerItem | None:
    if len(candles) < 60:
        return None
    levels = detect_levels(candles)
    last = candles[-1]
    closes = [c.close for c in candles]
    last_close = last.close

    # Find a resistance that has been breached in last 1-5 days
    triggered_at = None
    breakout_price = None
    for lvl in levels:
        if lvl.kind != "resistance" and lvl.label.startswith("R"):
            continue
    # Look at history: was there a candle in last 10 days that closed > a recent pivot high
    recent = candles[-30:]
    if len(recent) < 20:
        return None
    pivot_high = float(np.max([c.high for c in recent[:-5]]))
    last5 = recent[-5:]
    breakout_idx = None
    for i, c in enumerate(last5):
        if c.close > pivot_high * 1.005:
            breakout_idx = i
            breakout_price = pivot_high
            break
    if breakout_idx is None:
        return None

    # After breakout: pullback (lower volume) and current close stays above breakout
    after = last5[breakout_idx + 1:]
    if len(after) < 1:
        return None
    if last_close < breakout_price * 0.985:
        return None  # already broke down

    breakout_vol = last5[breakout_idx].volume
    pullback_vols = [c.volume for c in after]
    if not pullback_vols:
        return None
    avg_pullback_vol = float(np.mean(pullback_vols))
    if avg_pullback_vol > breakout_vol * 0.85:
        return None  # not a real pullback

    vr = _vol_ratio(candles)

    # score
    breakout_strength = min(30, 18 + (last5[breakout_idx].close / pivot_high - 1) * 1000)
    pullback_quality = min(25, 15 + (1 - avg_pullback_vol / max(breakout_vol, 1)) * 25)
    level_strength = 22  # placeholder
    multi_period = 18
    score = round(breakout_strength + pullback_quality + level_strength + multi_period, 1)

    change_pct = (last.close / candles[-2].close - 1) * 100 if len(candles) >= 2 else 0.0

    return ScreenerItem(
        code=code, name=name, pattern="breakout_pullback",
        score=score, price=round(last_close, 2),
        change_pct=round(change_pct, 2),
        volume_ratio=round(vr, 2),
        breakout_price=round(breakout_price, 2),
        pullback_price=round(min(c.low for c in after), 2),
        distance_to_support_pct=round((last_close - breakout_price) / breakout_price * 100, 2),
        triggers=["放量突破", "缩量回踩"],
    )


def detect_bottom_stabilize(code: str, name: str, candles: list[Candle]) -> ScreenerItem | None:
    if len(candles) < 60:
        return None
    last = candles[-1]
    last_close = last.close
    window = candles[-25:]
    closes = [c.close for c in window]
    drawdown = _max_drawdown_pct(closes)
    if drawdown > 18:
        return None  # too volatile, not a flat base

    # require recent decline before the base
    prior = candles[-60:-25]
    if not prior:
        return None
    prior_high = max(c.high for c in prior)
    if last_close > prior_high * 0.95:
        return None  # not a bottom; price still near prior high

    # bullish reversal in last 3 days
    recent3 = candles[-3:]
    bullish = sum(1 for c in recent3 if c.close > c.open)
    if bullish < 2:
        return None

    # third day should not make new low
    base_low = min(c.low for c in window[:-3])
    if any(c.low < base_low for c in recent3):
        return None

    vr = _vol_ratio(candles)
    if vr < 1.05:
        return None

    score = round(40 + bullish * 8 + min(20, vr * 5) + (20 - min(20, drawdown)), 1)
    change_pct = (last.close / candles[-2].close - 1) * 100 if len(candles) >= 2 else 0.0

    return ScreenerItem(
        code=code, name=name, pattern="bottom_stabilize",
        score=score, price=round(last_close, 2),
        change_pct=round(change_pct, 2),
        volume_ratio=round(vr, 2),
        breakout_price=None,
        pullback_price=round(base_low, 2),
        distance_to_support_pct=round((last_close - base_low) / base_low * 100, 2),
        triggers=["下跌企稳", "看涨吞没" if bullish == 3 else "底部止跌"],
    )


PATTERN_DETECTORS = {
    "breakout_pullback": detect_breakout_pullback,
    "bottom_stabilize": detect_bottom_stabilize,
}
