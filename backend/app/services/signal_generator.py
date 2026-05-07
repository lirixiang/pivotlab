"""Signal generator — produces actionable entry/exit recommendations.

Given a stock's current candles + a BacktestConfig (possibly optimised),
scans the last few bars for live entry signals and computes concrete:
  - entry_price (current close or limit order price)
  - stop_loss_price (based on nearest support / ATR / fixed %)
  - target_price (based on nearest resistance / fixed %)
  - risk_reward ratio
  - position sizing suggestion (% of capital based on risk)
  - confidence level and contributing factors
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, asdict

from ..schemas import Candle, Level
from .backtester import BacktestConfig, Strategy, _check_entry, _atr, _ma
from .levels_multifactor import detect_levels_multifactor

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    """A concrete trading recommendation."""
    action: str          # "buy" | "wait" | "near_signal"
    strategy: str        # which strategy triggered
    reason: str          # human-readable reason
    confidence: int      # 0-100

    # Prices — only meaningful when action == "buy"
    current_price: float
    entry_price: float   # recommended entry (limit or market)
    stop_loss: float
    target_price: float
    risk_pct: float      # distance to stop in %
    reward_pct: float    # distance to target in %
    risk_reward: float   # reward / risk

    # Context
    nearest_support: float
    nearest_resistance: float
    atr: float
    trend: str           # "up" | "down" | "neutral"

    # Position sizing (Kelly-like, capped)
    suggested_position_pct: float  # % of capital to allocate

    factors: list[str]   # contributing factor tags


def generate_signal(
    candles: list[Candle],
    strategy: Strategy = "breakout_pullback",
    config: BacktestConfig | None = None,
    backtest_stats: dict | None = None,
) -> dict:
    """Generate a live trading signal for the latest bar.

    Parameters
    ----------
    candles : recent daily candles (need >= 60)
    strategy : which strategy to check
    config : BacktestConfig (use optimised params if available)
    backtest_stats : stats from a prior backtest run (for confidence & sizing)

    Returns
    -------
    dict representation of Signal
    """
    cfg = config or BacktestConfig()
    if len(candles) < 60:
        return {"error": "need at least 60 candles"}

    idx = len(candles) - 1
    bar = candles[idx]
    price = bar.close

    # Detect levels
    levels = detect_levels_multifactor(candles, lookback=min(len(candles), 120))

    supports = sorted(
        [l for l in levels if l.kind == "support" and l.price < price],
        key=lambda l: price - l.price,
    )
    resistances = sorted(
        [l for l in levels if l.kind == "resistance" and l.price > price],
        key=lambda l: l.price - price,
    )

    nearest_sup = supports[0].price if supports else price * 0.95
    nearest_res = resistances[0].price if resistances else price * 1.10
    atr = _atr(candles, idx) if idx >= 14 else price * 0.02

    # Trend detection
    ma20 = _ma(candles, idx, 20)
    ma60 = _ma(candles, idx, 60) if idx >= 59 else None
    if ma20 and price > ma20:
        trend = "up" if (ma60 is None or ma20 > ma60) else "neutral"
    elif ma20 and price < ma20:
        trend = "down" if (ma60 is None or ma20 < ma60) else "neutral"
    else:
        trend = "neutral"

    # Check if there's a live entry signal on the LAST bar
    entry_reason = _check_entry(candles, idx, levels, strategy, cfg)

    # Also check if we're NEAR a signal (within 1-2 bars)
    near_reason = None
    if not entry_reason and idx >= 2:
        # Check if previous bar had signal (just missed it) or close to triggering
        near_reason = _check_entry(candles, idx - 1, levels, strategy, cfg)

    factors: list[str] = []
    confidence = 0

    if entry_reason:
        action = "buy"
        reason = entry_reason

        # ── Calculate entry/stop/target ──
        if strategy == "breakout_pullback":
            # Entry: current price or slightly below (limit near broken resistance)
            broken_res = [l for l in levels if l.kind == "resistance" and l.price < price and l.score >= cfg.min_level_score]
            if broken_res:
                ref_level = max(broken_res, key=lambda l: l.score)
                entry_price = round(max(ref_level.price, price * 0.998), 2)  # at or just above broken resistance
                stop_loss = round(ref_level.price * (1 - cfg.stop_loss_pct / 100), 2)
                factors.append(f"突破阻力位 {ref_level.price:.2f} (评分{ref_level.score:.0f})")
            else:
                entry_price = round(price, 2)
                stop_loss = round(price * (1 - cfg.stop_loss_pct / 100), 2)

            # Target: next resistance above, or fixed %
            target_price = round(min(nearest_res, price * (1 + cfg.target_pct / 100)), 2)

        else:  # bottom_stabilize
            # Entry: current price
            entry_price = round(price, 2)
            # Stop: below nearest support
            if supports:
                stop_loss = round(supports[0].price * 0.985, 2)
                factors.append(f"企稳支撑位 {supports[0].price:.2f} (评分{supports[0].score:.0f})")
            else:
                stop_loss = round(price * (1 - cfg.stop_loss_pct / 100), 2)
            target_price = round(min(nearest_res, price * (1 + cfg.target_pct / 100)), 2)

        # ATR-based alternative stop (use tighter of the two)
        if cfg.use_atr_stop:
            atr_stop = round(price - atr * cfg.atr_stop_mult, 2)
            if atr_stop > stop_loss:
                stop_loss = atr_stop
                factors.append(f"ATR止损 {atr_stop:.2f}")

        # Confidence scoring
        confidence = 40  # base for having a signal

        # Backtest win rate boost
        if backtest_stats:
            wr = backtest_stats.get("win_rate", 0)
            confidence += int(wr * 30)  # max +30
            if wr >= 0.5:
                factors.append(f"历史胜率 {wr*100:.0f}%")

        # Trend alignment boost
        if trend == "up":
            confidence += 15
            factors.append("顺势（上升趋势）")
        elif trend == "down":
            confidence -= 10
            factors.append("逆势（下降趋势）")

        # Volume confirmation
        if idx >= 5:
            avg_vol = sum(candles[j].volume for j in range(idx - 5, idx)) / 5
            if avg_vol > 0 and bar.volume > avg_vol * 1.3:
                confidence += 10
                factors.append("放量确认")

        # Support proximity boost
        if supports:
            dist_to_sup = (price - supports[0].price) / price * 100
            if dist_to_sup < 3:
                confidence += 5
                factors.append(f"距支撑仅 {dist_to_sup:.1f}%")

        confidence = max(0, min(100, confidence))

    elif near_reason:
        action = "near_signal"
        reason = f"接近信号：{near_reason}（关注回调入场）"
        entry_price = round(price * 0.99, 2)  # suggest limit below
        stop_loss = round(nearest_sup * 0.985 if supports else price * 0.97, 2)
        target_price = round(nearest_res, 2)
        confidence = 25
        factors.append("前一日出现信号")
        if trend == "up":
            factors.append("上升趋势")

    else:
        action = "wait"
        reason = _wait_reason(candles, idx, levels, strategy, cfg, trend)
        entry_price = round(price, 2)
        stop_loss = round(nearest_sup * 0.985 if supports else price * 0.97, 2)
        target_price = round(nearest_res, 2)
        confidence = 0
        factors.append("暂无入场信号")

    # Risk/reward calculation
    risk_pct = round(abs(entry_price - stop_loss) / entry_price * 100, 2) if entry_price > 0 else 0
    reward_pct = round(abs(target_price - entry_price) / entry_price * 100, 2) if entry_price > 0 else 0
    risk_reward = round(reward_pct / risk_pct, 2) if risk_pct > 0 else 0

    # Position sizing: risk-based (risk 2% of capital per trade)
    account_risk_pct = 2.0  # risk 2% of account per trade
    if risk_pct > 0:
        position_pct = round(min(account_risk_pct / risk_pct * 100, 100), 1)
    else:
        position_pct = 0

    # Adjust by confidence
    position_pct = round(position_pct * min(confidence, 80) / 80, 1)

    signal = Signal(
        action=action,
        strategy=strategy,
        reason=reason,
        confidence=confidence,
        current_price=round(price, 2),
        entry_price=entry_price,
        stop_loss=stop_loss,
        target_price=target_price,
        risk_pct=risk_pct,
        reward_pct=reward_pct,
        risk_reward=risk_reward,
        nearest_support=round(nearest_sup, 2),
        nearest_resistance=round(nearest_res, 2),
        atr=round(atr, 2),
        trend=trend,
        suggested_position_pct=position_pct,
        factors=factors,
    )
    return asdict(signal)


def _wait_reason(
    candles: list[Candle], idx: int, levels: list[Level],
    strategy: Strategy, cfg: BacktestConfig, trend: str,
) -> str:
    """Generate a helpful reason for why there's no signal."""
    price = candles[idx].close

    if strategy == "breakout_pullback":
        broken_res = [l for l in levels if l.kind == "resistance" and l.price < price]
        if not broken_res:
            return "尚无突破的阻力位，等待放量突破"
        nearest = max(broken_res, key=lambda l: l.price)
        dist = (price - nearest.price) / nearest.price * 100
        if dist > cfg.pullback_max_pct:
            return f"距已突破阻力 {nearest.price:.2f} 已远（{dist:.1f}%），等待新突破或回踩"
        if dist < cfg.pullback_min_pct:
            return f"刚突破 {nearest.price:.2f}，等待回踩确认（当前{dist:.1f}%）"
        return f"价格在突破区间内，但量价条件未满足"

    else:  # bottom_stabilize
        supports = [l for l in levels if l.kind == "support" and l.price < price]
        if not supports:
            return "未检测到有效支撑位"
        nearest = min(supports, key=lambda l: price - l.price)
        dist = (price - nearest.price) / nearest.price * 100
        if dist > cfg.stabilize_max_dist_pct:
            return f"距最近支撑 {nearest.price:.2f} 较远（{dist:.1f}%），等待回落"
        return f"接近支撑 {nearest.price:.2f}，等待企稳确认（{cfg.stabilize_bars}根K线）"
