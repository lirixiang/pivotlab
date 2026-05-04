"""Support/Resistance level detection.

Combines:
1. Local extrema (scipy argrelextrema on highs/lows)
2. Price clustering (group nearby pivots into level bands)
3. Moving averages (MA20/60 as dynamic levels)

Outputs ranked Level objects with strength (1-5) and touch counts.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

import numpy as np
from scipy.signal import argrelextrema

from ..schemas import Candle, Level


def _moving_average(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return float(np.mean(values[-window:]))


def _cluster_pivots(prices: list[float], tol_pct: float = 0.012) -> list[tuple[float, int]]:
    """Group nearby pivot prices. Returns [(centroid, count), ...] desc by count."""
    if not prices:
        return []
    sorted_p = sorted(prices)
    clusters: list[list[float]] = [[sorted_p[0]]]
    for p in sorted_p[1:]:
        if abs(p - clusters[-1][-1]) / max(clusters[-1][-1], 1e-6) <= tol_pct:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    out = [(float(np.mean(c)), len(c)) for c in clusters]
    out.sort(key=lambda x: x[1], reverse=True)
    return out


def detect_levels(
    candles: list[Candle],
    lookback: int = 120,
    sensitivity: int = 5,
    cluster_tol_pct: float = 0.012,
    max_per_side: int = 3,
    min_touches: int = 2,
) -> list[Level]:
    if not candles:
        return []
    data = candles[-lookback:] if len(candles) > lookback else candles
    highs = np.array([c.high for c in data])
    lows = np.array([c.low for c in data])
    closes = [c.close for c in data]
    last = closes[-1]

    # local extrema indices
    high_idx = argrelextrema(highs, np.greater_equal, order=sensitivity)[0]
    low_idx = argrelextrema(lows, np.less_equal, order=sensitivity)[0]

    high_pivots = [float(highs[i]) for i in high_idx]
    low_pivots = [float(lows[i]) for i in low_idx]

    high_clusters = _cluster_pivots(high_pivots, cluster_tol_pct)
    low_clusters = _cluster_pivots(low_pivots, cluster_tol_pct)

    # Resistance candidates come from swing highs above current price.
    # Support candidates come from swing lows below current price.
    # Allow polarity flip: a broken resistance (high pivot now below price)
    # becomes support; a lost support (low pivot now above price) becomes resistance.
    resistance_pool = (
        [(p, n) for p, n in high_clusters if p > last]
        + [(p, n) for p, n in low_clusters if p > last]
    )
    support_pool = (
        [(p, n) for p, n in low_clusters if p <= last]
        + [(p, n) for p, n in high_clusters if p <= last]
    )

    def _merge(items: list[tuple[float, int]]) -> list[tuple[float, int]]:
        if not items:
            return []
        items = sorted(items, key=lambda x: x[0])
        merged: list[list[float]] = [[items[0][0], float(items[0][1])]]
        for p, n in items[1:]:
            last_p = merged[-1][0]
            if abs(p - last_p) / max(last_p, 1e-6) <= cluster_tol_pct:
                merged[-1][1] += n
                merged[-1][0] = (last_p + p) / 2
            else:
                merged.append([p, float(n)])
        return [(float(p), int(n)) for p, n in merged]

    def _filter(items: list[tuple[float, int]]) -> list[tuple[float, int]]:
        kept = [(p, n) for p, n in items if n >= min_touches]
        if not kept and items:  # don't strip everything: keep top by touches
            kept = sorted(items, key=lambda x: x[1], reverse=True)[:1]
        return kept

    resistances = _filter(_merge(resistance_pool))
    supports = _filter(_merge(support_pool))
    # Closest first: R1 nearest above price, S1 nearest below price.
    resistances = sorted(resistances, key=lambda x: x[0])[:max_per_side]
    supports = sorted(supports, key=lambda x: x[0], reverse=True)[:max_per_side]

    # strength: scaled by touch count (max touches across all)
    all_counts = [n for _, n in (resistances + supports)] or [1]
    max_n = max(all_counts)

    def _strength(n: int) -> int:
        ratio = n / max_n
        return max(1, min(5, int(round(ratio * 5))))

    levels: list[Level] = []
    for i, (p, n) in enumerate(resistances, start=1):
        levels.append(Level(
            label=f"R{i}",
            price=round(p, 2),
            kind="resistance",
            strength=_strength(n),
            touches=n,
            distance_pct=round((p - last) / last * 100, 2),
            note=f"密集成交 · 触及{n}次",
        ))
    for i, (p, n) in enumerate(supports, start=1):
        levels.append(Level(
            label=f"S{i}",
            price=round(p, 2),
            kind="support",
            strength=_strength(n),
            touches=n,
            distance_pct=round((p - last) / last * 100, 2),
            note=f"密集成交 · 触及{n}次",
        ))

    # Add MA20 as dynamic support if below price
    ma20 = _moving_average(closes, 20)
    if ma20 and ma20 < last:
        levels.append(Level(
            label="MA20", price=round(ma20, 2), kind="support",
            strength=3, touches=0,
            distance_pct=round((ma20 - last) / last * 100, 2),
            note="MA20 动态支撑",
        ))
    return levels
