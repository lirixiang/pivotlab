"""ZigZag labeler — find optimal buy/sell points in historical price data.

Methods:
  1. zigzag()       — classic ZigZag: mark significant turning points (peaks/troughs)
  2. dp_optimal()   — dynamic programming: find theoretically optimal trades
  3. label_candles() — combine both to produce per-bar labels: buy / sell / hold
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from ..schemas import Candle

logger = logging.getLogger(__name__)


@dataclass
class LabeledPoint:
    idx: int
    date: str
    price: float
    label: str   # "buy" | "sell"


def zigzag(candles: list[Candle], pct_threshold: float = 5.0) -> list[LabeledPoint]:
    """Classic ZigZag indicator — identify significant turning points.

    A new swing is confirmed when price moves >= pct_threshold% from the last pivot.
    Returns alternating buy (trough) and sell (peak) points.
    """
    if len(candles) < 5:
        return []

    highs = np.array([c.high for c in candles])
    lows = np.array([c.low for c in candles])

    pivots: list[LabeledPoint] = []
    direction = 0  # 0=undecided, 1=looking for peak, -1=looking for trough
    last_high_idx = 0
    last_low_idx = 0
    last_high = highs[0]
    last_low = lows[0]

    for i in range(1, len(candles)):
        if direction == 0:
            # Determine initial direction
            if highs[i] >= last_high * (1 + pct_threshold / 100):
                direction = 1  # up — looking for peak
                last_high = highs[i]
                last_high_idx = i
            elif lows[i] <= last_low * (1 - pct_threshold / 100):
                direction = -1  # down — looking for trough
                last_low = lows[i]
                last_low_idx = i

        elif direction == 1:  # uptrend, looking for peak
            if highs[i] > last_high:
                last_high = highs[i]
                last_high_idx = i
            elif lows[i] <= last_high * (1 - pct_threshold / 100):
                # Confirmed peak — mark sell
                pivots.append(LabeledPoint(
                    idx=last_high_idx,
                    date=candles[last_high_idx].date,
                    price=candles[last_high_idx].high,
                    label="sell",
                ))
                direction = -1
                last_low = lows[i]
                last_low_idx = i

        elif direction == -1:  # downtrend, looking for trough
            if lows[i] < last_low:
                last_low = lows[i]
                last_low_idx = i
            elif highs[i] >= last_low * (1 + pct_threshold / 100):
                # Confirmed trough — mark buy
                pivots.append(LabeledPoint(
                    idx=last_low_idx,
                    date=candles[last_low_idx].date,
                    price=candles[last_low_idx].low,
                    label="buy",
                ))
                direction = 1
                last_high = highs[i]
                last_high_idx = i

    return pivots


def dp_optimal(
    candles: list[Candle],
    min_profit_pct: float = 2.0,
    max_trades: int = 50,
) -> list[LabeledPoint]:
    """Dynamic programming — find theoretically optimal buy/sell sequence.

    Finds trades that each yield >= min_profit_pct, maximising total return.
    Uses greedy approach on smoothed data to avoid overfitting to noise.
    """
    n = len(candles)
    if n < 10:
        return []

    # Smooth prices with 3-bar average to reduce noise
    closes = np.array([c.close for c in candles])
    smooth = np.convolve(closes, np.ones(3) / 3, mode="same")

    # Find local minima and maxima on smoothed data
    points: list[LabeledPoint] = []
    i = 1
    in_trade = False

    while i < n - 1:
        if not in_trade:
            # Look for local minimum (buy point)
            if smooth[i] <= smooth[i - 1] and smooth[i] <= smooth[i + 1]:
                # Check there's enough upside ahead
                future_max = float(np.max(smooth[i + 1:min(i + 60, n)]))
                if (future_max / smooth[i] - 1) * 100 >= min_profit_pct:
                    points.append(LabeledPoint(
                        idx=i, date=candles[i].date,
                        price=candles[i].close, label="buy",
                    ))
                    in_trade = True
        else:
            # Look for local maximum (sell point)
            if smooth[i] >= smooth[i - 1] and smooth[i] >= smooth[i + 1]:
                buy_price = points[-1].price
                if (candles[i].close / buy_price - 1) * 100 >= min_profit_pct:
                    points.append(LabeledPoint(
                        idx=i, date=candles[i].date,
                        price=candles[i].close, label="sell",
                    ))
                    in_trade = False
                    if len(points) >= max_trades * 2:
                        break
        i += 1

    # Remove trailing buy without sell
    if points and points[-1].label == "buy":
        points.pop()

    return points


def label_candles(
    candles: list[Candle],
    method: str = "zigzag",
    pct_threshold: float = 5.0,
    window: int = 2,
) -> list[int]:
    """Produce per-bar labels for the full candle array.

    Returns list of integers: 0=hold, 1=buy, 2=sell
    window: how many bars around a pivot to also mark (smoothing)
    """
    if method == "dp":
        pivots = dp_optimal(candles, min_profit_pct=pct_threshold)
    else:
        pivots = zigzag(candles, pct_threshold=pct_threshold)

    labels = [0] * len(candles)  # all hold by default

    for p in pivots:
        lbl = 1 if p.label == "buy" else 2
        # Mark the pivot and nearby bars
        for offset in range(-window, window + 1):
            j = p.idx + offset
            if 0 <= j < len(candles):
                # Pivot bar itself gets priority; nearby only if still hold
                if j == p.idx:
                    labels[j] = lbl
                elif labels[j] == 0:
                    labels[j] = lbl

    return labels


def get_labeled_points(
    candles: list[Candle],
    method: str = "zigzag",
    pct_threshold: float = 5.0,
) -> list[dict]:
    """Return labeled pivot points as dicts (for API response)."""
    if method == "dp":
        pivots = dp_optimal(candles, min_profit_pct=pct_threshold)
    else:
        pivots = zigzag(candles, pct_threshold=pct_threshold)

    return [
        {"idx": p.idx, "date": p.date, "price": round(p.price, 2), "label": p.label}
        for p in pivots
    ]
