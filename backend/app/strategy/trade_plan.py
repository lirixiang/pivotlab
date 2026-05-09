"""Build a concrete trade plan (buy zone, stop, targets, position size).

Given a stock's recent candles + SR levels + chosen style, output an
actionable plan that a retail user can place orders from.

Design principles:
  * Stops are based on ATR + nearest support, whichever is closer to the
    current price (避免止损过远).
  * Targets use risk-reward: TP1 = entry + 1.5R, TP2 = entry + 3R, capped
    at the next strong resistance.
  * Position sizing follows the "fixed-fractional" rule: risk no more than
    `risk_per_trade_pct` of total capital on stop-out.
  * Holding window is style-dependent.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Iterable

from ..schemas import Candle, Level
from .features import FeatureSet, atr as _atr_calc


# ── Style configuration ──────────────────────────────────────
STYLE_CONFIG: dict[str, dict] = {
    "short_term": {
        "atr_stop_mult": 1.2,
        "tp1_r": 1.5,
        "tp2_r": 2.5,
        "max_position_pct": 0.20,
        "risk_per_trade_pct": 0.015,   # risk 1.5% of total capital
        "holding_days_min": 1,
        "holding_days_max": 5,
        "buy_zone_atr_mult": 0.4,      # buy zone width = 0.4 * ATR
    },
    "swing": {
        "atr_stop_mult": 2.0,
        "tp1_r": 2.0,
        "tp2_r": 3.5,
        "max_position_pct": 0.25,
        "risk_per_trade_pct": 0.02,
        "holding_days_min": 5,
        "holding_days_max": 25,
        "buy_zone_atr_mult": 0.6,
    },
    "value": {
        "atr_stop_mult": 3.0,
        "tp1_r": 2.5,
        "tp2_r": 5.0,
        "max_position_pct": 0.30,
        "risk_per_trade_pct": 0.025,
        "holding_days_min": 20,
        "holding_days_max": 120,
        "buy_zone_atr_mult": 1.0,
    },
    "multi_factor": {
        "atr_stop_mult": 2.0,
        "tp1_r": 2.0,
        "tp2_r": 3.5,
        "max_position_pct": 0.15,       # diversified, smaller per-name
        "risk_per_trade_pct": 0.015,
        "holding_days_min": 10,
        "holding_days_max": 30,
        "buy_zone_atr_mult": 0.6,
    },
    # AI ensemble: same risk envelope as swing, but the recommender will
    # multiply position size by an RL-derived factor in [0.5, 1.5].
    "ai_ensemble": {
        "atr_stop_mult": 2.0,
        "tp1_r": 2.2,
        "tp2_r": 4.0,
        "max_position_pct": 0.20,
        "risk_per_trade_pct": 0.02,
        "holding_days_min": 5,
        "holding_days_max": 30,
        "buy_zone_atr_mult": 0.6,
    },
}


@dataclass
class TradePlanData:
    style: str
    buy_low: float
    buy_high: float
    buy_trigger: str
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    position_pct: float
    holding_days_min: int
    holding_days_max: int
    risk_reward: float
    atr_pct: float
    confidence: float
    reason: str
    factors: dict
    # New: tradability state
    state: str = "buy"          # "buy" | "wait_breakout" | "wait_pullback" | "reject"
    tradable: bool = True       # False = do not place an order today
    risk_warning: str = ""      # human-readable warning, empty if none

    def to_dict(self) -> dict:
        return asdict(self)


def _nearest_support(price: float, levels: list[Level], min_gap_pct: float = 0.5) -> float | None:
    """Return strongest support price below current price (with a min gap)."""
    cands = [
        L for L in levels
        if L.kind == "support" and L.price < price * (1 - min_gap_pct / 100)
    ]
    if not cands:
        return None
    cands.sort(key=lambda L: (-L.score, -L.price))  # high score, then nearest
    return float(cands[0].price)


def _nearest_resistance(price: float, levels: list[Level], min_gap_pct: float = 0.5) -> float | None:
    cands = [
        L for L in levels
        if L.kind == "resistance" and L.price > price * (1 + min_gap_pct / 100)
    ]
    if not cands:
        return None
    cands.sort(key=lambda L: (-L.score, L.price))
    return float(cands[0].price)


def build_trade_plan(
    *,
    style: str,
    candles: list[Candle],
    levels: list[Level],
    fs: FeatureSet,
    score: float,
    reasons: Iterable[str] = (),
    position_mult: float = 1.0,
) -> TradePlanData:
    """Build a TradePlanData for the latest bar."""
    cfg = STYLE_CONFIG.get(style, STYLE_CONFIG["swing"])
    price = fs.price
    atr14 = fs.atr14 if fs.atr14 > 0 else max(price * 0.02, 0.01)

    # ── Buy zone ──
    # Default: just below current price (回踩入场), centred on max(MA10, support)
    support = _nearest_support(price, levels)
    resistance = _nearest_resistance(price, levels)

    # ── EARLY EXIT: too close to resistance → "wait" plan, no order today ──
    # Use ATR-relative threshold so volatile stocks get more breathing room.
    atr_pct_val = max(fs.atr_pct, 0.01)
    if resistance is not None:
        dist_pct = (resistance - price) / price
        # Need at least 1*ATR room above current price; otherwise wait.
        if dist_pct < atr_pct_val * 1.0 and style != "value":
            trig = f"等突破 {resistance:.2f} (距默认{dist_pct*100:.1f}%)"
            warning = f"当前价 {price:.2f} 距阻力 {resistance:.2f} 仅 {dist_pct*100:.1f}%，不建议现价买入"
            return TradePlanData(
                style=style,
                buy_low=round(resistance * 1.005, 3),
                buy_high=round(resistance * 1.02, 3),
                buy_trigger=trig,
                stop_loss=round(resistance * 0.97, 3),
                take_profit_1=round(resistance * 1.08, 3),
                take_profit_2=round(resistance * 1.15, 3),
                position_pct=0.0,
                holding_days_min=cfg["holding_days_min"],
                holding_days_max=cfg["holding_days_max"],
                risk_reward=0.0,
                atr_pct=round(fs.atr_pct * 100, 2),
                confidence=round(score * 0.6, 1),
                reason=f"接近压力 {resistance:.2f}，等突破后跟进 · 预计买入区 {resistance*1.005:.2f}-{resistance*1.02:.2f}",
                factors={
                    "support": support,
                    "resistance": resistance,
                    "atr14": atr14,
                    "dist_to_resistance_pct": dist_pct * 100,
                    "trigger": trig,
                },
                state="wait_breakout",
                tradable=False,
                risk_warning=warning,
            )

    buy_zone_half = atr14 * cfg["buy_zone_atr_mult"] / 2
    if style == "short_term" and fs.is_breakout_20:
        # 突破型: 等回踩突破位附近
        anchor = max(fs.ma10, price * 0.98)
        trigger = "突破后回踩MA10/前高"
    elif style == "value" and support:
        anchor = support * 1.005
        trigger = f"回踩强支撑 {support:.2f}"
    elif fs.is_pullback_to_ma10:
        anchor = fs.ma10
        trigger = "回踩MA10企稳"
    elif support and (price - support) / price < 0.04:
        anchor = (price + support) / 2
        trigger = f"贴近支撑 {support:.2f}"
    else:
        anchor = price * 0.99
        trigger = "现价小幅回落"
    buy_low = max(anchor - buy_zone_half, price * 0.92)
    buy_high = min(anchor + buy_zone_half, price * 1.02)
    # Tighten buy_high so we never enter within 0.5*ATR of resistance.
    if resistance is not None:
        cap = resistance - atr14 * 0.5
        if cap < buy_high:
            buy_high = max(cap, buy_low + atr14 * 0.1)
    # Guarantee buy_low <= buy_high (anchor may sit outside the [0.92, 1.02] band)
    if buy_low > buy_high:
        mid = (buy_low + buy_high) / 2
        buy_low, buy_high = mid - buy_zone_half / 2, mid + buy_zone_half / 2
    buy_mid = (buy_low + buy_high) / 2

    # ── Stop loss ──
    atr_stop = buy_mid - atr14 * cfg["atr_stop_mult"]
    if support:
        # Use whichever is HIGHER (tighter): protects max draw
        stop = max(atr_stop, support * 0.985)
    else:
        stop = atr_stop
    # Sanity: stop must be below buy_mid by at least 1.5%
    stop = min(stop, buy_mid * 0.985)

    # ── Take profits ──
    risk_per_share = max(buy_mid - stop, price * 0.005)
    tp1 = buy_mid + risk_per_share * cfg["tp1_r"]
    tp2 = buy_mid + risk_per_share * cfg["tp2_r"]
    # Cap at strong resistance if it's meaningfully ABOVE buy_mid (≥1% gap).
    # Anything closer than that is treated as not-yet-broken intraday noise
    # and we keep the natural target.
    min_tp_gap = buy_mid * 1.01
    if resistance and resistance >= min_tp_gap:
        if resistance < tp1:
            tp1 = resistance * 0.995
            tp2 = max(tp2, resistance * 1.02)
        elif resistance < tp2:
            tp2 = resistance * 0.995
    # Final safety: TP1 must always exceed buy_high
    tp1 = max(tp1, buy_high * 1.01)
    tp2 = max(tp2, tp1 * 1.05)

    rr = (tp1 - buy_mid) / risk_per_share if risk_per_share > 0 else 0.0

    # ── Position sizing (fixed-fractional) ──
    # position_pct (of total capital) such that loss-on-stop = risk_per_trade_pct
    stop_loss_pct = (buy_mid - stop) / buy_mid if buy_mid > 0 else 0.05
    if stop_loss_pct > 0:
        size = cfg["risk_per_trade_pct"] / stop_loss_pct
    else:
        size = cfg["max_position_pct"] / 2
    # Cap by score (low score → smaller size)
    size = size * max(0.4, min(score / 100, 1.0))
    # RL-derived multiplier (only meaningful when caller passes it)
    size = size * float(position_mult)
    size = max(0.02, min(size, cfg["max_position_pct"]))

    confidence = min(100.0, score * (1.0 + min(rr, 4.0) / 10))

    # ── Reason text ──
    parts = list(reasons)
    parts.append(f"入场区 {buy_low:.2f}-{buy_high:.2f},触发: {trigger}")
    parts.append(
        f"止损 {stop:.2f} (-{stop_loss_pct*100:.1f}%),"
        f"目标 {tp1:.2f}/{tp2:.2f},盈亏比 {rr:.1f}"
    )
    parts.append(f"建议仓位 {size*100:.0f}%,持有 {cfg['holding_days_min']}-{cfg['holding_days_max']} 天")
    reason = " · ".join(parts)

    factors = {
        "support": support,
        "resistance": resistance,
        "atr14": atr14,
        "buy_mid": buy_mid,
        "stop_loss_pct": stop_loss_pct * 100,
        "risk_per_share": risk_per_share,
        "trigger": trigger,
        "position_mult": float(position_mult),
    }

    # ── Final tradability check ──
    state = "buy"
    tradable = True
    warn_parts: list[str] = []
    # Reject if rr too low — leaving the user with bad expected value
    min_rr = 1.2 if style != "short_term" else 1.0
    if rr < min_rr:
        state = "reject"
        tradable = False
        warn_parts.append(f"盈亏比 {rr:.1f} < {min_rr},期望值不足")
    # Friday weekend gap warning for short-term
    if style == "short_term" and fs.is_friday:
        warn_parts.append("周五下单将持仓过周末")
    # Holiday warning
    if fs.days_to_holiday <= 1:
        warn_parts.append(f"距假期{fs.days_to_holiday}日")
    risk_warning = " · ".join(warn_parts)

    return TradePlanData(
        style=style,
        buy_low=round(buy_low, 3),
        buy_high=round(buy_high, 3),
        buy_trigger=trigger,
        stop_loss=round(stop, 3),
        take_profit_1=round(tp1, 3),
        take_profit_2=round(tp2, 3),
        position_pct=round(size if tradable else 0.0, 3),
        holding_days_min=cfg["holding_days_min"],
        holding_days_max=cfg["holding_days_max"],
        risk_reward=round(rr, 2),
        atr_pct=round(fs.atr_pct * 100, 2),
        confidence=round(confidence, 1),
        reason=reason,
        factors=factors,
        state=state,
        tradable=tradable,
        risk_warning=risk_warning,
    )
