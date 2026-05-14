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
    "macd_divergence": ModelConfig(
        label="MACD底背离",
        min_candles=120,
        max_dist_support_pct=8.0,
        min_support_score=45,
        min_total_score=72,
        min_touch_count=1,
    ),
    # ─── New patterns (high-RR, US growth-stock playbooks) ───
    "stage2_breakout": ModelConfig(
        label="Stage 2 突破",
        min_candles=250,        # ~1 year for stable 30W MA slope
        max_dist_support_pct=8.0,
        min_support_score=40,
        min_total_score=60,
    ),
    "vcp": ModelConfig(
        label="VCP 波动收缩",
        min_candles=200,        # ~10 months for proper base + contractions
        max_dist_support_pct=5.0,
        min_support_score=40,
        min_total_score=60,
    ),
    "pivot_breakout": ModelConfig(
        label="Pivot 点突破",
        min_candles=180,        # ~9 months
        max_dist_support_pct=6.0,
        min_support_score=40,
        min_total_score=60,
    ),
    "cup_handle": ModelConfig(
        label="杯柄形态",
        min_candles=300,        # ~15 months (US standard: cup spans 7-65 weeks)
        max_dist_support_pct=6.0,
        min_support_score=40,
        min_total_score=60,
    ),
    "high_tight_flag": ModelConfig(
        label="高位紧旗",
        min_candles=120,
        max_dist_support_pct=10.0,
        min_support_score=30,
        min_total_score=65,
    ),
    "volume_breakout_resistance": ModelConfig(
        label="放量突破压力位",
        min_candles=120,
        max_dist_support_pct=15.0,
        min_support_score=0,
        min_total_score=55,
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
        levels = detect_levels_multifactor(candles, lookback=min(len(candles), 250))
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
# Model: stage2_breakout (Stan Weinstein Stage 2 突破)
# ─────────────────────────────────────────────────────────────────────
# 思路: 长期均线（MA150 模拟周线 MA30）拐头向上 + 价格站上 MA150 + 突破近 N 日新高 + 量能放大。
# 这是趋势跟随的入场信号，目标是抓 Stage 1→Stage 2 的转换点。

def detect_stage2_breakout(code: str, name: str, candles: list[Candle], weekly_candles: list[Candle] | None = None) -> ScreenerItem | None:
    cfg = SCREENER_CONFIG["stage2_breakout"]
    if not cfg.enabled or len(candles) < cfg.min_candles:
        return None

    closes = np.array([c.close for c in candles])
    highs = np.array([c.high for c in candles])
    volumes = np.array([c.volume for c in candles])
    cur = float(closes[-1])
    n = len(candles)

    # ── A) 长期均线状态: MA50 / MA150 / MA200 ──
    ma50 = float(np.mean(closes[-50:]))
    ma150 = float(np.mean(closes[-150:]))
    ma200_window = min(n, 200)
    ma200 = float(np.mean(closes[-ma200_window:]))

    # 价格必须在 MA50 + MA150 上方
    if cur < ma150 or cur < ma50:
        return None

    # MA50 > MA150 (短中期共振向上)
    if ma50 < ma150:
        return None

    # MA150 上行 (拐头): 与 30 日前的 MA150 比较
    if n < 180:
        return None
    ma150_30d_ago = float(np.mean(closes[-180:-30]))
    if ma150 < ma150_30d_ago:  # MA150 必须在过去 30 天上行
        return None

    # ── B) 突破近 N 日新高 (近 50 日内是新高) ──
    high50 = float(np.max(highs[-50:-1]))   # 不含今日
    breakout_pct = (cur - high50) / max(high50, 1)
    # -1% (即将突破) ~ +8% (刚突破不久)
    if breakout_pct < -0.01 or breakout_pct > 0.08:
        return None

    # ── C) 距 52 周高点不远 (Minervini Trend Template: 距 52 周高 < 25%) ──
    high52w = float(np.max(highs[-min(n, 250):]))
    dist_from_high = (high52w - cur) / max(high52w, 1)
    if dist_from_high > 0.25:
        return None

    # ── D) 距 52 周低点足够远 (>= 30%) ──
    low52w = float(np.min(closes[-min(n, 250):]))
    above_low = (cur - low52w) / max(low52w, 1)
    if above_low < 0.30:
        return None

    # ── E) 突破当天量能放大 (>= 1.5 倍 50日均量) ──
    avg_vol_50 = float(np.mean(volumes[-50:-1]))
    if avg_vol_50 <= 0:
        return None
    cur_vol = float(volumes[-1])
    vol_mult = cur_vol / avg_vol_50
    # 量能不一定要爆，但至少不能萎缩
    if vol_mult < 0.8:
        return None

    # ── SR 上下文 ──
    ctx = get_sr_context(code, candles, cur, weekly_candles)

    # ── 打分 ──
    score_trend = 25  # 通过了所有趋势模板检查
    score_breakout = 15 if breakout_pct >= 0 else 8  # 已突破 vs 即将突破
    score_volume = min((vol_mult - 0.8) * 15, 20)
    score_dist_high = max(0, (0.25 - dist_from_high) / 0.25) * 10
    score_above_low = min((above_low - 0.30) / 0.70 * 10, 10)
    weekly_bonus = 10 if ctx.weekly_trend.get("direction") == "up" else 5
    score_support = min(ctx.sup_score, 100) * 0.10 if ctx.sup_price else 0

    total = score_trend + score_breakout + score_volume + score_dist_high + score_above_low + weekly_bonus + score_support
    total = round(min(total, 100), 1)
    if total < cfg.min_total_score:
        return None

    triggers = [
        f"突破{50}日新高{high50:.2f} (+{breakout_pct*100:.1f}%)",
        f"MA50>MA150 多头排列",
        f"MA150 30日上行 ({(ma150/ma150_30d_ago-1)*100:+.1f}%)",
        f"距52周高{dist_from_high*100:.0f}% / 距52周低+{above_low*100:.0f}%",
    ]
    if vol_mult >= 1.5:
        triggers.append(f"放量突破 {vol_mult:.1f}x")
    if ctx.weekly_trend.get("direction") == "up":
        triggers.append("周线向上")

    last = candles[-1]
    change_pct = (last.close / candles[-2].close - 1) * 100 if n >= 2 else 0.0
    sup_for_rr = ctx.sup_price if ctx.sup_price else high50  # 突破回测位作止损
    target = cur + (cur - sup_for_rr) * 3  # Stage 2 目标 RR=3
    rr = _rr_ratio(cur, sup_for_rr, target)
    dist_pct = abs(cur - sup_for_rr) / sup_for_rr * 100

    return ScreenerItem(
        code=code, name=name, pattern="stage2_breakout",
        score=total, price=round(cur, 2),
        change_pct=round(change_pct, 2),
        volume_ratio=round(vol_mult, 2),
        breakout_price=round(high50, 2),
        pullback_price=round(sup_for_rr, 2),
        distance_to_support_pct=round(dist_pct, 2),
        triggers=triggers,
        rr_ratio=round(rr, 2),
        support_score=round(ctx.sup_score, 1),
    )


# ─────────────────────────────────────────────────────────────────────
# Model: vcp (Mark Minervini Volatility Contraction Pattern)
# ─────────────────────────────────────────────────────────────────────
# 思路: 在过去 60 天内有 2-4 次回撤，每次回撤幅度递减，量能也递减。
# 关键特征:
#   - 收缩序列: pullback1 > pullback2 > pullback3
#   - 量能干涸: 最近 5 日均量 < 50 日均量的 70%
#   - 现价贴近最后一段整理的高点 (pivot point) 准备突破

def _find_swing_points(highs: np.ndarray, lows: np.ndarray, window: int = 5) -> list[tuple[int, str, float]]:
    """找出局部高点(H)和局部低点(L)。返回按时间顺序: (idx, kind, price)。"""
    points = []
    n = len(highs)
    for i in range(window, n - window):
        if highs[i] == max(highs[i - window:i + window + 1]):
            points.append((i, "H", float(highs[i])))
        elif lows[i] == min(lows[i - window:i + window + 1]):
            points.append((i, "L", float(lows[i])))
    return points


def detect_vcp(code: str, name: str, candles: list[Candle], weekly_candles: list[Candle] | None = None) -> ScreenerItem | None:
    cfg = SCREENER_CONFIG["vcp"]
    if not cfg.enabled or len(candles) < cfg.min_candles:
        return None

    closes = np.array([c.close for c in candles])
    highs = np.array([c.high for c in candles])
    lows = np.array([c.low for c in candles])
    volumes = np.array([c.volume for c in candles])
    cur = float(closes[-1])
    n = len(candles)

    # ── A) 必须处于上升趋势 (MA50 > MA150) ──
    ma50 = float(np.mean(closes[-50:]))
    ma150 = float(np.mean(closes[-min(n, 150):]))
    if ma50 < ma150:
        return None
    if cur < ma50 * 0.95:  # 价格不能远低于 MA50
        return None

    # ── B) 在最近 60 天里寻找 swing points，识别收缩序列 ──
    look = min(n, 70)
    sub_highs = highs[-look:]
    sub_lows = lows[-look:]
    points = _find_swing_points(sub_highs, sub_lows, window=4)

    if len(points) < 4:  # 至少 2 个 H + 2 个 L
        return None

    # ── C) 提取连续的 (H, L) 回撤序列 ──
    contractions = []  # (high_price, low_price, pullback_pct)
    last_high = None
    for idx, kind, price in points:
        if kind == "H":
            last_high = price
        elif kind == "L" and last_high is not None and price < last_high:
            pb = (last_high - price) / last_high
            if pb >= 0.03:  # 至少 3% 才算一次有意义的回撤
                contractions.append((last_high, price, pb))
            last_high = None

    if len(contractions) < 2:
        return None

    # ── D) 收缩序列: 后一次回撤幅度 < 前一次的 80% ──
    last_n = contractions[-3:] if len(contractions) >= 3 else contractions[-2:]
    is_contracting = True
    for i in range(1, len(last_n)):
        if last_n[i][2] >= last_n[i - 1][2] * 0.85:
            is_contracting = False
            break
    if not is_contracting:
        return None

    final_pullback_pct = last_n[-1][2]
    if final_pullback_pct > 0.15:  # 最后一次回撤不能 >15%
        return None

    # ── E) 量能干涸: 最近 5 日均量 < 50 日均量的 75% ──
    avg_vol_50 = float(np.mean(volumes[-50:-5])) if n >= 55 else float(np.mean(volumes[-50:]))
    avg_vol_5 = float(np.mean(volumes[-5:]))
    if avg_vol_50 <= 0:
        return None
    vol_dryup_ratio = avg_vol_5 / avg_vol_50
    if vol_dryup_ratio > 0.75:
        return None

    # ── F) 现价贴近 pivot point (最后一次回撤前的高点) ──
    pivot = last_n[-1][0]
    dist_to_pivot = (pivot - cur) / max(pivot, 1)
    # 距 pivot < 8% (允许在 pivot 附近的整理或突破)
    if dist_to_pivot < -0.05 or dist_to_pivot > 0.08:
        return None

    # ── SR 上下文 ──
    ctx = get_sr_context(code, candles, cur, weekly_candles)

    # ── 打分 ──
    score_contraction = 25 + min(len(last_n) - 2, 2) * 5  # 2-4 次收缩加分
    score_pullback_size = max(0, (0.15 - final_pullback_pct) / 0.15) * 20  # 越小越好
    score_volume_dryup = max(0, (0.75 - vol_dryup_ratio) / 0.75) * 20
    score_pivot_proximity = max(0, (0.08 - abs(dist_to_pivot)) / 0.08) * 15
    score_trend = 10 if ma50 >= ma150 * 1.02 else 5
    weekly_bonus = 5 if ctx.weekly_trend.get("direction") == "up" else 0

    total = score_contraction + score_pullback_size + score_volume_dryup + score_pivot_proximity + score_trend + weekly_bonus
    total = round(min(total, 100), 1)
    if total < cfg.min_total_score:
        return None

    triggers = [
        f"{len(last_n)}次收缩 ({'/'.join(f'{c[2]*100:.0f}%' for c in last_n)})",
        f"量能干涸 5日/50日={vol_dryup_ratio:.0%}",
        f"距 pivot {pivot:.2f} {dist_to_pivot*100:+.1f}%",
    ]
    if ma50 >= ma150 * 1.02:
        triggers.append("MA50>MA150 强趋势")
    if ctx.weekly_trend.get("direction") == "up":
        triggers.append("周线向上")

    last = candles[-1]
    change_pct = (last.close / candles[-2].close - 1) * 100 if n >= 2 else 0.0
    stop = last_n[-1][1]  # 最后一次回撤的低点作止损
    target = pivot + (pivot - stop) * 2.5  # VCP 目标 RR=2.5+
    rr = _rr_ratio(cur, stop, target)
    dist_pct = abs(cur - stop) / stop * 100

    return ScreenerItem(
        code=code, name=name, pattern="vcp",
        score=total, price=round(cur, 2),
        change_pct=round(change_pct, 2),
        volume_ratio=round(vol_dryup_ratio, 2),
        breakout_price=round(pivot, 2),
        pullback_price=round(stop, 2),
        distance_to_support_pct=round(dist_pct, 2),
        triggers=triggers,
        rr_ratio=round(rr, 2),
        support_score=round(ctx.sup_score, 1),
    )


# ─────────────────────────────────────────────────────────────────────
# Model: pivot_breakout (William O'Neil Pivot Point Breakout)
# ─────────────────────────────────────────────────────────────────────
# 思路: 找一个 5-10 周的 base (高低差 <25%)，当前价格突破 base 高点 0~3%，量能放大。
# 与 stage2_breakout 区别: 不要求长期均线，但要求严格的 base 形态 + 突破当天量能。

def detect_pivot_breakout(code: str, name: str, candles: list[Candle], weekly_candles: list[Candle] | None = None) -> ScreenerItem | None:
    cfg = SCREENER_CONFIG["pivot_breakout"]
    if not cfg.enabled or len(candles) < cfg.min_candles:
        return None

    closes = np.array([c.close for c in candles])
    highs = np.array([c.high for c in candles])
    lows = np.array([c.low for c in candles])
    volumes = np.array([c.volume for c in candles])
    cur = float(closes[-1])
    n = len(candles)

    # ── 寻找最优 base 长度: 25-50 个交易日 (5-10 周) ──
    best_base = None  # (base_len, base_high, base_low, base_range_pct)
    for base_len in (25, 35, 50):
        if n < base_len + 5:
            continue
        base_highs = highs[-base_len-1:-1]  # 不含今日
        base_lows = lows[-base_len-1:-1]
        bh = float(np.max(base_highs))
        bl = float(np.min(base_lows))
        rng = (bh - bl) / max(bl, 1)
        if rng > 0.30:  # 整理太宽不算 base
            continue
        # 选择宽度最小的 base
        if best_base is None or rng < best_base[3]:
            best_base = (base_len, bh, bl, rng)

    if best_base is None:
        return None
    base_len, pivot_high, base_low, base_range = best_base

    # ── 现价必须 >= pivot_high * 0.99 (即将突破) 且 <= pivot_high * 1.05 (刚突破) ──
    breakout_pct = (cur - pivot_high) / max(pivot_high, 1)
    if breakout_pct < -0.01 or breakout_pct > 0.05:
        return None

    # ── 量能放大: 当日量 > 1.4x base 期间均量 ──
    avg_vol_base = float(np.mean(volumes[-base_len-1:-1]))
    if avg_vol_base <= 0:
        return None
    cur_vol = float(volumes[-1])
    vol_mult = cur_vol / avg_vol_base
    if vol_mult < 1.2:
        return None

    # ── base 的最后 1/3 不应该是大跌 (深 V 不算 base) ──
    last_third_low = float(np.min(lows[-base_len // 3:]))
    if (pivot_high - last_third_low) / pivot_high > 0.20:
        return None

    # ── 趋势确认: 现价应在 MA50 上方 ──
    if n >= 50:
        ma50 = float(np.mean(closes[-50:]))
        if cur < ma50:
            return None

    ctx = get_sr_context(code, candles, cur, weekly_candles)

    # ── 打分 ──
    score_base_quality = max(0, (0.30 - base_range) / 0.30) * 25  # 越窄越好
    score_breakout = 20 if breakout_pct >= 0 else 10
    score_volume = min((vol_mult - 1.0) * 15, 25)
    score_base_len = min(base_len / 50 * 10, 10)  # 越长越好
    weekly_bonus = 10 if ctx.weekly_trend.get("direction") == "up" else 5
    score_support = min(ctx.sup_score, 100) * 0.10 if ctx.sup_price else 0

    total = score_base_quality + score_breakout + score_volume + score_base_len + weekly_bonus + score_support
    total = round(min(total, 100), 1)
    if total < cfg.min_total_score:
        return None

    triggers = [
        f"突破 {base_len}日 base 高点 {pivot_high:.2f} ({breakout_pct*100:+.1f}%)",
        f"base 宽度 {base_range*100:.1f}%",
        f"放量 {vol_mult:.1f}x base 均量",
    ]
    if ctx.weekly_trend.get("direction") == "up":
        triggers.append("周线向上")

    last = candles[-1]
    change_pct = (last.close / candles[-2].close - 1) * 100 if n >= 2 else 0.0
    stop = base_low * 1.01  # base 低点为止损
    target = cur + (pivot_high - base_low)  # 目标 = 突破点 + base 高度
    rr = _rr_ratio(cur, stop, target)
    dist_pct = abs(cur - stop) / stop * 100

    return ScreenerItem(
        code=code, name=name, pattern="pivot_breakout",
        score=total, price=round(cur, 2),
        change_pct=round(change_pct, 2),
        volume_ratio=round(vol_mult, 2),
        breakout_price=round(pivot_high, 2),
        pullback_price=round(stop, 2),
        distance_to_support_pct=round(dist_pct, 2),
        triggers=triggers,
        rr_ratio=round(rr, 2),
        support_score=round(ctx.sup_score, 1),
    )


# ─────────────────────────────────────────────────────────────────────
# Model: cup_handle (William O'Neil Cup & Handle)
# ─────────────────────────────────────────────────────────────────────
# 思路: 找出 7-30 周 (35-150 日) 的圆弧底:
#   - 杯左沿 H1 (前期高点)
#   - 杯底 L (在 H1 之后，回撤 12-35%)
#   - 杯右沿 H2 (近期回升到接近 H1, 与 H1 差异 <8%)
#   - 然后 handle: 1-4 周浅回撤 (3-15%)，现价接近 handle 高点准备突破

def detect_cup_handle(code: str, name: str, candles: list[Candle], weekly_candles: list[Candle] | None = None) -> ScreenerItem | None:
    cfg = SCREENER_CONFIG["cup_handle"]
    if not cfg.enabled or len(candles) < cfg.min_candles:
        return None

    closes = np.array([c.close for c in candles])
    highs = np.array([c.high for c in candles])
    lows = np.array([c.low for c in candles])
    volumes = np.array([c.volume for c in candles])
    cur = float(closes[-1])
    n = len(candles)

    # ── handle 应该在最近 5-25 日 ──
    handle_len = 0
    handle_high_idx = None
    handle_high = None
    handle_low = None

    # handle 高点 = 最近 5-25 日内的某个局部高
    for hl in range(5, min(26, n - 30)):
        ph_idx = n - 1 - hl
        ph = float(highs[ph_idx])
        # 后续 hl 天没有创出新高
        if max(highs[ph_idx + 1:]) <= ph * 1.005:
            handle_len = hl
            handle_high_idx = ph_idx
            handle_high = ph
            handle_low = float(np.min(lows[ph_idx + 1:]))
            handle_pullback = (ph - handle_low) / ph
            if 0.03 <= handle_pullback <= 0.15:  # handle 浅回撤
                break
    else:
        return None

    if handle_high_idx is None or handle_high is None:
        return None

    # ── 杯部分: handle 之前的 30-130 个交易日 ──
    cup_search_start = max(0, handle_high_idx - 130)
    cup_segment_highs = highs[cup_search_start:handle_high_idx]
    cup_segment_lows = lows[cup_search_start:handle_high_idx]
    if len(cup_segment_highs) < 30:
        return None

    # 杯底 = cup segment 中的最低点
    cup_low_relative_idx = int(np.argmin(cup_segment_lows))
    cup_low = float(cup_segment_lows[cup_low_relative_idx])
    cup_low_idx = cup_search_start + cup_low_relative_idx

    # 杯左沿 = 杯底之前的最高点
    if cup_low_idx <= cup_search_start + 5:
        return None
    left_segment = highs[cup_search_start:cup_low_idx]
    cup_left_high = float(np.max(left_segment))

    # 杯右沿 = 杯底之后, handle 之前的最高点 (应该接近 handle_high)
    right_segment = highs[cup_low_idx:handle_high_idx + 1]
    cup_right_high = float(np.max(right_segment))

    # ── 形态校验 ──
    # 1. 杯深: 12-35%
    cup_depth = (cup_left_high - cup_low) / cup_left_high
    if not (0.12 <= cup_depth <= 0.40):
        return None

    # 2. 左右沿对称: 差异 <10%
    rim_diff = abs(cup_left_high - cup_right_high) / cup_left_high
    if rim_diff > 0.10:
        return None

    # 3. 杯长 (从左沿到右沿): 30-130 日
    cup_length = handle_high_idx - cup_search_start
    if not (30 <= cup_length <= 130):
        return None

    # 4. 现价靠近 handle_high (突破点)
    breakout_pct = (cur - handle_high) / max(handle_high, 1)
    if breakout_pct < -0.03 or breakout_pct > 0.05:
        return None

    # 5. 量能: 突破当日 > 1.3x handle 期间均量
    avg_vol_handle = float(np.mean(volumes[handle_high_idx + 1:])) if handle_len > 0 else 1
    cur_vol = float(volumes[-1])
    vol_mult = cur_vol / max(avg_vol_handle, 1)
    if vol_mult < 1.0:
        return None

    ctx = get_sr_context(code, candles, cur, weekly_candles)

    # ── 打分 ──
    score_symmetry = max(0, (0.10 - rim_diff) / 0.10) * 25
    score_depth = 20 if 0.15 <= cup_depth <= 0.30 else 10  # 理想杯深 15-30%
    score_handle_quality = max(0, (0.15 - (handle_high - handle_low) / handle_high) / 0.15) * 15
    score_breakout = 15 if breakout_pct >= 0 else 8
    score_volume = min((vol_mult - 1.0) * 10, 15)
    weekly_bonus = 10 if ctx.weekly_trend.get("direction") == "up" else 5

    total = score_symmetry + score_depth + score_handle_quality + score_breakout + score_volume + weekly_bonus
    total = round(min(total, 100), 1)
    if total < cfg.min_total_score:
        return None

    triggers = [
        f"杯深{cup_depth*100:.0f}% / 杯长{cup_length}日",
        f"左右沿对称 (差{rim_diff*100:.1f}%)",
        f"handle {handle_len}日浅回撤 {(handle_high-handle_low)/handle_high*100:.1f}%",
        f"现价距突破点 {handle_high:.2f} {breakout_pct*100:+.1f}%",
    ]
    if vol_mult >= 1.3:
        triggers.append(f"放量 {vol_mult:.1f}x")
    if ctx.weekly_trend.get("direction") == "up":
        triggers.append("周线向上")

    last = candles[-1]
    change_pct = (last.close / candles[-2].close - 1) * 100 if n >= 2 else 0.0
    stop = handle_low * 0.99  # handle 低点为止损
    target = handle_high + (handle_high - cup_low)  # 目标 = 突破点 + 杯深
    rr = _rr_ratio(cur, stop, target)
    dist_pct = abs(cur - stop) / stop * 100

    return ScreenerItem(
        code=code, name=name, pattern="cup_handle",
        score=total, price=round(cur, 2),
        change_pct=round(change_pct, 2),
        volume_ratio=round(vol_mult, 2),
        breakout_price=round(handle_high, 2),
        pullback_price=round(stop, 2),
        distance_to_support_pct=round(dist_pct, 2),
        triggers=triggers,
        rr_ratio=round(rr, 2),
        support_score=round(ctx.sup_score, 1),
    )


# ─────────────────────────────────────────────────────────────────────
# Model: high_tight_flag (高位紧旗 — 强爆发型)
# ─────────────────────────────────────────────────────────────────────
# 思路: 8-12 周内涨幅 >=80%，然后 3-5 周窄幅整理 (回撤 <25%)，现价突破整理上沿。
# 这是 RR 极高、胜率偏低的强爆发形态，最适合龙头股、热门题材。

def detect_high_tight_flag(code: str, name: str, candles: list[Candle], weekly_candles: list[Candle] | None = None) -> ScreenerItem | None:
    cfg = SCREENER_CONFIG["high_tight_flag"]
    if not cfg.enabled or len(candles) < cfg.min_candles:
        return None

    closes = np.array([c.close for c in candles])
    highs = np.array([c.high for c in candles])
    lows = np.array([c.low for c in candles])
    volumes = np.array([c.volume for c in candles])
    cur = float(closes[-1])
    n = len(candles)

    # ── A) 寻找近期整理段 (15-30 日) ──
    flag_high = None
    flag_low = None
    flag_len = 0
    flag_start_idx = None
    for fl in (15, 20, 25):
        if n < fl + 50:
            continue
        seg_highs = highs[-fl:]
        seg_lows = lows[-fl:]
        fh = float(np.max(seg_highs))
        fl_low = float(np.min(seg_lows))
        flag_range = (fh - fl_low) / max(fl_low, 1)
        # 旗形必须紧 (range <25%)
        if flag_range > 0.25:
            continue
        flag_high = fh
        flag_low = fl_low
        flag_len = fl
        flag_start_idx = n - fl
        break

    if flag_high is None or flag_start_idx is None or flag_low is None:
        return None

    # ── B) 旗杆: 整理段之前的 35-65 日内涨幅 >= 80% ──
    pole_search_start = max(0, flag_start_idx - 65)
    pole_search_end = flag_start_idx
    if pole_search_end - pole_search_start < 35:
        return None

    pole_low = float(np.min(lows[pole_search_start:pole_search_end]))
    pole_high = float(np.max(highs[pole_search_start:pole_search_end]))
    pole_gain = (pole_high - pole_low) / max(pole_low, 1)
    if pole_gain < 0.80:  # 旗杆涨幅 >= 80%
        return None

    # 旗杆高点应接近 flag 高点 (差异 <10%)
    if abs(pole_high - flag_high) / pole_high > 0.15:
        return None

    # ── C) 突破: 现价 >= flag_high * 0.98 ──
    breakout_pct = (cur - flag_high) / max(flag_high, 1)
    if breakout_pct < -0.02 or breakout_pct > 0.08:
        return None

    # ── D) 量能: 当日 > 1.5x 旗形期间均量 ──
    avg_vol_flag = float(np.mean(volumes[flag_start_idx:]))
    if avg_vol_flag <= 0:
        return None
    cur_vol = float(volumes[-1])
    vol_mult = cur_vol / avg_vol_flag
    if vol_mult < 1.3:
        return None

    ctx = get_sr_context(code, candles, cur, weekly_candles)

    # ── 打分 ──
    score_pole = min((pole_gain - 0.80) / 1.20 * 25 + 15, 30)  # 旗杆越强越好
    flag_pullback = (flag_high - flag_low) / flag_high
    score_flag_tightness = max(0, (0.25 - flag_pullback) / 0.25) * 20  # 旗越紧越好
    score_breakout = 15 if breakout_pct >= 0 else 8
    score_volume = min((vol_mult - 1.0) * 10, 20)
    weekly_bonus = 10 if ctx.weekly_trend.get("direction") == "up" else 5

    total = score_pole + score_flag_tightness + score_breakout + score_volume + weekly_bonus
    total = round(min(total, 100), 1)
    if total < cfg.min_total_score:
        return None

    triggers = [
        f"旗杆涨幅 {pole_gain*100:.0f}% (~{pole_search_end-pole_search_start}日)",
        f"旗形 {flag_len}日紧整理 (回撤{flag_pullback*100:.1f}%)",
        f"突破旗形上沿 {flag_high:.2f} ({breakout_pct*100:+.1f}%)",
        f"放量 {vol_mult:.1f}x 旗形均量",
    ]
    if ctx.weekly_trend.get("direction") == "up":
        triggers.append("周线向上")

    last = candles[-1]
    change_pct = (last.close / candles[-2].close - 1) * 100 if n >= 2 else 0.0
    stop = flag_low * 1.01
    # HTF 目标: 至少复制旗杆涨幅
    target = flag_high * (1 + pole_gain * 0.7)
    rr = _rr_ratio(cur, stop, target)
    dist_pct = abs(cur - stop) / stop * 100

    return ScreenerItem(
        code=code, name=name, pattern="high_tight_flag",
        score=total, price=round(cur, 2),
        change_pct=round(change_pct, 2),
        volume_ratio=round(vol_mult, 2),
        breakout_price=round(flag_high, 2),
        pullback_price=round(stop, 2),
        distance_to_support_pct=round(dist_pct, 2),
        triggers=triggers,
        rr_ratio=round(rr, 2),
        support_score=round(ctx.sup_score, 1),
    )


# ─────────────────────────────────────────────────────────────────────
# Model: ma_support (均线支撑) — 回踩 MA20/MA60 附近获支撑
# ─────────────────────────────────────────────────────────────────────

def detect_ma_support(code: str, name: str, candles: list[Candle],
                      weekly_candles: list[Candle] | None = None) -> ScreenerItem | None:
    """价格回踩 MA20 或 MA60 附近（±3%）并站稳，同时高于更长均线。"""
    n = len(candles)
    if n < 60:
        return None
    closes = np.array([c.close for c in candles], dtype=float)
    volumes = np.array([c.volume for c in candles], dtype=float)
    cur = float(closes[-1])

    ma20 = float(np.mean(closes[-20:]))
    ma60 = float(np.mean(closes[-60:]))

    triggers = []
    score = 0.0

    # 趋势前提: MA20 > MA60（或至少持平）
    if ma20 < ma60 * 0.97:
        return None

    # 价格应在 MA60 之上
    if cur < ma60 * 0.97:
        return None

    # 判断回踩到哪条均线
    dist_ma20 = (cur - ma20) / ma20
    dist_ma60 = (cur - ma60) / ma60

    touched_ma20 = -0.03 <= dist_ma20 <= 0.03
    touched_ma60 = -0.03 <= dist_ma60 <= 0.05

    if not touched_ma20 and not touched_ma60:
        return None

    # 近 5 日有至少 1 日最低价碰到均线
    lows_5 = np.array([c.low for c in candles[-5:]], dtype=float)
    touched_low_ma20 = any(abs(l - ma20) / ma20 < 0.02 for l in lows_5)
    touched_low_ma60 = any(abs(l - ma60) / ma60 < 0.02 for l in lows_5)
    if not touched_low_ma20 and not touched_low_ma60:
        return None

    # 今日收盘应站在均线之上或附近
    if cur < min(ma20, ma60) * 0.98:
        return None

    # 量能: 缩量回踩更好
    vol_avg20 = float(np.mean(volumes[-20:])) if np.any(volumes[-20:]) else 1
    vol_recent = float(np.mean(volumes[-3:])) if np.any(volumes[-3:]) else 1
    vol_ratio = vol_recent / vol_avg20 if vol_avg20 > 0 else 1.0

    # 评分
    if touched_ma20 or touched_low_ma20:
        triggers.append(f"回踩 MA20({ma20:.2f}) 获支撑")
        score += 30
    if touched_ma60 or touched_low_ma60:
        triggers.append(f"回踩 MA60({ma60:.2f}) 获支撑")
        score += 25

    if ma20 > ma60:
        triggers.append("MA20 > MA60 多头排列")
        score += 15
    if cur > ma20:
        triggers.append(f"站上 MA20")
        score += 10

    if vol_ratio < 0.8:
        triggers.append(f"缩量回踩 (量比{vol_ratio:.2f})")
        score += 10
    elif vol_ratio < 1.2:
        score += 5

    # 距 60 日高点不太远（还在趋势中）
    hi60 = float(np.max([c.high for c in candles[-60:]]))
    from_hi = (cur - hi60) / hi60
    if from_hi > -0.15:
        score += 10
        triggers.append(f"距60日高点 {from_hi*100:.1f}%")

    score = round(min(score, 100), 1)
    if score < 40:
        return None

    change_pct = (closes[-1] / closes[-2] - 1) * 100 if n >= 2 else 0
    stop = min(ma20, ma60) * 0.97
    target = hi60
    rr = _rr_ratio(cur, stop, target)
    dist_pct = (cur - stop) / stop * 100

    return ScreenerItem(
        code=code, name=name, pattern="ma_support",
        score=score, price=round(cur, 2),
        change_pct=round(change_pct, 2),
        volume_ratio=round(vol_ratio, 2),
        breakout_price=round(hi60, 2),
        pullback_price=round(stop, 2),
        distance_to_support_pct=round(dist_pct, 2),
        triggers=triggers,
        rr_ratio=round(rr, 2),
        support_score=0,
    )


# ─────────────────────────────────────────────────────────────────────
# Model: volume_shrink_consolidation (缩量整理) — 涨后缩量横盘
# ─────────────────────────────────────────────────────────────────────

def detect_volume_shrink_consolidation(code: str, name: str, candles: list[Candle],
                                       weekly_candles: list[Candle] | None = None) -> ScreenerItem | None:
    """前期上涨后近 10-20 日缩量横盘整理，振幅收窄，等待突破。"""
    n = len(candles)
    if n < 60:
        return None
    closes = np.array([c.close for c in candles], dtype=float)
    highs = np.array([c.high for c in candles], dtype=float)
    lows = np.array([c.low for c in candles], dtype=float)
    volumes = np.array([c.volume for c in candles], dtype=float)
    cur = float(closes[-1])

    # 前期上涨: 30-60 日前到 10 日前涨幅 >= 15%
    base_price = float(np.min(lows[-60:-10])) if n >= 60 else float(np.min(lows[:-10]))
    peak_price = float(np.max(highs[-30:-5]))
    prior_gain = (peak_price - base_price) / base_price if base_price > 0 else 0
    if prior_gain < 0.15:
        return None

    # 近 10 日整理：振幅 <= 10%
    recent_high = float(np.max(highs[-10:]))
    recent_low = float(np.min(lows[-10:]))
    consolidation_range = (recent_high - recent_low) / recent_low if recent_low > 0 else 1
    if consolidation_range > 0.10:
        return None

    # 缩量: 近 10 日均量 < 前 20 日均量 * 0.7
    vol_recent = float(np.mean(volumes[-10:])) if np.any(volumes[-10:]) else 1
    vol_prior = float(np.mean(volumes[-30:-10])) if np.any(volumes[-30:-10]) else 1
    vol_ratio = vol_recent / vol_prior if vol_prior > 0 else 1
    if vol_ratio > 0.85:
        return None

    # 现价不能跌太多(应在整理区间上半部分)
    mid = (recent_high + recent_low) / 2
    if cur < mid:
        return None

    triggers = [
        f"前期涨幅 {prior_gain*100:.0f}%",
        f"近10日横盘整理 (振幅{consolidation_range*100:.1f}%)",
        f"缩量至 {vol_ratio:.0%} (相比前20日)",
    ]
    score = 0.0
    score += min(prior_gain / 0.5 * 25, 30)       # 涨幅越大越好
    score += (0.10 - consolidation_range) / 0.10 * 20  # 越窄越好
    score += (0.85 - vol_ratio) / 0.85 * 20        # 越缩越好
    score += 15 if cur >= recent_high * 0.98 else 5  # 接近上沿更好

    if cur >= recent_high * 0.98:
        triggers.append("逼近整理上沿")

    ma20 = float(np.mean(closes[-20:]))
    if cur > ma20:
        score += 10
        triggers.append("站上 MA20")

    score = round(min(score, 100), 1)
    if score < 35:
        return None

    change_pct = (closes[-1] / closes[-2] - 1) * 100 if n >= 2 else 0
    stop = recent_low * 0.98
    target = recent_high * (1 + prior_gain * 0.5)
    rr = _rr_ratio(cur, stop, target)
    dist_pct = (cur - stop) / stop * 100

    return ScreenerItem(
        code=code, name=name, pattern="volume_shrink_consolidation",
        score=score, price=round(cur, 2),
        change_pct=round(change_pct, 2),
        volume_ratio=round(vol_ratio, 2),
        breakout_price=round(recent_high, 2),
        pullback_price=round(stop, 2),
        distance_to_support_pct=round(dist_pct, 2),
        triggers=triggers,
        rr_ratio=round(rr, 2),
        support_score=0,
    )


# ─────────────────────────────────────────────────────────────────────
# Model: trend_strong (强势趋势) — 多头排列 + 站稳均线
# ─────────────────────────────────────────────────────────────────────

def detect_trend_strong(code: str, name: str, candles: list[Candle],
                        weekly_candles: list[Candle] | None = None) -> ScreenerItem | None:
    """MA10 > MA20 > MA60, 价格在 MA10 之上, 且近 20 日创新高或接近新高。
    这是最宽松的"可以进"的形态，只要趋势向上就标记。"""
    n = len(candles)
    if n < 60:
        return None
    closes = np.array([c.close for c in candles], dtype=float)
    highs = np.array([c.high for c in candles], dtype=float)
    volumes = np.array([c.volume for c in candles], dtype=float)
    cur = float(closes[-1])

    ma10 = float(np.mean(closes[-10:]))
    ma20 = float(np.mean(closes[-20:]))
    ma60 = float(np.mean(closes[-60:]))

    # 多头排列: MA10 > MA20 > MA60
    if not (ma10 > ma20 > ma60):
        return None

    # 价格在 MA20 之上
    if cur < ma20 * 0.99:
        return None

    triggers = [f"多头排列 MA10({ma10:.2f}) > MA20({ma20:.2f}) > MA60({ma60:.2f})"]
    score = 40.0  # 基础分

    # 价格在 MA10 之上: 额外加分
    if cur >= ma10:
        score += 10
        triggers.append("站上 MA10")

    # MA20 斜率向上 (对比 5 日前的 MA20)
    if n >= 25:
        ma20_5ago = float(np.mean(closes[-25:-5]))
        if ma20 > ma20_5ago:
            slope_pct = (ma20 - ma20_5ago) / ma20_5ago * 100
            score += min(slope_pct * 3, 15)
            triggers.append(f"MA20 上升 {slope_pct:.1f}%/周")

    # 距 60 日高点
    hi60 = float(np.max(highs[-60:]))
    from_hi = (cur - hi60) / hi60
    if from_hi > -0.05:
        score += 15
        triggers.append(f"接近60日新高 ({from_hi*100:+.1f}%)")
    elif from_hi > -0.10:
        score += 8
        triggers.append(f"距60日高点 {from_hi*100:.1f}%")

    # 量价配合: 近 5 日量能不萎缩
    vol_avg20 = float(np.mean(volumes[-20:])) if np.any(volumes[-20:]) else 1
    vol_recent = float(np.mean(volumes[-5:])) if np.any(volumes[-5:]) else 1
    vol_ratio = vol_recent / vol_avg20 if vol_avg20 > 0 else 1
    if vol_ratio >= 0.8:
        score += 10
    if vol_ratio >= 1.2:
        triggers.append(f"放量 (量比{vol_ratio:.2f})")

    # 周线趋势加分
    if weekly_candles and len(weekly_candles) >= 10:
        wc = [c.close for c in weekly_candles]
        wma5 = np.mean(wc[-5:])
        wma10 = np.mean(wc[-10:])
        if wma5 > wma10:
            score += 10
            triggers.append("周线趋势向上")

    score = round(min(score, 100), 1)
    if score < 45:
        return None

    change_pct = (closes[-1] / closes[-2] - 1) * 100 if n >= 2 else 0
    stop = ma20 * 0.97
    target = hi60 * 1.1
    rr = _rr_ratio(cur, stop, target)
    dist_pct = (cur - stop) / stop * 100

    return ScreenerItem(
        code=code, name=name, pattern="trend_strong",
        score=score, price=round(cur, 2),
        change_pct=round(change_pct, 2),
        volume_ratio=round(vol_ratio, 2),
        breakout_price=round(hi60, 2),
        pullback_price=round(stop, 2),
        distance_to_support_pct=round(dist_pct, 2),
        triggers=triggers,
        rr_ratio=round(rr, 2),
        support_score=0,
    )


# ─────────────────────────────────────────────────────────────────────
# Model: volume_breakout_resistance (放量突破压力位)
# ─────────────────────────────────────────────────────────────────────

def detect_volume_breakout_resistance(
    code: str, name: str, candles: list[Candle],
    weekly_candles: list[Candle] | None = None,
) -> ScreenerItem | None:
    """Detect stocks that just broke above a key resistance level on heavy volume.

    Logic:
    1. Compute SR levels using *yesterday's* close so resistance levels
       that were overhead are still classified as resistance.
    2. Check if the latest close crossed above one of those resistance levels.
    3. Require volume ≥ 1.5× the 20-day average (放量确认).
    4. Score based on resistance strength, volume ratio, MA alignment,
       breakout magnitude, and weekly trend.
    """
    cfg = SCREENER_CONFIG["volume_breakout_resistance"]
    if not cfg.enabled or len(candles) < cfg.min_candles:
        return None

    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    volumes = [c.volume for c in candles]
    cur = closes[-1]
    prev_close = closes[-2]
    n = len(candles)

    # ── 1) Find resistance levels using yesterday's close ──
    try:
        levels = detect_levels_multifactor(candles[:-1], lookback=min(n - 1, 250))
    except Exception:
        return None

    resistances = [
        lv for lv in levels
        if lv.kind == "resistance" and lv.price > prev_close * 0.99
    ]
    if not resistances:
        return None

    # Sort by price ascending → find the nearest STRONG resistance that was just broken
    # Only consider resistances with score ≥ 50; weak ones are not meaningful breakouts
    resistances.sort(key=lambda lv: lv.price)
    broken = None
    for r in resistances:
        if r.score < 50:
            continue  # skip weak resistance
        # Today's close above resistance, yesterday's close was below or near
        if cur >= r.price and prev_close < r.price * 1.005:
            broken = r
            break  # first (nearest) strong broken resistance

    if broken is None:
        return None

    # ── 2) Volume surge: today's volume ≥ 1.5× 20-day average ──
    if n < 22:
        return None
    avg_vol_20 = float(np.mean(volumes[-21:-1]))
    if avg_vol_20 <= 0:
        return None
    vol_ratio = float(volumes[-1] / avg_vol_20)
    if vol_ratio < 1.5:
        return None

    # ── 3) Not a gap-up dump (last bar must close in upper half of range) ──
    last = candles[-1]
    bar_range = last.high - last.low
    if bar_range > 0 and (last.close - last.low) / bar_range < 0.35:
        return None  # closed near low → suspect

    # ── 4) Big red candle filter ──
    last_pct = (last.close - last.open) / max(last.open, 1)
    if last_pct < -0.02:
        return None

    # ── 5) MA context ──
    ma5 = sum(closes[-5:]) / 5
    ma10 = sum(closes[-10:]) / 10
    ma20 = sum(closes[-20:]) / 20
    ma_aligned = ma5 >= ma10 >= ma20
    above_ma20 = cur > ma20

    # ── 6) Weekly trend ──
    wtrend = _weekly_trend_context(weekly_candles)
    weekly_down = wtrend["direction"] == "down"

    # ── 7) Breakout magnitude ──
    breakout_pct = (cur - broken.price) / broken.price

    # ── 8) Support below (nice to have) ──
    supports = [lv for lv in levels if lv.kind == "support" and lv.price < cur]
    sup_price = max(supports, key=lambda lv: lv.price).price if supports else None
    sup_score_val = max(supports, key=lambda lv: lv.price).score if supports else 0

    # ── Scoring ──
    triggers: list[str] = []

    # Resistance strength (0-25): stronger resistance = more significant breakout
    res_strength = min(broken.score, 100)
    score_resistance = res_strength * 0.25
    triggers.append(f"突破压力位 {broken.price:.2f}（强度{res_strength:.0f}）")

    # Volume component (0-25)
    score_volume = min((vol_ratio - 1.0) * 20, 25)
    triggers.append(f"放量{vol_ratio:.1f}倍（当日/20日均量）")

    # Breakout magnitude (0-15): 1-5% sweet spot
    if breakout_pct >= 0.05:
        score_breakout = 12
    elif breakout_pct >= 0.02:
        score_breakout = 15
    elif breakout_pct >= 0.005:
        score_breakout = 10
    else:
        score_breakout = 5
    triggers.append(f"突破幅度 {breakout_pct * 100:.1f}%")

    # MA alignment (0-15)
    if ma_aligned:
        score_ma = 15
        triggers.append(f"均线多头排列 MA5({ma5:.2f})>MA10({ma10:.2f})>MA20({ma20:.2f})")
    elif above_ma20:
        score_ma = 8
        triggers.append(f"站上MA20({ma20:.2f})")
    else:
        score_ma = 0

    # Weekly trend (0-10)
    if wtrend["direction"] == "up":
        weekly_bonus = 10
        triggers.append("周线趋势向上")
    elif not weekly_down:
        weekly_bonus = 5
    else:
        weekly_bonus = 0

    # Support safety net (0-10)
    if sup_price and sup_price > cur * 0.9:
        score_support = min(sup_score_val * 0.10, 10)
        triggers.append(f"下方支撑 {sup_price:.2f}")
    else:
        score_support = 0

    # ── 9) Check room to next STRONG resistance ──
    # Only consider resistances with score ≥ 50 as real blockers;
    # weak ones (< 50) are easily broken and shouldn't block the signal.
    next_res_strong = [lv for lv in resistances if lv.price > cur and lv.score >= 50]
    next_res = [lv for lv in resistances if lv.price > cur]
    blocker = next_res_strong[0] if next_res_strong else None
    if blocker:
        room_pct = (blocker.price - cur) / cur
        if room_pct < 0.01:
            return None
        elif room_pct < 0.03:
            score_room = -15
            triggers.append(f"⚠ 距强压力位 {blocker.price:.2f}（{blocker.score:.0f}分）仅 {room_pct*100:.1f}%")
        elif room_pct < 0.05:
            score_room = -5
            triggers.append(f"距强压力位 {blocker.price:.2f}（{room_pct*100:.1f}%）")
        else:
            score_room = 5
            triggers.append(f"上方空间至 {blocker.price:.2f}（{room_pct*100:.1f}%）")
    else:
        score_room = 10
        triggers.append("上方无强压力位")

    total_score = score_resistance + score_volume + score_breakout + score_ma + weekly_bonus + score_support + score_room
    total_score = round(min(max(total_score, 0), 100), 1)
    if total_score < cfg.min_total_score:
        return None

    # ── Result ──
    change_pct = (cur / prev_close - 1) * 100
    stop = sup_price * 0.98 if sup_price else broken.price * 0.97
    target = next_res[0].price if next_res else cur * 1.10
    rr = _rr_ratio(cur, stop, target)
    dist_pct = (cur - stop) / stop * 100 if stop > 0 else 0

    return ScreenerItem(
        code=code, name=name, pattern="volume_breakout_resistance",
        score=total_score, price=round(cur, 2),
        change_pct=round(change_pct, 2),
        volume_ratio=round(vol_ratio, 2),
        breakout_price=round(broken.price, 2),
        pullback_price=round(sup_price, 2) if sup_price else None,
        distance_to_support_pct=round(dist_pct, 2),
        triggers=triggers,
        rr_ratio=round(rr, 2),
        support_score=round(sup_score_val, 1),
    )


# ─────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────

PATTERN_DETECTORS = {
    "breakout_pullback": detect_breakout_pullback,
    "macd_divergence": detect_macd_divergence,
    "stage2_breakout": detect_stage2_breakout,
    "vcp": detect_vcp,
    "pivot_breakout": detect_pivot_breakout,
    "cup_handle": detect_cup_handle,
    "high_tight_flag": detect_high_tight_flag,
    "ma_support": detect_ma_support,
    "volume_shrink_consolidation": detect_volume_shrink_consolidation,
    "trend_strong": detect_trend_strong,
    "volume_breakout_resistance": detect_volume_breakout_resistance,
}

MODEL_LABELS = {
    "breakout_pullback": "突破回踩",
    "macd_divergence": "MACD底背离",
    "stage2_breakout": "Stage 2 突破",
    "vcp": "VCP 波动收缩",
    "pivot_breakout": "Pivot 点突破",
    "cup_handle": "杯柄形态",
    "high_tight_flag": "高位紧旗",
    "ma_support": "均线支撑",
    "volume_shrink_consolidation": "缩量整理",
    "trend_strong": "强势趋势",
    "volume_breakout_resistance": "放量突破压力位",
}
