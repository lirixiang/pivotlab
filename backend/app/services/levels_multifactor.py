"""Multi-factor support/resistance detection engine (v2).

Scoring model: per-touch accumulation with time decay, volume confirmation,
and additive bonuses.

Each touch of a level is individually scored:
    touch_score += quality(i) * exp(-lambda * age_i) * vol_weight(i)

Final score:
    base  = touch_score*3 + reaction*2 + dwell*0.4 + overlap*0.8
          + reversal_bonus + round_bonus + structure_bonus
    adjusted = base * trend_coeff * confluence_mult
    final    = adjusted * false_break_factor (0.7^n)
    normalised = final / max_in_group * 100

Factors matching stock-sr-platform:
  - Weekly confluence (×2.0 when weekly level aligns)
  - Structure candidates (prior high/low, range edge, breakout-retest)
  - Multi-window overlap (pivot detected by 3/5/8 windows simultaneously)
  - Decisive-break validation (filters broken levels)
  - Dynamic tolerance (ATR-based, 0.6%–2%)
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Literal

import numpy as np

from ..schemas import Candle, Level

# ── Tuning constants ──
_DECAY_LAMBDA = 0.03           # per-bar decay ~ half-life 23 bars
_REVERSAL_BONUS = 8.0          # confirmed S<->R flip
_FALSE_BREAK_PENALTY = 0.7     # multiply per false breakout
_ROUND_NUMBER_BONUS = 1.5      # round-number price bonus
_DWELL_WEIGHT = 0.4            # per bar price sits in zone
_MA_RESONANCE_BONUS = 2.0      # convergence with key MAs
_STRUCTURE_PIVOT_BONUS = 3.0   # prior high/low
_STRUCTURE_RANGE_BONUS = 2.0   # range edge
_RETEST_CONFIRM_BONUS = 6.0    # breakout-retest confirmation
_CONFLUENCE_MULT = 2.0         # weekly confluence multiplier
_MIN_SCORE_THRESHOLD = 20      # noise filter
_OVERLAP_WEIGHT = 0.8          # per-window overlap bonus

# ── Default factor weights (kept for the /factors API) ──
DEFAULT_WEIGHTS: dict[str, float] = {
    "touch_count":     1.0,
    "volume_weight":   1.2,
    "recency":         1.0,
    "touch_quality":   0.8,
    "dwell_time":      0.6,
    "trend_alignment": 0.7,
    "ma_resonance":    0.9,
    "role_reversal":   0.8,
    "false_breakout":  0.5,
    "zone_tightness":  0.4,
}


# ────────────────────────────────────────────────────────────
#  Pivot detection — multi-scale with overlap counting
# ────────────────────────────────────────────────────────────

def _find_swing_points(
    candles: list[Candle], window: int = 5,
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Pure comparison swing detection at a given window."""
    highs: list[tuple[int, float]] = []
    lows: list[tuple[int, float]] = []
    for i in range(window, len(candles) - window):
        h = candles[i].high
        lo = candles[i].low
        if all(h >= candles[j].high for j in range(i - window, i + window + 1) if j != i):
            highs.append((i, h))
        if all(lo <= candles[j].low for j in range(i - window, i + window + 1) if j != i):
            lows.append((i, lo))
    return highs, lows


def _detect_pivots_multiscale(
    candles: list[Candle],
) -> tuple[dict[int, float], dict[int, float], dict[int, int], dict[int, int]]:
    """Detect swing highs/lows at windows 3, 5, 8.
    Returns (seen_h, seen_l, overlap_h, overlap_l) where overlap counts
    how many window sizes detected each pivot bar."""
    seen_h: dict[int, float] = {}
    seen_l: dict[int, float] = {}
    overlap_h: dict[int, int] = {}
    overlap_l: dict[int, int] = {}
    for win in (3, 5, 8):
        highs, lows = _find_swing_points(candles, window=win)
        for idx, price in highs:
            if idx not in seen_h or price >= seen_h[idx]:
                seen_h[idx] = price
            overlap_h[idx] = overlap_h.get(idx, 0) + 1
        for idx, price in lows:
            if idx not in seen_l or price <= seen_l[idx]:
                seen_l[idx] = price
            overlap_l[idx] = overlap_l.get(idx, 0) + 1
    return seen_h, seen_l, overlap_h, overlap_l


# ────────────────────────────────────────────────────────────
#  Weekly candle resampling + confluence
# ────────────────────────────────────────────────────────────

def _resample_to_weekly(candles: list[Candle]) -> list[Candle]:
    """Resample daily candles to weekly for higher-timeframe confluence."""
    if not candles:
        return []
    result: list[Candle] = []
    bucket: list[Candle] = []

    def flush():
        if not bucket:
            return
        result.append(Candle(
            date=bucket[-1].date,
            open=bucket[0].open,
            high=max(c.high for c in bucket),
            low=min(c.low for c in bucket),
            close=bucket[-1].close,
            volume=sum(c.volume for c in bucket),
        ))

    for c in candles:
        try:
            d = datetime.strptime(c.date[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if bucket:
            try:
                prev_d = datetime.strptime(bucket[-1].date[:10], "%Y-%m-%d").date()
                if d.isocalendar()[1] != prev_d.isocalendar()[1] or d.year != prev_d.year:
                    flush()
                    bucket = []
            except ValueError:
                pass
        bucket.append(c)
    flush()
    return result


def _higher_timeframe_levels(weekly: list[Candle], current: float) -> dict[str, list[float]]:
    """Extract swing pivots from weekly candles for confluence check."""
    if not weekly or len(weekly) < 12:
        return {"resistance": [], "support": []}
    highs, lows = _find_swing_points(weekly, window=3)
    all_prices = [(p, 1) for _, p in highs] + [(p, 1) for _, p in lows]
    grouped = _group_levels(all_prices, tol=0.015)
    return {
        "resistance": [price for price, _ in grouped if price > current],
        "support": [price for price, _ in grouped if price < current],
    }


# ────────────────────────────────────────────────────────────
#  Structure candidates (prior highs/lows, range edges)
# ────────────────────────────────────────────────────────────

def _collect_structure_candidates(
    candles: list[Candle], current: float, tolerance: float,
) -> dict[str, list[dict]]:
    """Identify structural price levels with reason labels."""
    recent = candles[-160:] if len(candles) > 160 else candles
    if len(recent) < 10:
        return {"support": [], "resistance": []}

    swing_highs, swing_lows = _find_swing_points(recent, window=4)
    candidates: dict[str, list[dict]] = {"support": [], "resistance": []}

    def _push(lt: str, price: float, base_count: int, bonus: float, reason: str):
        candidates[lt].append({
            "price": price, "base_count": base_count,
            "bonus": bonus, "reason": reason,
        })

    for index, price in swing_lows:
        age_weight = math.exp(-_DECAY_LAMBDA * (len(recent) - index - 1))
        if price < current * (1 - tolerance * 0.25):
            if not _is_decisively_broken(recent, index, price, "support", tolerance):
                _push("support", price, 3, _STRUCTURE_PIVOT_BONUS + age_weight, "前低支撑")
        if price > current * (1 + tolerance * 0.25):
            if _detect_role_reversal(recent, index, price, "resistance", tolerance):
                _push("resistance", price, 4, _RETEST_CONFIRM_BONUS + age_weight, "跌破后反抽确认")

    for index, price in swing_highs:
        age_weight = math.exp(-_DECAY_LAMBDA * (len(recent) - index - 1))
        if price > current * (1 + tolerance * 0.25):
            if not _is_decisively_broken(recent, index, price, "resistance", tolerance):
                _push("resistance", price, 3, _STRUCTURE_PIVOT_BONUS + age_weight, "前高压力")
        if price < current * (1 - tolerance * 0.25):
            if _detect_role_reversal(recent, index, price, "support", tolerance):
                _push("support", price, 4, _RETEST_CONFIRM_BONUS + age_weight, "突破回踩确认")

    range_lookback = recent[-30:]
    range_high = max(c.high for c in range_lookback)
    range_low = min(c.low for c in range_lookback)
    if range_high > current:
        _push("resistance", range_high, 2, _STRUCTURE_RANGE_BONUS, "区间上沿")
    if range_low < current:
        _push("support", range_low, 2, _STRUCTURE_RANGE_BONUS, "区间下沿")

    return candidates


# ────────────────────────────────────────────────────────────
#  Dynamic tolerance (ATR-based)
# ────────────────────────────────────────────────────────────

def _dynamic_tolerance(candles: list[Candle]) -> float:
    recent = candles[-30:]
    avg_range = sum((c.high - c.low) / max(c.close, 1) for c in recent) / max(len(recent), 1)
    return min(max(avg_range * 0.6, 0.006), 0.02)


# ────────────────────────────────────────────────────────────
#  Break / role-reversal validation
# ────────────────────────────────────────────────────────────

def _is_decisively_broken(
    candles: list[Candle], start_index: int, level_price: float,
    level_type: Literal["support", "resistance"], tolerance: float,
) -> bool:
    future = candles[start_index + 1:]
    if not future:
        return False
    avg_vol = sum(c.volume for c in candles[-60:]) / max(min(len(candles), 60), 1) or 1
    consecutive = 0
    broke_with_volume = False
    for idx, c in enumerate(future):
        if level_type == "support":
            is_beyond = c.close < level_price * (1 - tolerance)
        else:
            is_beyond = c.close > level_price * (1 + tolerance)
        if is_beyond:
            if consecutive == 0:
                broke_with_volume = c.volume > avg_vol * 0.8
            consecutive += 1
            if consecutive >= 2 and broke_with_volume:
                reclaimed = False
                for rc in future[idx + 1: idx + 8]:
                    if level_type == "support" and rc.close > level_price * (1 - tolerance * 0.3):
                        reclaimed = True
                        break
                    if level_type == "resistance" and rc.close < level_price * (1 + tolerance * 0.3):
                        reclaimed = True
                        break
                if not reclaimed:
                    return True
                consecutive = 0
                broke_with_volume = False
        else:
            consecutive = 0
            broke_with_volume = False
    return False


def _detect_role_reversal(
    candles: list[Candle], start_index: int, level_price: float,
    target_type: Literal["support", "resistance"], tolerance: float,
) -> bool:
    future = candles[start_index + 1:]
    if len(future) < 4:
        return False
    breakout_at: int | None = None
    for idx, c in enumerate(future):
        if target_type == "support" and c.close > level_price * (1 + tolerance * 0.6):
            breakout_at = idx
            break
        if target_type == "resistance" and c.close < level_price * (1 - tolerance * 0.6):
            breakout_at = idx
            break
    if breakout_at is None:
        return False
    for c in future[breakout_at + 1:]:
        if target_type == "support":
            if abs(c.low - level_price) / max(level_price, 1) <= tolerance and c.close >= level_price:
                return True
        else:
            if abs(c.high - level_price) / max(level_price, 1) <= tolerance and c.close <= level_price:
                return True
    return False


# ────────────────────────────────────────────────────────────
#  False breakout counter
# ────────────────────────────────────────────────────────────

def _count_false_breaks(
    candles: list[Candle], level_price: float,
    level_type: Literal["support", "resistance"], tolerance: float,
) -> int:
    false_breaks = 0
    i = 0
    while i < len(candles) - 3:
        c = candles[i]
        broke = False
        if level_type == "support" and c.close < level_price * (1 - tolerance * 0.8):
            broke = True
        elif level_type == "resistance" and c.close > level_price * (1 + tolerance * 0.8):
            broke = True
        if broke:
            for j in range(1, min(4, len(candles) - i)):
                nxt = candles[i + j]
                if level_type == "support" and nxt.close > level_price * (1 - tolerance * 0.3):
                    false_breaks += 1
                    break
                if level_type == "resistance" and nxt.close < level_price * (1 + tolerance * 0.3):
                    false_breaks += 1
                    break
            i += 4
        else:
            i += 1
    return false_breaks


# ────────────────────────────────────────────────────────────
#  Trend coefficient
# ────────────────────────────────────────────────────────────

def _trend_coefficient(
    candles: list[Candle], level_type: Literal["support", "resistance"],
) -> float:
    if len(candles) < 20:
        return 1.0
    ma20 = sum(c.close for c in candles[-20:]) / 20
    ma60 = sum(c.close for c in candles[-min(60, len(candles)):]) / min(60, len(candles))
    uptrend = ma20 > ma60
    if level_type == "support" and uptrend:
        return 1.3
    if level_type == "resistance" and not uptrend:
        return 1.3
    if level_type == "support" and not uptrend:
        return 0.8
    if level_type == "resistance" and uptrend:
        return 0.8
    return 1.0


# ────────────────────────────────────────────────────────────
#  MA resonance check
# ────────────────────────────────────────────────────────────

def _ma_resonance_bonus(candles: list[Candle], level_price: float) -> float:
    closes = [c.close for c in candles]
    bonus = 0.0
    for w in [20, 60, 120, 250]:
        if len(closes) >= w:
            ma = float(np.mean(closes[-w:]))
            if abs(ma - level_price) / max(level_price, 1e-6) < 0.015:
                bonus += _MA_RESONANCE_BONUS
    return bonus


# ────────────────────────────────────────────────────────────
#  Group levels (use most-touched price, not average)
# ────────────────────────────────────────────────────────────

def _group_levels(
    raw: list[tuple[float, int]], tol: float,
) -> list[tuple[float, int]]:
    if not raw:
        return []
    sorted_raw = sorted(raw, key=lambda x: x[0])
    groups: list[list[tuple[float, int]]] = []
    for price, cnt in sorted_raw:
        if groups and abs(price - groups[-1][-1][0]) / max(groups[-1][-1][0], 1) < tol:
            groups[-1].append((price, cnt))
        else:
            groups.append([(price, cnt)])
    result = []
    for g in groups:
        best_price = max(g, key=lambda x: x[1])[0]
        total_cnt = sum(c for _, c in g)
        result.append((best_price, total_cnt))
    return result


# ────────────────────────────────────────────────────────────
#  Per-touch accumulation scoring (with all factors)
# ────────────────────────────────────────────────────────────

def _score_level(
    price: float,
    base_count: int,
    candles: list[Candle],
    level_type: Literal["support", "resistance"],
    tolerance: float,
    multi_window_overlap: int = 1,
    higher_levels: list[float] | None = None,
    structure_candidates: list[dict] | None = None,
) -> dict | None:
    """Score a single candidate level using per-touch accumulation.

    Returns a rich dict with full score breakdown, or None if the level
    doesn't meet minimum viability.
    """
    total_bars = len(candles)
    avg_volume = sum(c.volume for c in candles[-60:]) / max(min(len(candles), 60), 1) or 1
    trend_coeff = _trend_coefficient(candles, level_type)
    higher_levels = higher_levels or []
    structure_candidates = structure_candidates or []

    touch_score = 0.0
    touch_count = 0
    reaction_count = 0
    vol_confirm_count = 0
    dwell_bars = 0

    for i, c in enumerate(candles):
        wick = c.low if level_type == "support" else c.high
        close = c.close
        age = total_bars - i - 1
        decay = math.exp(-_DECAY_LAMBDA * age)

        wick_dist = abs(wick - price) / max(price, 1)
        close_dist = abs(close - price) / max(price, 1)
        touched = wick_dist <= tolerance or close_dist <= tolerance * 0.6

        if not touched:
            if abs((c.high + c.low) / 2 - price) / max(price, 1) <= tolerance * 2:
                dwell_bars += 1
            continue

        # Touch quality
        quality = 1.0
        body = abs(close - c.open)
        if level_type == "support":
            lower_wick = min(c.open, close) - c.low
            if lower_wick > body * 0.5:
                quality = 1.5
            if close > c.open:
                quality += 0.3
        else:
            upper_wick = c.high - max(c.open, close)
            if upper_wick > body * 0.5:
                quality = 1.5
            if close < c.open:
                quality += 0.3

        # Volume weight
        vol_weight = 1.0
        if avg_volume > 0:
            vol_ratio = c.volume / avg_volume
            if vol_ratio >= 1.5:
                vol_weight = 1.8
                vol_confirm_count += 1
            elif vol_ratio >= 1.0:
                vol_weight = 1.3
            elif vol_ratio < 0.5:
                vol_weight = 0.6

        touch_score += quality * decay * vol_weight
        touch_count += 1

        # Reaction check
        if i < total_bars - 1:
            nc = candles[i + 1]
            if level_type == "support" and nc.close > price * (1 + tolerance * 0.5):
                reaction_count += 1
            elif level_type == "resistance" and nc.close < price * (1 - tolerance * 0.5):
                reaction_count += 1

    # ── Structure bonus ──
    matched_structure = [
        cand for cand in structure_candidates
        if abs(price - float(cand["price"])) / max(abs(float(cand["price"])), 1) <= max(tolerance, 0.012)
    ]
    structure_bonus = min(
        sum(float(cand.get("bonus", 0) or 0) for cand in matched_structure),
        _RETEST_CONFIRM_BONUS + _STRUCTURE_PIVOT_BONUS + _STRUCTURE_RANGE_BONUS,
    )
    structure_reasons = []
    seen_reasons: set[str] = set()
    for cand in matched_structure:
        r = str(cand.get("reason", "")).strip()
        if r and r not in seen_reasons:
            seen_reasons.add(r)
            structure_reasons.append(r)

    # ── False breakout penalty ──
    false_breaks = _count_false_breaks(candles, price, level_type, tolerance)

    # ── Role reversal bonus ──
    reversal = _detect_role_reversal(
        candles, 0, price,
        "support" if level_type == "resistance" else "resistance",
        tolerance,
    )
    reversal_bonus = _REVERSAL_BONUS if reversal else 0.0

    # ── Round number bonus ──
    round_bonus = 0.0
    if price > 0:
        remainder = price % 10
        if remainder < 0.5 or remainder > 9.5:
            round_bonus = _ROUND_NUMBER_BONUS
        elif price % 5 < 0.3 or price % 5 > 4.7:
            round_bonus = _ROUND_NUMBER_BONUS * 0.5

    # ── MA resonance ──
    ma_bonus = _ma_resonance_bonus(candles, price)

    # ── Weekly confluence ──
    weekly_confluence = any(
        abs(price - hl) / max(hl, 1) <= max(tolerance, 0.012)
        for hl in higher_levels
    )
    confluence_mult = _CONFLUENCE_MULT if weekly_confluence else 1.0

    # ── Minimum viability ──
    if touch_count < 2 and base_count < 2 and not weekly_confluence:
        return None

    # ── Composite score ──
    # base = touch_score*3 + reaction*2 + dwell*0.4 + overlap*0.8
    #      + reversal_bonus + round_bonus + structure_bonus
    base_score = (
        touch_score * 3.0
        + reaction_count * 2.0
        + dwell_bars * _DWELL_WEIGHT
        + multi_window_overlap * _OVERLAP_WEIGHT
        + reversal_bonus
        + round_bonus
        + structure_bonus
    )
    trend_adjusted = base_score * trend_coeff
    confluence_adjusted = trend_adjusted * confluence_mult
    false_break_factor = _FALSE_BREAK_PENALTY ** false_breaks
    raw_score = confluence_adjusted * false_break_factor

    # ── Build reasons ──
    reasons = list(structure_reasons)
    if weekly_confluence:
        reasons.append("周线共振")
    if reversal:
        reasons.append("角色转换确认")
    if ma_bonus > 0:
        reasons.append("均线共振")
    if touch_count >= 3:
        reasons.append("多次测试未破")
    if reaction_count >= 2:
        reasons.append("触线后反应明确")
    if vol_confirm_count >= 2:
        reasons.append("放量确认")
    if dwell_bars >= 10:
        reasons.append("横盘沉淀")
    if false_breaks >= 2:
        reasons.append(f"假突破x{false_breaks}")

    return {
        "price": price,
        "touch_count": max(base_count, touch_count),
        "raw_score": raw_score,
        "weekly_confluence": weekly_confluence,
        "reasons": reasons,
        "score_details": {
            "touch_score": round(touch_score, 2),
            "touch_count": touch_count,
            "reaction_count": reaction_count,
            "dwell_bars": dwell_bars,
            "base_count": base_count,
            "vol_confirm_count": vol_confirm_count,
            "multi_window_overlap": multi_window_overlap,
            "structure_bonus": round(structure_bonus, 2),
            "reversal_bonus": round(reversal_bonus, 1),
            "round_bonus": round(round_bonus, 1),
            "ma_bonus": round(ma_bonus, 1),
            "trend_coeff": round(trend_coeff, 2),
            "weekly_confluence": weekly_confluence,
            "confluence_mult": round(confluence_mult, 1),
            "false_breaks": false_breaks,
            "false_break_factor": round(false_break_factor, 4),
            "base_score": round(base_score, 2),
            "trend_adjusted": round(trend_adjusted, 2),
            "confluence_adjusted": round(confluence_adjusted, 2),
            "raw_score": round(raw_score, 2),
        },
    }


# ────────────────────────────────────────────────────────────
#  Strength label
# ────────────────────────────────────────────────────────────

def _strength_label(score: float) -> str:
    if score >= 70:
        return "强"
    if score >= 40:
        return "中"
    return "弱"


def _strength_num(score: float) -> int:
    if score >= 80:
        return 5
    if score >= 60:
        return 4
    if score >= 40:
        return 3
    if score >= 25:
        return 2
    return 1


# ────────────────────────────────────────────────────────────
#  Main entry point
# ────────────────────────────────────────────────────────────

def detect_levels_multifactor(
    candles: list[Candle],
    lookback: int = 120,
    sensitivity: int = 5,
    cluster_tol_pct: float = 0.012,
    max_per_side: int = 4,
    min_touches: int = 2,
    factor_weights: dict[str, float] | None = None,
    min_score: float | None = None,
) -> list[Level]:
    """Detect S/R levels using per-touch accumulation scoring (v2).

    Returns Level objects with:
      - strength (1-5)
      - score (0-100, normalised)
      - factors dict with full score_details breakdown
      - note with human-readable reasons
    """
    if not candles:
        return []

    data = candles[-lookback:] if len(candles) > lookback else candles
    last_price = data[-1].close
    tolerance = _dynamic_tolerance(data)

    # 1. Multi-scale pivot detection with overlap counting
    seen_h, seen_l, overlap_h, overlap_l = _detect_pivots_multiscale(data)

    # 2. Weekly confluence: resample daily -> weekly, extract pivots
    weekly = _resample_to_weekly(candles)
    higher_levels = _higher_timeframe_levels(weekly, last_price)

    # 3. Structure candidates
    structure = _collect_structure_candidates(data, last_price, tolerance)

    # 4. Build candidate levels with break validation
    res_raw: list[tuple[float, int, int]] = []  # (price, base_count, overlap)
    sup_raw: list[tuple[float, int, int]] = []

    for idx, price in seen_h.items():
        ovlp = overlap_h.get(idx, 1)
        if price > last_price * (1 + tolerance * 0.15):
            if not _is_decisively_broken(data, idx, price, "resistance", tolerance):
                res_raw.append((price, 1, ovlp))
        elif price < last_price * (1 - tolerance * 0.15):
            if _detect_role_reversal(data, idx, price, "support", tolerance):
                sup_raw.append((price, 2, ovlp))

    for idx, price in seen_l.items():
        ovlp = overlap_l.get(idx, 1)
        if price < last_price * (1 - tolerance * 0.15):
            if not _is_decisively_broken(data, idx, price, "support", tolerance):
                sup_raw.append((price, 1, ovlp))
        elif price > last_price * (1 + tolerance * 0.15):
            if _detect_role_reversal(data, idx, price, "resistance", tolerance):
                res_raw.append((price, 2, ovlp))

    # Add structure candidates
    for cand in structure["resistance"]:
        res_raw.append((float(cand["price"]), int(cand["base_count"]), 1))
    for cand in structure["support"]:
        sup_raw.append((float(cand["price"]), int(cand["base_count"]), 1))

    # Recent range edges
    range_lookback = data[-20:]
    range_high = max(c.high for c in range_lookback)
    range_low = min(c.low for c in range_lookback)
    if range_high > last_price * (1 + tolerance * 0.15):
        res_raw.append((range_high, 1, 1))
    if range_low < last_price * (1 - tolerance * 0.15):
        sup_raw.append((range_low, 1, 1))

    # 5. Group nearby levels (keep max overlap per group)
    def _group_with_overlap(
        raw: list[tuple[float, int, int]], tol: float,
    ) -> list[tuple[float, int, int]]:
        if not raw:
            return []
        sorted_raw = sorted(raw, key=lambda x: x[0])
        groups: list[list[tuple[float, int, int]]] = []
        for price, cnt, ovlp in sorted_raw:
            if groups and abs(price - groups[-1][-1][0]) / max(groups[-1][-1][0], 1) < tol:
                groups[-1].append((price, cnt, ovlp))
            else:
                groups.append([(price, cnt, ovlp)])
        result = []
        for g in groups:
            best_price = max(g, key=lambda x: x[1])[0]
            total_cnt = sum(c for _, c, _ in g)
            max_ovlp = max(o for _, _, o in g)
            result.append((best_price, total_cnt, max_ovlp))
        return result

    res_grouped = _group_with_overlap(res_raw, tol=tolerance)
    sup_grouped = _group_with_overlap(sup_raw, tol=tolerance)

    # 6. Score each level with all factors
    res_scored: list[dict] = []
    for price, cnt, ovlp in res_grouped:
        result = _score_level(
            price, cnt, data, "resistance", tolerance,
            multi_window_overlap=ovlp,
            higher_levels=higher_levels["resistance"],
            structure_candidates=structure["resistance"],
        )
        if result:
            res_scored.append(result)

    sup_scored: list[dict] = []
    for price, cnt, ovlp in sup_grouped:
        result = _score_level(
            price, cnt, data, "support", tolerance,
            multi_window_overlap=ovlp,
            higher_levels=higher_levels["support"],
            structure_candidates=structure["support"],
        )
        if result:
            sup_scored.append(result)

    # 7. Normalise scores to 0-100 per side
    for scored_list in [res_scored, sup_scored]:
        if not scored_list:
            continue
        max_raw = max(item["raw_score"] for item in scored_list) or 1
        for item in scored_list:
            item["score"] = round(item["raw_score"] / max_raw * 100, 1)
            item["score_details"]["max_raw"] = round(max_raw, 2)
            item["score_details"]["normalized_score"] = item["score"]

    # 8. Filter by threshold, keep at least 1
    threshold = min_score if min_score is not None else _MIN_SCORE_THRESHOLD
    def _filter_and_sort(scored: list[dict], ascending: bool) -> list[dict]:
        qualified = [item for item in scored if item["score"] >= threshold]
        if not qualified and scored:
            qualified = sorted(scored, key=lambda x: -x["raw_score"])[:1]
        return sorted(qualified, key=lambda x: x["price"], reverse=not ascending)

    res_final = _filter_and_sort(res_scored, ascending=True)
    sup_final = _filter_and_sort(sup_scored, ascending=False)

    # 9. Build Level objects
    levels: list[Level] = []

    for i, item in enumerate(res_final, 1):
        strength_str = _strength_label(item["score"])
        note_parts = [strength_str]
        if item.get("weekly_confluence"):
            note_parts.append("周")
        note_parts.extend(item["reasons"])
        note = " · ".join(note_parts)
        levels.append(Level(
            label=f"R{i}",
            price=round(item["price"], 2),
            kind="resistance",
            strength=_strength_num(item["score"]),
            touches=item["touch_count"],
            distance_pct=round((item["price"] - last_price) / last_price * 100, 2),
            note=note,
            score=item["score"],
            factors=item["score_details"],
            reasons=item["reasons"],
        ))

    for i, item in enumerate(sup_final, 1):
        strength_str = _strength_label(item["score"])
        note_parts = [strength_str]
        if item.get("weekly_confluence"):
            note_parts.append("周")
        note_parts.extend(item["reasons"])
        note = " · ".join(note_parts)
        levels.append(Level(
            label=f"S{i}",
            price=round(item["price"], 2),
            kind="support",
            strength=_strength_num(item["score"]),
            touches=item["touch_count"],
            distance_pct=round((item["price"] - last_price) / last_price * 100, 2),
            note=note,
            score=item["score"],
            factors=item["score_details"],
            reasons=item["reasons"],
        ))

    # 10. Add MA dynamic levels (always, toggle controlled by frontend)
    closes = [c.close for c in data]
    for w, label in [(20, "MA20"), (60, "MA60")]:
        if len(closes) >= w:
            ma = float(np.mean(closes[-w:]))
            kind = "support" if ma < last_price else "resistance"
            dist = round((ma - last_price) / last_price * 100, 2)
            levels.append(Level(
                label=label,
                price=round(ma, 2),
                kind=kind,
                strength=3,
                touches=0,
                distance_pct=dist,
                note=f"{label} 动态{'支撑' if kind == 'support' else '压力'}",
                score=50.0,
                factors={},
                reasons=[f"{label}动态位"],
            ))

    return levels


# ────────────────────────────────────────────────────────────
#  Factor metadata API (kept for frontend config UI)
# ────────────────────────────────────────────────────────────

FACTOR_REGISTRY: dict[str, str] = {
    "touch_score":       "触碰累积 - 每次触碰独立评分并累加",
    "reaction_count":    "反应确认 - 触碰后价格明确反弹/回落",
    "dwell_bars":        "停留时间 - 横盘越久筹码交换越充分",
    "vol_confirm":       "量能确认 - 放量触碰权重更高",
    "multi_window":      "多窗口重合 - 被3/5/8多个窗口同时识别",
    "structure_bonus":   "结构加分 - 前高/前低/区间边沿/回踩确认",
    "trend_coeff":       "趋势系数 - 顺势位加强,逆势位削弱",
    "weekly_confluence": "周线共振 - 与周线级别重合则x2.0",
    "ma_resonance":      "均线共振 - 与MA20/60/120/250重合则增强",
    "role_reversal":     "角色转换 - 突破回踩确认后加分",
    "false_breakout":    "假突破扣分 - 每次假突破x0.7惩罚",
    "round_number":      "整数关口 - 10/50/100整数位小幅加分",
    "break_filter":      "突破过滤 - 已被有效突破的级别自动移除",
}


def get_available_factors() -> list[dict]:
    """Return metadata about scoring factors for frontend display."""
    return [
        {"key": name, "label": desc, "default_weight": 1.0}
        for name, desc in FACTOR_REGISTRY.items()
    ]


# ── Decision score ──

_DECISION_LABELS = [
    (80, "强买"),
    (60, "偏多"),
    (40, "中性"),
    (20, "偏空"),
    (0, "回避"),
]


def compute_decision_score(
    candles: list[Candle],
    levels: list[Level],
    current_price: float,
) -> tuple[float, str]:
    """Compute a 0-100 decision score for a stock based on S/R context.

    Factors:
      1. Support proximity – closer to strong support = higher score
      2. Risk/reward ratio – R1 distance vs S1 distance
      3. Trend alignment – price vs MA20
      4. Support depth – multiple supports below
      5. Resistance pressure – penalise if near strong resistance

    Returns (score, label).
    """
    supports = sorted(
        [l for l in levels if l.kind == "support" and l.price < current_price],
        key=lambda l: current_price - l.price,
    )
    resistances = sorted(
        [l for l in levels if l.kind == "resistance" and l.price > current_price],
        key=lambda l: l.price - current_price,
    )

    score = 50.0  # neutral base

    # 1. Support proximity bonus (max +25)
    if supports:
        s1 = supports[0]
        dist_pct = (current_price - s1.price) / current_price
        if dist_pct < 0.05:
            proximity = 1.0 - dist_pct / 0.05  # 1.0 at support, 0.0 at 5% away
            quality = min(s1.score, 100.0) / 100.0
            score += proximity * quality * 25

    # 2. Risk/reward bonus (max +15)
    if supports and resistances:
        s1, r1 = supports[0], resistances[0]
        dist_s = max(current_price - s1.price, 0.001)
        dist_r = r1.price - current_price
        rr = dist_r / dist_s
        score += min(rr * 5, 15)

    # 3. Trend alignment (max +10)
    if len(candles) >= 20:
        ma20 = sum(c.close for c in candles[-20:]) / 20
        if current_price > ma20:
            score += 10
        elif current_price < ma20 * 0.97:
            score -= 5

    # 4. Support depth bonus (max +10)
    if len(supports) >= 2:
        score += min(len(supports) * 3, 10)

    # 5. Resistance pressure penalty (max -15)
    if resistances:
        r1 = resistances[0]
        dist_pct = (r1.price - current_price) / current_price
        if dist_pct < 0.02:
            pressure = 1.0 - dist_pct / 0.02
            quality = min(r1.score, 100.0) / 100.0
            score -= pressure * quality * 15

    score = max(0, min(100, score))

    label = "回避"
    for threshold, lbl in _DECISION_LABELS:
        if score >= threshold:
            label = lbl
            break

    return round(score, 1), label
