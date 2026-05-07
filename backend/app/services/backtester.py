"""S/R-based backtesting engine (v2).

Strategies:
  - breakout_pullback: buy when price pulls back to a broken resistance.
  - bottom_stabilize: buy when price stabilises near a strong support.

All core thresholds are configurable via BacktestConfig.
New in v2: MA trend filter, commission/slippage, ATR-based trailing stop,
           configurable pullback range, min level score, hold limit, cooldown.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, asdict, field
from typing import Literal

from ..schemas import Candle, Level
from .levels_multifactor import detect_levels_multifactor

logger = logging.getLogger(__name__)

Strategy = Literal["breakout_pullback", "bottom_stabilize"]

PERIOD_BARS = {"1m": 22, "3m": 66, "6m": 132, "1y": 250}


# ── Configuration ──

@dataclass
class BacktestConfig:
    """All tunable parameters for the backtest engine."""
    # Exit rules
    stop_loss_pct: float = 2.5        # fixed stop-loss %
    target_pct: float = 6.0           # fixed take-profit %
    max_hold_bars: int = 20           # max holding period (days)
    use_atr_stop: bool = False        # trailing stop based on ATR
    atr_stop_mult: float = 2.0       # ATR multiplier for trailing stop

    # Entry filters
    volume_filter: bool = True        # require breakout volume ≥1.5x
    shrink_filter: bool = True        # require pullback vol ≤50% of breakout
    close_above_support: bool = True  # bar must close above the level
    weekly_confluence: bool = True    # prefer levels with weekly alignment
    ma_trend_filter: bool = False     # only enter in trend direction (above MA)
    ma_trend_period: int = 20         # MA period for trend filter

    # Strategy thresholds
    pullback_min_pct: float = 0.1     # min distance to broken level (%)
    pullback_max_pct: float = 3.0     # max distance to broken level (%)
    min_level_score: int = 30         # minimum S/R level score to trade
    stabilize_bars: int = 3           # bars to confirm stabilisation
    stabilize_max_dist_pct: float = 3.0  # max distance from support (%)

    # Cost & position
    commission_pct: float = 0.1       # round-trip commission (%)
    slippage_pct: float = 0.05        # slippage per side (%)
    cooldown_bars: int = 2            # min bars between trades


@dataclass
class Trade:
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    pnl_pct: float
    pnl_net: float        # after commission + slippage
    side: str
    reason_entry: str
    reason_exit: str
    holding_bars: int


@dataclass
class BacktestResult:
    code: str
    strategy: str
    period: str
    trades: list[dict]
    equity_curve: list[dict]
    stats: dict
    levels_used: list[dict]
    config: dict


# ── Helpers ──

def _find_levels_at(candles: list[Candle], bar_idx: int, lookback: int = 120) -> list[Level]:
    end = bar_idx + 1
    start = max(0, end - lookback)
    subset = candles[start:end]
    if len(subset) < 30:
        return []
    return detect_levels_multifactor(subset, lookback=len(subset))


def _atr(candles: list[Candle], idx: int, period: int = 14) -> float:
    """Compute ATR up to bar idx."""
    start = max(1, idx - period + 1)
    trs = []
    for j in range(start, idx + 1):
        h = candles[j].high
        l = candles[j].low
        pc = candles[j - 1].close
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs) if trs else 0.0


def _ma(candles: list[Candle], idx: int, period: int) -> float | None:
    """Simple moving average of close prices ending at idx."""
    if idx < period - 1:
        return None
    return sum(candles[j].close for j in range(idx - period + 1, idx + 1)) / period


# ── Main engine ──

def run_backtest(
    candles: list[Candle],
    strategy: Strategy = "breakout_pullback",
    period: str = "3m",
    config: BacktestConfig | None = None,
) -> BacktestResult:
    """Run backtest with full configuration."""
    cfg = config or BacktestConfig()
    n_bars = PERIOD_BARS.get(period, 66)
    data = candles[-(n_bars + 120):] if len(candles) > n_bars + 120 else candles
    test_start = max(0, len(data) - n_bars)

    trades: list[Trade] = []
    equity = 100.0
    in_trade = False
    entry_price = 0.0
    entry_date = ""
    entry_reason = ""
    entry_bar = 0
    trailing_stop = 0.0   # for ATR trailing stop
    last_exit_bar = -999   # for cooldown

    equity_curve: list[dict] = []
    base_price = data[test_start].close if test_start < len(data) else data[0].close

    levels: list[Level] = []
    levels_refresh_bar = -999

    for i in range(test_start, len(data)):
        bar = data[i]
        date_str = bar.date if hasattr(bar, "date") and bar.date else f"bar_{i}"

        # Refresh levels every 20 bars
        if i - levels_refresh_bar >= 20:
            levels = _find_levels_at(data, i)
            levels_refresh_bar = i

        price = bar.close
        benchmark = (price / base_price - 1) * 100

        if in_trade:
            pnl_raw = (price / entry_price - 1) * 100
            holding = i - entry_bar

            # Update trailing stop
            if cfg.use_atr_stop:
                atr_val = _atr(data, i)
                new_stop = price - atr_val * cfg.atr_stop_mult
                if new_stop > trailing_stop:
                    trailing_stop = new_stop

            exit_reason = ""
            if pnl_raw >= cfg.target_pct:
                exit_reason = "target"
            elif pnl_raw <= -cfg.stop_loss_pct:
                exit_reason = "stop"
            elif cfg.use_atr_stop and bar.low <= trailing_stop:
                exit_reason = "trail_stop"
                price = trailing_stop  # exit at trailing stop level
            elif holding >= cfg.max_hold_bars:
                exit_reason = "timeout"

            if exit_reason:
                pnl_raw = (price / entry_price - 1) * 100
                cost = cfg.commission_pct + cfg.slippage_pct * 2
                pnl_net = pnl_raw - cost
                trade = Trade(
                    entry_date=entry_date,
                    entry_price=round(entry_price, 2),
                    exit_date=date_str,
                    exit_price=round(price, 2),
                    pnl_pct=round(pnl_raw, 2),
                    pnl_net=round(pnl_net, 2),
                    side="long",
                    reason_entry=entry_reason,
                    reason_exit=exit_reason,
                    holding_bars=holding,
                )
                trades.append(trade)
                equity *= (1 + pnl_net / 100)
                in_trade = False
                last_exit_bar = i
        else:
            # Cooldown check
            if i - last_exit_bar < cfg.cooldown_bars:
                pass
            else:
                signal = _check_entry(data, i, levels, strategy, cfg)
                if signal:
                    in_trade = True
                    entry_price = price
                    entry_date = date_str
                    entry_reason = signal
                    entry_bar = i
                    if cfg.use_atr_stop:
                        atr_val = _atr(data, i)
                        trailing_stop = price - atr_val * cfg.atr_stop_mult

        equity_curve.append({
            "date": date_str,
            "equity": round(
                equity if not in_trade else equity * (1 + (price / entry_price - 1)),
                2,
            ),
            "benchmark": round(benchmark, 2),
        })

    # Close open trade
    if in_trade and data:
        last = data[-1]
        pnl_raw = (last.close / entry_price - 1) * 100
        cost = cfg.commission_pct + cfg.slippage_pct * 2
        trades.append(Trade(
            entry_date=entry_date,
            entry_price=round(entry_price, 2),
            exit_date=last.date if hasattr(last, "date") and last.date else "end",
            exit_price=round(last.close, 2),
            pnl_pct=round(pnl_raw, 2),
            pnl_net=round(pnl_raw - cost, 2),
            side="long",
            reason_entry=entry_reason,
            reason_exit="open",
            holding_bars=len(data) - 1 - entry_bar,
        ))

    # Stats
    wins = [t for t in trades if t.pnl_net > 0]
    losses = [t for t in trades if t.pnl_net <= 0]
    total = len(trades)
    win_rate = len(wins) / total if total else 0
    avg_win = sum(t.pnl_net for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t.pnl_net for t in losses) / len(losses) if losses else 0
    gross_profit = sum(t.pnl_net for t in wins)
    gross_loss = abs(sum(t.pnl_net for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    total_cost = sum(cfg.commission_pct + cfg.slippage_pct * 2 for _ in trades)

    # Max drawdown
    peak = 100.0
    max_dd = 0.0
    for pt in equity_curve:
        eq = pt["equity"]
        if eq > peak:
            peak = eq
        dd = (eq - peak) / peak * 100
        if dd < max_dd:
            max_dd = dd

    total_return = (equity_curve[-1]["equity"] / 100 - 1) * 100 if equity_curve else 0

    # Sharpe-like ratio (daily returns annualised)
    daily_returns = []
    for j in range(1, len(equity_curve)):
        prev_eq = equity_curve[j - 1]["equity"]
        cur_eq = equity_curve[j]["equity"]
        if prev_eq > 0:
            daily_returns.append(cur_eq / prev_eq - 1)
    if daily_returns:
        mean_r = sum(daily_returns) / len(daily_returns)
        std_r = math.sqrt(sum((r - mean_r) ** 2 for r in daily_returns) / len(daily_returns)) if len(daily_returns) > 1 else 1
        sharpe = (mean_r / std_r * math.sqrt(252)) if std_r > 0 else 0
    else:
        sharpe = 0

    stats = {
        "total_trades": total,
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": round(win_rate, 4),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(min(profit_factor, 99.9), 2),
        "max_drawdown": round(max_dd, 2),
        "total_return": round(total_return, 2),
        "sharpe": round(sharpe, 2),
        "total_cost": round(total_cost, 2),
    }

    levels_info = [
        {"price": round(l.price, 2), "kind": l.kind, "score": round(l.score, 1), "label": l.label}
        for l in levels[:8]
    ]

    return BacktestResult(
        code="",
        strategy=strategy,
        period=period,
        trades=[asdict(t) for t in trades],
        equity_curve=equity_curve,
        stats=stats,
        levels_used=levels_info,
        config=asdict(cfg),
    )


# ── Entry signal detection ──

def _check_entry(
    data: list[Candle],
    idx: int,
    levels: list[Level],
    strategy: Strategy,
    cfg: BacktestConfig,
) -> str | None:
    if idx < 3 or idx >= len(data):
        return None

    bar = data[idx]
    prev = data[idx - 1]
    price = bar.close

    # MA trend filter
    if cfg.ma_trend_filter:
        ma_val = _ma(data, idx, cfg.ma_trend_period)
        if ma_val is not None and price < ma_val:
            return None  # below MA → skip long entry

    supports = sorted(
        [l for l in levels if l.kind == "support" and l.price < price],
        key=lambda l: price - l.price,
    )

    if strategy == "breakout_pullback":
        # Broken resistances that price is now above
        broken_res = [
            l for l in levels
            if l.kind == "resistance" and l.price < price and l.score >= cfg.min_level_score
        ]
        for r in broken_res[:3]:
            dist_pct = (price - r.price) / r.price * 100
            if cfg.pullback_min_pct < dist_pct < cfg.pullback_max_pct:
                prev_dist = (prev.close - r.price) / r.price * 100
                if prev_dist < dist_pct and prev_dist < cfg.pullback_max_pct:
                    # Volume filter
                    if cfg.volume_filter and idx >= 5:
                        avg_vol = sum(data[j].volume for j in range(idx - 5, idx)) / 5
                        if avg_vol > 0:
                            if bar.volume > avg_vol * 1.5:
                                pass  # breakout volume OK
                            elif cfg.shrink_filter and bar.volume > avg_vol * 0.8:
                                continue

                    if cfg.close_above_support and price < r.price:
                        continue

                    qual = "强" if r.score >= 60 else "中"
                    return f"突破回踩{qual}阻力{r.price:.1f}"

    elif strategy == "bottom_stabilize":
        for s in supports[:3]:
            if s.score < cfg.min_level_score:
                continue
            dist_pct = (price - s.price) / s.price * 100
            if dist_pct > cfg.stabilize_max_dist_pct:
                continue

            # Stabilisation check
            n = cfg.stabilize_bars
            start_j = max(0, idx - n + 1)
            held = all(
                data[j].close > s.price * 0.985
                for j in range(start_j, idx + 1)
            )
            if not held:
                continue

            body_up = bar.close > bar.open
            has_lower_wick = (min(bar.open, bar.close) - bar.low) > abs(bar.close - bar.open) * 0.5
            if not (body_up or has_lower_wick):
                continue

            if cfg.volume_filter and idx >= 5:
                avg_vol = sum(data[j].volume for j in range(idx - 5, idx)) / 5
                if avg_vol > 0 and bar.volume < avg_vol * 0.3:
                    continue

            qual = "强" if s.score >= 60 else "中"
            return f"企稳{qual}支撑{s.price:.1f}"

    return None
