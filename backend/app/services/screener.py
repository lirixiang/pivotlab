"""Pattern screener V4: aligned with stock-sr-platform.

Models:
  - breakout_pullback: 突破回踩
  - stabilize: 下跌企稳
  - box_support: 箱体支撑
  - volume_breakout: 放量突破
  - macd_divergence: MACD底背离

Architecture:
  - SR cache: compute levels once per stock, reuse across models
  - Weekly candles for trend + confluence
  - 3-step funnel: coarse filter → SR check → shape confirmation
  - Scoring formulas identical to stock-sr-platform
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from ..schemas import Candle, ScreenerItem
from .levels_multifactor import detect_levels_multifactor


# ─────────────────────────────────────────────────────────────────────
# Configuration (matching SR thresholds)
# ─────────────────────────────────────────────────────────────────────

@dataclass
class ModelConfig:
    """Per-model configurable parameters."""
    enabled: bool = True
    min_candles: int = 120
    max_dist_support_pct: float = 1.5
    min_support_score: float = 75
    min_total_score: float = 60
    min_touch_count: int = 0
    label: str = ""


SCREENER_CONFIG: dict[str, ModelConfig] = {
    "breakout_pullback": ModelConfig(
        label="突破回踩",
        min_candles=120,
        max_dist_support_pct=1.5,
        min_support_score=75,
        min_total_score=60,
    ),
    "stabilize": ModelConfig(
        label="下跌企稳",
        min_candles=120,
        max_dist_support_pct=8.0,
        min_support_score=60,
        min_total_score=52,
        min_touch_count=2,
    ),
    "box_support": ModelConfig(
        label="箱体支撑",
        min_candles=120,
        max_dist_support_pct=4.5,
        min_support_score=55,
        min_total_score=50,
        min_touch_count=2,
    ),
    "volume_breakout": ModelConfig(
        label="放量突破",
        min_candles=120,
        max_dist_support_pct=5.0,
        min_support_score=50,
        min_total_score=75,
        min_touch_count=1,
    ),
    "macd_divergence": ModelConfig(
        label="MACD底背离",
        min_candles=120,
        max_dist_support_pct=8.0,
        min_support_score=45,
        min_total_score=72,
        min_touch_count=1,
    ),
}


def get_config() -> dict[str, dict]:
    """Return current screener config as serializable dict."""
    return {k: v.__dict__ for k, v in SCREENER_CONFIG.items()}


def update_config(pattern: str, **kwargs) -> bool:
    """Update config for a pattern. Only updates provided keys."""
    cfg = SCREENER_CONFIG.get(pattern)
    if cfg is None:
        return False
    for k, v in kwargs.items():
        if hasattr(cfg, k):
            setattr(cfg, k, type(getattr(cfg, k))(v))
    return True


# ─────────────────────────────────────────────────────────────────────
# SR Cache — compute once per stock, reuse across all models
# ─────────────────────────────────────────────────────────────────────

@dataclass
class SRContext:
    """Cached SR levels for a stock."""
    sup_price: float | None
    sup_score: float
    sup_touches: int
    res_price: float | None
    weekly_conf: bool
    weekly_trend: dict  # {"direction": "up"|"down"|"flat", "rising": bool, "above_slow": bool}


_sr_cache: dict[str, SRContext] = {}


def clear_sr_cache():
    """Clear SR cache (call at start of each scan)."""
    _sr_cache.clear()


def get_sr_context(code: str, candles: list[Candle], price: float, weekly_candles: list[Candle] | None = None) -> SRContext:
    """Get or compute SR context for a stock. Cached per code within a scan."""
    if code in _sr_cache:
        return _sr_cache[code]

    sup_price, sup_score, sup_touches, weekly_conf = None, 0, 0, False
    res_price = None

    try:
        levels = detect_levels_multifactor(candles, lookback=min(len(candles), 120))
        supports = [lv for lv in levels if lv.kind == "support" and lv.price < price]
        resistances = [lv for lv in levels if lv.kind == "resistance" and lv.price > price]

        if supports:
            s1 = max(supports, key=lambda lv: lv.price)
            sup_price = s1.price
            sup_score = s1.score
            sup_touches = getattr(s1, "touch_count", 2)
            weekly_conf = getattr(s1, "weekly_confluence", False)

        if resistances:
            r1 = min(resistances, key=lambda lv: lv.price)
            res_price = r1.price
    except Exception:
        pass

    wtrend = _weekly_trend_context(weekly_candles)

    ctx = SRContext(
        sup_price=sup_price, sup_score=sup_score, sup_touches=sup_touches,
        res_price=res_price, weekly_conf=weekly_conf, weekly_trend=wtrend,
    )
    _sr_cache[code] = ctx
    return ctx


def _weekly_trend_context(weekly_candles: list[Candle] | None) -> dict:
    """Compute weekly trend direction from weekly candles.
    Falls back to a simple dict if not enough data.
    """
    if not weekly_candles or len(weekly_candles) < 8:
        return {"direction": "flat", "rising": False, "above_slow": True}

    closes = [c.close for c in weekly_candles[-12:]]
    ma_fast = sum(closes[-4:]) / 4
    ma_slow = sum(closes[-8:]) / 8
    latest = closes[-1]

    if latest >= ma_fast >= ma_slow:
        direction = "up"
    elif latest < ma_fast < ma_slow:
        direction = "down"
    else:
        direction = "flat"

    rising = len(closes) >= 3 and closes[-1] >= closes[-2] >= closes[-3]
    above_slow = latest >= ma_slow

    return {
        "direction": direction,
        "rising": rising,
        "above_slow": above_slow,
    }


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def _vol_ratio(candles: list[Candle], window: int = 5) -> float:
    if len(candles) < window + 1:
        return 0.0
    recent = candles[-1].volume
    prev = np.mean([c.volume for c in candles[-window - 1:-1]])
    return float(recent / prev) if prev > 0 else 0.0


def _rr_ratio(cur: float, sup_price: float, target_price: float | None) -> float:
    """Risk/reward ratio."""
    stop_loss = sup_price * 0.98
    risk = cur - stop_loss
    if risk <= 0:
        return 0
    reward = (target_price - cur) if target_price and target_price > cur else risk * 2
    return reward / risk


# ─────────────────────────────────────────────────────────────────────
# Model: breakout_pullback (突破回踩) — matches SR exactly
# ─────────────────────────────────────────────────────────────────────

def detect_breakout_pullback(code: str, name: str, candles: list[Candle], weekly_candles: list[Candle] | None = None) -> ScreenerItem | None:
    cfg = SCREENER_CONFIG["breakout_pullback"]
    if not cfg.enabled or len(candles) < cfg.min_candles:
        return None

    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    volumes = [c.volume for c in candles]
    cur = closes[-1]
    n = len(candles)

    # ── Coarse filter: must have made 20-day high + pullback 3-15% ──
    high20 = max(highs[-20:])
    pullback_pct = (high20 - cur) / high20
    if not (0.03 <= pullback_pct <= 0.15):
        return None

    # ── SR from cache ──
    ctx = get_sr_context(code, candles, cur, weekly_candles)
    if ctx.sup_price is None:
        return None
    if ctx.sup_score < cfg.min_support_score:
        return None

    dist_pct = abs(cur - ctx.sup_price) / ctx.sup_price
    if dist_pct > cfg.max_dist_support_pct / 100:
        return None

    # ── Weekly trend: must not be fully down ──
    if ctx.weekly_trend["direction"] == "down":
        return None

    # ── A) Pullback duration: 3~8 trading days ──
    high20_idx = None
    for i in range(n - 1, max(n - 21, -1), -1):
        if highs[i] >= high20 * 0.998:
            high20_idx = i
            break
    if high20_idx is None:
        return None

    pullback_days = n - 1 - high20_idx
    if pullback_days < 3 or pullback_days > 8:
        return None

    # ── B) Volume shrinkage during pullback ──
    breakout_vol = volumes[high20_idx]
    recent_3_vol = sum(volumes[-3:]) / 3
    vol_shrink_ratio = recent_3_vol / max(breakout_vol, 1)
    volume_shrinking = vol_shrink_ratio < 0.6

    # ── C) Stop-decline K-line signal ──
    last = candles[-1]
    prev = candles[-2] if n >= 2 else last
    last_body = abs(last.close - last.open)
    last_lower_shadow = min(last.open, last.close) - last.low
    last_range = last.high - last.low

    has_lower_shadow = last_lower_shadow > last_body and last_range > 0
    is_doji = last_body / max(last_range, 0.01) < 0.3 and last_range > 0
    is_small_yang = last.close > last.open and last_body / max(cur, 1) < 0.015
    no_new_low = last.low >= prev.low

    # ── D) MA5 not diverging downward ──
    ma5 = sum(closes[-5:]) / 5
    ma10 = sum(closes[-10:]) / 10
    if ma5 < ma10 * 0.995:
        return None

    # ── E) Last bar not big red ──
    last_pct = (last.close - last.open) / max(last.open, 1)
    if last_pct < -0.03:
        return None

    # ── Scoring (identical to SR) ──
    score_support = min(ctx.sup_score, 100) * 0.40
    score_precision = max(0, (1.5 - dist_pct * 100) / 1.5) * 25

    score_volume = 0
    if volume_shrinking:
        score_volume = 15
    elif vol_shrink_ratio < 0.8:
        score_volume = 10

    score_signal = 0
    signal_parts = []
    if has_lower_shadow:
        score_signal += 8
        signal_parts.append("下影线护盘")
    if is_doji:
        score_signal += 6
        signal_parts.append("十字星")
    if is_small_yang:
        score_signal += 6
        signal_parts.append("小阳企稳")
    if no_new_low:
        score_signal += 4
    score_signal = min(score_signal, 20)

    role_reversal = ctx.sup_touches >= 3
    reversal_bonus = 5 if role_reversal else 0
    weekly_bonus = 5 if ctx.weekly_trend["direction"] == "up" else 2
    confluence_bonus = 3 if ctx.weekly_conf else 0

    total_score = score_support + score_precision + score_volume + score_signal + reversal_bonus + weekly_bonus + confluence_bonus
    total_score = round(min(total_score, 100), 1)

    if total_score < cfg.min_total_score:
        return None

    # ── Build triggers ──
    triggers = [f"回踩{pullback_days}天至强支撑位"]
    if volume_shrinking:
        triggers.append(f"回踩缩量{vol_shrink_ratio:.0%}")
    if signal_parts:
        triggers.append("止跌信号: " + "+".join(signal_parts))
    if ctx.weekly_trend["direction"] == "up":
        triggers.append("周线向上")
    if role_reversal:
        triggers.append("角色反转支撑")
    if ctx.weekly_conf:
        triggers.append("周线位共振")

    vr = _vol_ratio(candles)
    change_pct = (last.close / candles[-2].close - 1) * 100 if n >= 2 else 0.0
    rr = _rr_ratio(cur, ctx.sup_price, high20)

    return ScreenerItem(
        code=code, name=name, pattern="breakout_pullback",
        score=total_score, price=round(cur, 2),
        change_pct=round(change_pct, 2),
        volume_ratio=round(vr, 2),
        breakout_price=round(high20, 2),
        pullback_price=round(min(lows[-pullback_days:]), 2),
        distance_to_support_pct=round(dist_pct * 100, 2),
        triggers=triggers,
        rr_ratio=round(rr, 2),
        support_score=round(ctx.sup_score, 1),
    )


# ─────────────────────────────────────────────────────────────────────
# Model: stabilize (下跌企稳) — matches SR _model_stabilize
# ─────────────────────────────────────────────────────────────────────

def detect_stabilize(code: str, name: str, candles: list[Candle], weekly_candles: list[Candle] | None = None) -> ScreenerItem | None:
    cfg = SCREENER_CONFIG["stabilize"]
    if not cfg.enabled or len(candles) < cfg.min_candles:
        return None

    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    volumes = [c.volume for c in candles]
    cur = closes[-1]
    n = len(candles)

    # ── Coarse filter: drawdown 10-35%, rebound < 12% ──
    lookback = min(n, 60)
    high60 = max(highs[-lookback:])
    low20 = min(lows[-20:])
    low30 = min(lows[-30:])

    drawdown_pct = (high60 - cur) / max(high60, 1)
    if not (0.10 <= drawdown_pct <= 0.35):
        return None

    rebound_from_low_pct = (cur - low20) / max(low20, 1)
    if rebound_from_low_pct > 0.12:
        return None

    recent_5_low = min(lows[-5:])
    if recent_5_low < low20 * 0.97:
        return None

    # ── SR from cache ──
    ctx = get_sr_context(code, candles, cur, weekly_candles)
    if ctx.sup_price is None:
        return None
    if ctx.sup_score < cfg.min_support_score:
        return None
    if ctx.sup_touches < cfg.min_touch_count:
        return None

    dist_pct = abs(cur - ctx.sup_price) / max(ctx.sup_price, 1)
    if dist_pct > cfg.max_dist_support_pct / 100:
        return None

    # ── Range position check ──
    range_20 = max(max(highs[-20:]) - low20, 0.01)
    range_position = (cur - low20) / range_20
    if range_position > 0.75:
        return None

    # ── Low zone: recent lows holding above support ──
    if recent_5_low < ctx.sup_price * 0.97:
        return None
    recent_10_low = min(lows[-10:])
    if recent_10_low < low30 * 0.97:
        return None

    # ── Stop-decline K-line ──
    last = candles[-1]
    prev = candles[-2] if n >= 2 else last
    last_body = abs(last.close - last.open)
    last_range = max(last.high - last.low, 0.01)
    last_lower_shadow = min(last.open, last.close) - last.low
    last_pct = (last.close - last.open) / max(last.open, 1)

    has_lower_shadow = last_lower_shadow >= last_body * 0.9
    is_doji = last_body / last_range < 0.33
    is_small_up = last.close >= last.open and last_body / max(cur, 1) < 0.018
    no_new_low = last.low >= prev.low
    closes_above_recent_low = last.close >= low20 * 1.02

    if last_pct < -0.04:
        return None
    if not ((has_lower_shadow or is_doji or is_small_up) and no_new_low and closes_above_recent_low):
        return None

    # ── MA checks ──
    ma5 = sum(closes[-5:]) / 5
    prev_ma5 = sum(closes[-6:-1]) / 5
    ma10 = sum(closes[-10:]) / 10
    ma_turning = ma5 >= prev_ma5 * 0.998
    ma_not_weak = ma5 >= ma10 * 0.97
    if not ma_turning or not ma_not_weak:
        return None

    # ── Resistance gap >= 3% ──
    effective_resistance = max(ctx.res_price or 0, max(highs[-60:]))
    if effective_resistance <= cur:
        return None
    resistance_gap_pct = (effective_resistance - cur) / max(cur, 1)
    if resistance_gap_pct < 0.03:
        return None

    # ── Weekly: not fully down unless rising ──
    if ctx.weekly_trend["direction"] == "down" and not ctx.weekly_trend.get("rising"):
        return None

    # ── Volume ──
    recent_5_vol = sum(volumes[-5:]) / 5
    avg_vol_20 = sum(volumes[-20:]) / 20
    volume_shrink = recent_5_vol <= avg_vol_20 * 0.85

    # ── Scoring (identical to SR _model_stabilize) ──
    score_support = min(ctx.sup_score, 100) * 0.36
    score_precision = max(0, (8 - dist_pct * 100) / 8) * 18
    score_drawdown = 12 if 0.14 <= drawdown_pct <= 0.24 else 8 if 0.10 <= drawdown_pct <= 0.30 else 4
    score_position = 10 if range_position <= 0.25 else 6

    score_signal = 0
    signal_parts = []
    if has_lower_shadow:
        score_signal += 6
        signal_parts.append("下影承接")
    if is_doji:
        score_signal += 5
        signal_parts.append("十字止跌")
    if is_small_up:
        score_signal += 5
        signal_parts.append("小阳回稳")
    if no_new_low:
        score_signal += 4
    score_signal = min(score_signal, 18)

    volume_bonus = 10 if volume_shrink else 5 if recent_5_vol <= avg_vol_20 else 0
    weekly_bonus = 8 if ctx.weekly_trend["direction"] == "up" else 5 if ctx.weekly_trend["direction"] == "flat" else 2

    total_score = score_support + score_precision + score_drawdown + score_position + score_signal + volume_bonus + weekly_bonus
    total_score = round(min(total_score, 100), 1)
    if total_score < cfg.min_total_score:
        return None

    # ── Triggers ──
    triggers = [f"近60日回撤{drawdown_pct * 100:.1f}%"]
    triggers.append(f"距支撑{dist_pct * 100:.1f}%")
    triggers.append(f"处于20日低位区{range_position * 100:.0f}%")
    if signal_parts:
        triggers.append("止跌信号: " + "+".join(signal_parts))
    if volume_shrink:
        triggers.append("近5日缩量企稳")
    if ma_turning:
        triggers.append("短线均线止跌回稳")
    if ctx.weekly_trend["direction"] in {"up", "flat"}:
        triggers.append(f"周线{'向上' if ctx.weekly_trend['direction'] == 'up' else '走平'}")
    elif ctx.weekly_trend.get("rising"):
        triggers.append("周线下跌但连续收稳")

    vr = _vol_ratio(candles)
    change_pct = (last.close / candles[-2].close - 1) * 100 if n >= 2 else 0.0
    rr = _rr_ratio(cur, ctx.sup_price, effective_resistance)

    return ScreenerItem(
        code=code, name=name, pattern="stabilize",
        score=total_score, price=round(cur, 2),
        change_pct=round(change_pct, 2),
        volume_ratio=round(vr, 2),
        breakout_price=None,
        pullback_price=round(ctx.sup_price, 2),
        distance_to_support_pct=round(dist_pct * 100, 2),
        triggers=triggers,
        rr_ratio=round(rr, 2),
        support_score=round(ctx.sup_score, 1),
    )


# ─────────────────────────────────────────────────────────────────────
# Model: volume_breakout (放量突破) — 量价齐升突破前高
# ─────────────────────────────────────────────────────────────────────

def detect_volume_breakout(code: str, name: str, candles: list[Candle], weekly_candles: list[Candle] | None = None) -> ScreenerItem | None:
    """Detect stocks breaking out of consolidation with volume surge.

    Logic: price breaks above recent resistance / consolidation high on
    above-average volume, ideally with support below as a safety net.
    """
    cfg = SCREENER_CONFIG["volume_breakout"]
    if not cfg.enabled or len(candles) < cfg.min_candles:
        return None

    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    volumes = [c.volume for c in candles]
    cur = closes[-1]
    n = len(candles)

    # ── Coarse filter: close above 20-day high (excluding last 3 bars to allow fresh breakout) ──
    if n < 25:
        return None
    lookback_highs = highs[-23:-3]  # 20 bars ending 3 bars ago
    if not lookback_highs:
        return None
    prev_high = max(lookback_highs)
    # Price must be near or above the previous high (within 2% above to count recent breakout)
    breakout_pct = (cur - prev_high) / max(prev_high, 1)
    if breakout_pct < -0.01:  # allow 1% below (just touched)
        return None
    if breakout_pct > 0.15:  # too far above, not a fresh breakout
        return None

    # ── Volume surge: last 3-day avg volume >= 1.5x 20-day avg ──
    avg_vol_20 = np.mean(volumes[-23:-3]) if len(volumes) >= 23 else np.mean(volumes[:-3])
    if avg_vol_20 <= 0:
        return None
    avg_vol_recent = np.mean(volumes[-3:])
    vol_ratio_breakout = float(avg_vol_recent / avg_vol_20)
    if vol_ratio_breakout < 2.0:
        return None

    # ── Pre-breakout consolidation: 20-day range before breakout <= 25% ──
    pre_high = max(highs[-23:-3])
    pre_low = min(lows[-23:-3])
    pre_range_pct = (pre_high - pre_low) / max(pre_low, 1)
    if pre_range_pct > 0.22:
        return None

    # ── Last bar: must be positive or small doji (not big red) ──
    last = candles[-1]
    last_pct = (last.close - last.open) / max(last.open, 1)
    if last_pct < -0.03:
        return None

    # ── SR from cache ──
    ctx = get_sr_context(code, candles, cur, weekly_candles)

    # Support is nice-to-have, not mandatory for breakout
    dist_pct = 0.0
    if ctx.sup_price is not None and ctx.sup_price < cur:
        dist_pct = (cur - ctx.sup_price) / max(ctx.sup_price, 1)

    # ── Weekly trend: prefer not down ──
    weekly_down = ctx.weekly_trend["direction"] == "down"

    # ── MA alignment: MA5 > MA10 > MA20 (bullish alignment) ──
    ma5 = sum(closes[-5:]) / 5
    ma10 = sum(closes[-10:]) / 10
    ma20 = sum(closes[-20:]) / 20
    ma_aligned = ma5 >= ma10 >= ma20
    ma_rising = ma5 > ma20  # at least short above long

    if not ma_aligned:
        return None

    # ── Resistance gap (upside room) ──
    effective_resistance = ctx.res_price if ctx.res_price is not None and ctx.res_price > cur else prev_high * 1.15
    resistance_gap_pct = (effective_resistance - cur) / max(cur, 1)

    # ── Scoring ──
    # Volume component: higher volume ratio = stronger signal (0-25)
    score_volume = min((vol_ratio_breakout - 1.0) * 25, 25)

    # Breakout strength: how cleanly price broke out (0-20)
    score_breakout = 20 if breakout_pct >= 0.03 else 15 if breakout_pct >= 0.01 else 10 if breakout_pct >= 0 else 5

    # Support safety net (0-20)
    score_support = 0
    if ctx.sup_price is not None:
        score_support = min(ctx.sup_score, 100) * 0.20

    # MA alignment (0-15)
    score_ma = 15 if ma_aligned else 8 if ma_rising else 0

    # Weekly trend bonus (0-10)
    weekly_bonus = 10 if ctx.weekly_trend["direction"] == "up" else 5 if not weekly_down else 0

    # Consolidation tightness bonus (0-10): tighter pre-breakout range = better
    score_consolidation = max(0, (0.25 - pre_range_pct) / 0.25) * 10

    total_score = score_volume + score_breakout + score_support + score_ma + weekly_bonus + score_consolidation
    total_score = round(min(total_score, 100), 1)
    if total_score < cfg.min_total_score:
        return None

    # ── Triggers ──
    triggers = [f"突破前高{prev_high:.2f}，涨幅{breakout_pct * 100:.1f}%"]
    triggers.append(f"量比{vol_ratio_breakout:.1f}倍(3日/20日)")
    triggers.append(f"整理幅度{pre_range_pct * 100:.1f}%")
    if ma_aligned:
        triggers.append("均线多头排列(5>10>20)")
    if ctx.sup_price is not None:
        triggers.append(f"下方支撑{ctx.sup_price:.2f}")
    if ctx.weekly_trend["direction"] == "up":
        triggers.append("周线向上")

    vr = _vol_ratio(candles)
    change_pct = (last.close / candles[-2].close - 1) * 100 if n >= 2 else 0.0
    sup_for_rr = ctx.sup_price if ctx.sup_price else cur * 0.95
    rr = _rr_ratio(cur, sup_for_rr, effective_resistance)

    return ScreenerItem(
        code=code, name=name, pattern="volume_breakout",
        score=total_score, price=round(cur, 2),
        change_pct=round(change_pct, 2),
        volume_ratio=round(vr, 2),
        breakout_price=round(prev_high, 2),
        pullback_price=round(ctx.sup_price, 2) if ctx.sup_price else None,
        distance_to_support_pct=round(dist_pct * 100, 2) if ctx.sup_price else None,
        triggers=triggers,
        rr_ratio=round(rr, 2),
        support_score=round(ctx.sup_score, 1),
    )


# ─────────────────────────────────────────────────────────────────────
# Model: macd_divergence (MACD底背离) — 底背离信号
# ─────────────────────────────────────────────────────────────────────

def _compute_macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9):
    """Compute MACD line, signal line, histogram."""
    arr = np.array(closes, dtype=float)
    ema_fast = _ema(arr, fast)
    ema_slow = _ema(arr, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def _ema(data: np.ndarray, period: int) -> np.ndarray:
    """Exponential moving average."""
    alpha = 2.0 / (period + 1)
    result = np.empty_like(data)
    result[0] = data[0]
    for i in range(1, len(data)):
        result[i] = alpha * data[i] + (1 - alpha) * result[i - 1]
    return result


def detect_macd_divergence(code: str, name: str, candles: list[Candle], weekly_candles: list[Candle] | None = None) -> ScreenerItem | None:
    """Detect bullish MACD divergence (bottom divergence).

    Logic: price makes a lower low while MACD histogram or MACD line makes
    a higher low — classic bottom divergence signal suggesting trend reversal.
    """
    cfg = SCREENER_CONFIG["macd_divergence"]
    if not cfg.enabled or len(candles) < cfg.min_candles:
        return None

    closes = [c.close for c in candles]
    lows = [c.low for c in candles]
    highs = [c.high for c in candles]
    volumes = [c.volume for c in candles]
    cur = closes[-1]
    n = len(candles)

    # Need at least 60 bars for meaningful MACD
    if n < 60:
        return None

    # ── Compute MACD ──
    macd_line, signal_line, histogram = _compute_macd(closes)

    # ── Find two recent troughs in price and MACD ──
    # Look in last 60 bars for two distinct lows
    search_len = min(n, 60)
    search_lows = lows[-search_len:]
    search_hist = histogram[-search_len:]
    search_macd = macd_line[-search_len:]

    # Find local minima in price (within 5-bar window)
    price_troughs = []
    for i in range(5, search_len - 3):
        window = search_lows[max(0, i - 5):i + 6]
        if search_lows[i] == min(window):
            price_troughs.append((i, search_lows[i]))

    if len(price_troughs) < 2:
        return None

    # Take the two most recent troughs, at least 8 bars apart
    valid_pairs = []
    for j in range(len(price_troughs) - 1):
        for k in range(j + 1, len(price_troughs)):
            t1_idx, t1_price = price_troughs[j]
            t2_idx, t2_price = price_troughs[k]
            if t2_idx - t1_idx >= 8:
                valid_pairs.append((t1_idx, t1_price, t2_idx, t2_price))

    if not valid_pairs:
        return None

    # Use the latest valid pair
    t1_idx, t1_price, t2_idx, t2_price = valid_pairs[-1]

    # ── Check divergence: price lower low, MACD higher low ──
    # Second trough must be within last 6 bars (recent signal)
    if search_len - t2_idx > 8:
        return None

    # Price: second low <= first low (lower or equal low)
    if t2_price > t1_price * 1.01:  # allow 1% tolerance
        return None

    # MACD: second trough higher than first (divergence)
    macd_t1 = min(search_hist[max(0, t1_idx - 2):t1_idx + 3])
    macd_t2 = min(search_hist[max(0, t2_idx - 2):t2_idx + 3])

    # Also check MACD line itself
    macd_line_t1 = min(search_macd[max(0, t1_idx - 2):t1_idx + 3])
    macd_line_t2 = min(search_macd[max(0, t2_idx - 2):t2_idx + 3])

    hist_divergence = macd_t2 > macd_t1  # histogram higher low
    line_divergence = macd_line_t2 > macd_line_t1  # MACD line higher low

    if not hist_divergence:  # histogram divergence is required
        return None
    if not line_divergence:  # MACD line divergence also required
        return None

    # ── Current MACD should be turning up (histogram increasing) ──
    if len(histogram) >= 3:
        hist_turning = histogram[-1] > histogram[-2] or histogram[-1] > histogram[-3]
    else:
        hist_turning = False

    if not hist_turning:
        return None

    # ── Must not be in free-fall: last bar not big red ──
    last = candles[-1]
    last_pct = (last.close - last.open) / max(last.open, 1)
    if last_pct < -0.05:
        return None

    # ── Recent drawdown: must have pulled back (not at highs) ──
    high60 = max(highs[-60:])
    drawdown_pct = (high60 - cur) / max(high60, 1)
    if drawdown_pct < 0.12:  # need meaningful pullback for divergence
        return None

    # ── SR from cache ──
    ctx = get_sr_context(code, candles, cur, weekly_candles)

    dist_pct = 0.0
    if ctx.sup_price is not None and ctx.sup_price < cur:
        dist_pct = (cur - ctx.sup_price) / max(ctx.sup_price, 1)

    # ── Scoring ──
    # Divergence clarity (0-30)
    score_divergence = 0
    if hist_divergence and line_divergence:
        score_divergence = 30  # both diverge — strongest signal
    elif hist_divergence:
        score_divergence = 22
    elif line_divergence:
        score_divergence = 18

    # MACD turning bonus (0-15)
    score_turning = 15 if hist_turning else 5

    # Support safety net (0-20)
    score_support = 0
    if ctx.sup_price is not None:
        score_support = min(ctx.sup_score, 100) * 0.20

    # Drawdown depth: deeper pullback = more room (0-15)
    score_drawdown = min(drawdown_pct * 100, 15)

    # Weekly trend (0-10)
    weekly_bonus = 10 if ctx.weekly_trend["direction"] == "up" else 5 if ctx.weekly_trend["direction"] == "flat" else 0

    # Volume shrinkage at second trough (0-10): lower volume = healthier divergence
    vol_t1 = float(np.mean(volumes[max(0, n - search_len + t1_idx - 2):n - search_len + t1_idx + 3]))
    vol_t2 = float(np.mean(volumes[max(0, n - search_len + t2_idx - 2):n - search_len + t2_idx + 3]))
    vol_shrink = vol_t2 < vol_t1 * 0.9 if vol_t1 > 0 else False
    score_volume = 10 if vol_shrink else 4

    total_score = score_divergence + score_turning + score_support + score_drawdown + weekly_bonus + score_volume
    total_score = round(min(total_score, 100), 1)
    if total_score < cfg.min_total_score:
        return None

    # ── Triggers ──
    price_diff_pct = (t2_price - t1_price) / max(t1_price, 1) * 100
    triggers = [f"价格二次探底({t2_price:.2f} vs {t1_price:.2f})"]
    if hist_divergence:
        triggers.append("MACD柱状线底背离")
    if line_divergence:
        triggers.append("MACD线底背离")
    if hist_turning:
        triggers.append("MACD柱状线拐头向上")
    triggers.append(f"回调幅度{drawdown_pct * 100:.1f}%")
    if vol_shrink:
        triggers.append("二次探底量能萎缩")
    if ctx.sup_price is not None:
        triggers.append(f"下方支撑{ctx.sup_price:.2f}")
    if ctx.weekly_trend["direction"] in {"up", "flat"}:
        triggers.append(f"周线{'向上' if ctx.weekly_trend['direction'] == 'up' else '走平'}")

    vr = _vol_ratio(candles)
    change_pct = (last.close / candles[-2].close - 1) * 100 if n >= 2 else 0.0
    effective_resistance = ctx.res_price if ctx.res_price is not None and ctx.res_price > cur else high60
    sup_for_rr = ctx.sup_price if ctx.sup_price else min(lows[-20:])
    rr = _rr_ratio(cur, sup_for_rr, effective_resistance)

    return ScreenerItem(
        code=code, name=name, pattern="macd_divergence",
        score=total_score, price=round(cur, 2),
        change_pct=round(change_pct, 2),
        volume_ratio=round(vr, 2),
        breakout_price=None,
        pullback_price=round(ctx.sup_price, 2) if ctx.sup_price else None,
        distance_to_support_pct=round(dist_pct * 100, 2) if ctx.sup_price else None,
        triggers=triggers,
        rr_ratio=round(rr, 2),
        support_score=round(ctx.sup_score, 1),
    )


# ─────────────────────────────────────────────────────────────────────
# Model: box_support (箱体支撑) — matches SR _model_box_support
# ─────────────────────────────────────────────────────────────────────

def detect_box_support(code: str, name: str, candles: list[Candle], weekly_candles: list[Candle] | None = None) -> ScreenerItem | None:
    cfg = SCREENER_CONFIG["box_support"]
    if not cfg.enabled or len(candles) < cfg.min_candles:
        return None

    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    volumes = [c.volume for c in candles]
    cur = closes[-1]
    n = len(candles)

    # ── Box definition: 60-day range ──
    lookback = min(n, 60)
    box_high = max(highs[-lookback:])
    box_low = min(lows[-lookback:])
    box_range = box_high - box_low
    if box_range <= 0:
        return None

    box_height_pct = box_range / max(box_low, 1)
    if not (0.06 <= box_height_pct <= 0.36):
        return None

    # ── Coarse: box position < 48%, 2+ touches on both sides ──
    box_position = (cur - box_low) / max(box_range, 0.01)
    if box_position > 0.48:
        return None

    low_touches = sum(1 for low in lows[-lookback:] if low <= box_low * 1.015)
    high_touches = sum(1 for high in highs[-lookback:] if high >= box_high * 0.985)
    if low_touches < 2 or high_touches < 2:
        return None

    # ── SR from cache ──
    ctx = get_sr_context(code, candles, cur, weekly_candles)
    if ctx.sup_price is None:
        return None
    if ctx.sup_score < cfg.min_support_score:
        return None
    if ctx.sup_touches < cfg.min_touch_count:
        return None

    dist_pct = abs(cur - ctx.sup_price) / max(ctx.sup_price, 1)
    if dist_pct > cfg.max_dist_support_pct / 100:
        return None

    # ── Resistance gap >= 3% ──
    effective_resistance = ctx.res_price if ctx.res_price is not None and ctx.res_price > cur else box_high
    if effective_resistance <= cur:
        return None
    resistance_gap_pct = (effective_resistance - cur) / max(cur, 1)
    if resistance_gap_pct < 0.03:
        return None

    # ── Recent 20-day low >= support * 0.95 ──
    recent_20_low = min(lows[-20:])
    if recent_20_low < ctx.sup_price * 0.95:
        return None

    # ── Stop-decline K-line ──
    last = candles[-1]
    prev = candles[-2] if n >= 2 else last
    last_body = abs(last.close - last.open)
    last_lower_shadow = min(last.open, last.close) - last.low
    last_range = max(last.high - last.low, 0.01)
    last_pct = (last.close - last.open) / max(last.open, 1)

    has_lower_shadow = last_lower_shadow > last_body
    is_doji = last_body / last_range < 0.33
    is_small_up = last.close >= last.open and last_body / max(cur, 1) < 0.018
    is_small_down = last.close < last.open and last_pct > -0.02 and last_body / max(cur, 1) < 0.02
    no_new_low = last.low >= prev.low

    if last_pct < -0.045:
        return None
    if not ((has_lower_shadow or is_doji or is_small_up or is_small_down) and no_new_low):
        return None

    # ── MA10 >= MA20 * 0.95 ──
    ma10 = sum(closes[-10:]) / 10
    ma20 = sum(closes[-20:]) / 20
    if ma10 < ma20 * 0.95:
        return None

    # ── Volume not exploding (recent 5-day <= 120% of 20-day avg) ──
    recent_5_vol = sum(volumes[-5:]) / 5
    avg_vol_20 = sum(volumes[-20:]) / 20
    volume_ok = recent_5_vol <= avg_vol_20 * 1.2

    # ── Weekly trend: must not be fully down ──
    if ctx.weekly_trend["direction"] == "down":
        return None

    # ── Scoring (identical to SR _model_box_support) ──
    score_support = min(ctx.sup_score, 100) * 0.35
    score_precision = max(0, (2.0 - dist_pct * 100) / 2.0) * 25
    score_box = 15 if 0.10 <= box_height_pct <= 0.22 else 10

    score_signal = 0
    signal_parts = []
    if has_lower_shadow:
        score_signal += 6
        signal_parts.append("下影承接")
    if is_doji:
        score_signal += 5
        signal_parts.append("十字企稳")
    if is_small_up:
        score_signal += 5
        signal_parts.append("小阳回稳")
    if is_small_down:
        score_signal += 4
        signal_parts.append("小阴止跌")
    if no_new_low:
        score_signal += 4
    score_signal = min(score_signal, 15)

    touch_bonus = min((ctx.sup_touches or 0), 4) * 2
    weekly_bonus = 6 if ctx.weekly_trend["direction"] == "up" else 3 if ctx.weekly_trend["direction"] == "flat" else 0
    volume_bonus = 4 if volume_ok else 0

    total_score = score_support + score_precision + score_box + score_signal + touch_bonus + weekly_bonus + volume_bonus
    total_score = round(min(total_score, 100), 1)
    if total_score < cfg.min_total_score:
        return None

    # ── Triggers ──
    triggers = [f"箱体下沿附近，距支撑{dist_pct * 100:.1f}%"]
    triggers.append(f"箱体宽度{box_height_pct * 100:.1f}%")
    triggers.append(f"支撑触碰{ctx.sup_touches}次")
    if signal_parts:
        triggers.append("止跌信号: " + "+".join(signal_parts))
    if volume_ok:
        triggers.append("量能未放大，低吸容错尚可")
    if ctx.weekly_trend["direction"] in {"up", "flat"}:
        triggers.append(f"周线{'向上' if ctx.weekly_trend['direction'] == 'up' else '走平'}")

    vr = _vol_ratio(candles)
    change_pct = (last.close / candles[-2].close - 1) * 100 if n >= 2 else 0.0
    rr = _rr_ratio(cur, ctx.sup_price, effective_resistance)

    return ScreenerItem(
        code=code, name=name, pattern="box_support",
        score=total_score, price=round(cur, 2),
        change_pct=round(change_pct, 2),
        volume_ratio=round(vr, 2),
        breakout_price=round(box_high, 2),
        pullback_price=round(ctx.sup_price, 2),
        distance_to_support_pct=round(dist_pct * 100, 2),
        triggers=triggers,
        rr_ratio=round(rr, 2),
        support_score=round(ctx.sup_score, 1),
    )


# ─────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────

PATTERN_DETECTORS = {
    "breakout_pullback": detect_breakout_pullback,
    "stabilize": detect_stabilize,
    "box_support": detect_box_support,
    "volume_breakout": detect_volume_breakout,
    "macd_divergence": detect_macd_divergence,
}

MODEL_LABELS = {
    "breakout_pullback": "突破回踩",
    "stabilize": "下跌企稳",
    "box_support": "箱体支撑",
    "volume_breakout": "放量突破",
    "macd_divergence": "MACD底背离",
}
