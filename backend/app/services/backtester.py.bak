"""Simple S/R-based backtesting engine.

Strategies:
  - breakout_pullback: buy when price breaks above resistance then pulls back
    to retest, sell at target or stop-loss.
  - bottom_stabilize: buy when price stabilises near a strong support level,
    sell at target or stop-loss.

Returns a list of trades with entry/exit dates, prices, PnL, and an equity curve.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from typing import Literal

from ..schemas import Candle, Level
from .levels_multifactor import detect_levels_multifactor

logger = logging.getLogger(__name__)

Strategy = Literal["breakout_pullback", "bottom_stabilize"]

PERIOD_BARS = {"1m": 22, "3m": 66, "6m": 132, "1y": 250}


@dataclass
class Trade:
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    pnl_pct: float
    side: str  # "long"
    reason_entry: str
    reason_exit: str  # "target" | "stop" | "timeout"
    holding_bars: int


@dataclass
class BacktestResult:
    code: str
    strategy: str
    period: str
    trades: list[dict]
    equity_curve: list[dict]  # [{date, equity, benchmark}]
    stats: dict
    levels_used: list[dict]  # simplified level info


def _find_levels_at(candles: list[Candle], bar_idx: int, lookback: int = 120) -> list[Level]:
    """Detect levels using candles up to bar_idx (no lookahead)."""
    end = bar_idx + 1
    start = max(0, end - lookback)
    subset = candles[start:end]
    if len(subset) < 30:
        return []
    return detect_levels_multifactor(subset, lookback=len(subset))


def run_backtest(
    candles: list[Candle],
    strategy: Strategy = "breakout_pullback",
    period: str = "3m",
    stop_loss_pct: float = 2.5,
    target_pct: float = 6.0,
    volume_filter: bool = True,
    shrink_filter: bool = True,
    close_above_support: bool = True,
    weekly_confluence: bool = True,
) -> BacktestResult:
    """Run a backtest over the given candles."""
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
    max_hold = 20  # max holding period in bars

    equity_curve: list[dict] = []
    base_price = data[test_start].close if test_start < len(data) else data[0].close

    # Pre-compute levels at the start of the test period (refreshed every 20 bars)
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
            pnl = (price / entry_price - 1) * 100
            holding = i - entry_bar

            exit_reason = ""
            if pnl >= target_pct:
                exit_reason = "target"
            elif pnl <= -stop_loss_pct:
                exit_reason = "stop"
            elif holding >= max_hold:
                exit_reason = "timeout"

            if exit_reason:
                trade = Trade(
                    entry_date=entry_date,
                    entry_price=round(entry_price, 2),
                    exit_date=date_str,
                    exit_price=round(price, 2),
                    pnl_pct=round(pnl, 2),
                    side="long",
                    reason_entry=entry_reason,
                    reason_exit=exit_reason,
                    holding_bars=holding,
                )
                trades.append(trade)
                equity *= (1 + pnl / 100)
                in_trade = False
        else:
            # Look for entry signal
            signal = _check_entry(
                data, i, levels, strategy,
                volume_filter=volume_filter,
                shrink_filter=shrink_filter,
                close_above_support=close_above_support,
                weekly_confluence=weekly_confluence,
            )
            if signal:
                in_trade = True
                entry_price = price
                entry_date = date_str
                entry_reason = signal
                entry_bar = i

        equity_curve.append({
            "date": date_str,
            "equity": round(equity if not in_trade else equity * (1 + (price / entry_price - 1)), 2),
            "benchmark": round(benchmark, 2),
        })

    # Close any open trade at last bar
    if in_trade and len(data) > 0:
        last = data[-1]
        pnl = (last.close / entry_price - 1) * 100
        trades.append(Trade(
            entry_date=entry_date,
            entry_price=round(entry_price, 2),
            exit_date=last.date if hasattr(last, "date") and last.date else "end",
            exit_price=round(last.close, 2),
            pnl_pct=round(pnl, 2),
            side="long",
            reason_entry=entry_reason,
            reason_exit="open",
            holding_bars=len(data) - 1 - entry_bar,
        ))

    # Compute stats
    wins = [t for t in trades if t.pnl_pct > 0]
    losses = [t for t in trades if t.pnl_pct <= 0]
    total = len(trades)
    win_rate = len(wins) / total if total else 0
    avg_win = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0
    gross_profit = sum(t.pnl_pct for t in wins)
    gross_loss = abs(sum(t.pnl_pct for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Max drawdown from equity curve
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
    }

    # Simplified levels for display
    levels_info = [
        {
            "price": round(l.price, 2),
            "kind": l.kind,
            "score": round(l.score, 1),
            "label": l.label,
        }
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
    )


def _check_entry(
    data: list[Candle],
    idx: int,
    levels: list[Level],
    strategy: Strategy,
    volume_filter: bool = True,
    shrink_filter: bool = True,
    close_above_support: bool = True,
    weekly_confluence: bool = True,
) -> str | None:
    """Check if bar at idx triggers an entry signal. Returns reason string or None."""
    if idx < 3 or idx >= len(data):
        return None

    bar = data[idx]
    prev = data[idx - 1]
    prev2 = data[idx - 2]
    price = bar.close

    supports = sorted(
        [l for l in levels if l.kind == "support" and l.price < price],
        key=lambda l: price - l.price,
    )
    resistances = sorted(
        [l for l in levels if l.kind == "resistance" and l.price > price],
        key=lambda l: l.price - price,
    )

    if strategy == "breakout_pullback":
        # Look for: price was above a resistance (broke it), then pulled back near it
        for r in resistances[:3]:
            # Check if prev2 closed above resistance (breakout happened)
            if prev2.close > r.price and prev.close > r.price:
                continue  # Still above, no pullback yet

        # Also check past resistances that price is now above (broken)
        broken_res = [l for l in levels if l.kind == "resistance" and l.price < price and l.score >= 30]
        for r in broken_res[:3]:
            dist_pct = (price - r.price) / r.price * 100
            if 0.1 < dist_pct < 3.0:
                # Price pulled back near broken resistance (now support)
                # Check pullback: prev bar was closer or touched
                prev_dist = (prev.close - r.price) / r.price * 100
                if prev_dist < dist_pct and prev_dist < 2.0:
                    # Volume filter
                    if volume_filter and idx >= 5:
                        avg_vol = sum(data[j].volume for j in range(idx - 5, idx)) / 5
                        if bar.volume > avg_vol * 1.5:
                            pass  # breakout bar had volume - skip shrink check
                        elif shrink_filter and bar.volume > avg_vol * 0.8:
                            continue  # not shrinking enough

                    if close_above_support and price < r.price:
                        continue  # closed below the level

                    qual = "强" if r.score >= 60 else "中"
                    return f"突破回踩{qual}阻力{r.price:.1f}"

    elif strategy == "bottom_stabilize":
        # Look for: price near strong support with stabilisation signs
        for s in supports[:3]:
            if s.score < 25:
                continue
            dist_pct = (price - s.price) / s.price * 100
            if dist_pct > 3.0:
                continue  # too far

            # Check stabilisation: bar closed above support, recent bars also held
            held = all(data[j].close > s.price * 0.985 for j in range(max(0, idx - 2), idx + 1))
            if not held:
                continue

            # Check for reversal candle pattern (close > open, lower wick)
            body_up = bar.close > bar.open
            has_lower_wick = (min(bar.open, bar.close) - bar.low) > abs(bar.close - bar.open) * 0.5
            if not (body_up or has_lower_wick):
                continue

            # Volume filter
            if volume_filter and idx >= 5:
                avg_vol = sum(data[j].volume for j in range(idx - 5, idx)) / 5
                if bar.volume < avg_vol * 0.3:
                    continue  # too dead

            qual = "强" if s.score >= 60 else "中"
            return f"企稳{qual}支撑{s.price:.1f}"

    return None
