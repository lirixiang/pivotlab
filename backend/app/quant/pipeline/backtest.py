"""历史回测引擎 (M4)

把 daily_run 的逻辑放进历史日期循环，叠加持仓账本 + 现金账本 + 净值曲线。

性能策略：
  1. 一次性 bulk load 主板所有股票在 [start - warmup, end] 区间的 K 线
  2. 为每只股票预先构造完整 numpy 数组（open/high/low/close/vol/amount）+ dates 列表
  3. 每个交易日 d：用 np.searchsorted 取截止 d 的切片视图（O(log N) + 零拷贝）
  4. 复用 dsl.eval_rule、signal.evaluate_signal、risk.size_order

MVP 简化（后续可扩展）：
  - 成交价：信号日收盘价（trade-on-close）
  - 卖出优先：止损命中（低点 ≤ stop）→ 信号触发；命中即 100% 清仓
  - 不考虑分红/送股 / T+1 用以下约束实现：今日开仓今日不卖
  - 佣金 + 滑点：默认 0.025% + 0.05%（双边）
"""
from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import DailyCandle, Stock
from ..dsl import eval_rule
from ..models import QuantSystem
from .risk import size_order
from .signal import evaluate_signal
from .universe import _get_engine, _is_main_board, _load_sector_pool_codes

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# 账本
# ──────────────────────────────────────────────────────────────
@dataclass
class Position:
    code: str
    name: str
    qty: int
    entry_price: float
    entry_date: str
    stop_price: float
    cost_basis: float = 0.0  # qty * entry_price + commission_in


@dataclass
class Portfolio:
    cash: float
    initial_capital: float
    positions: dict[str, Position] = field(default_factory=dict)
    peak_equity: float = 0.0
    drawdown_breaker_active: bool = False


# ──────────────────────────────────────────────────────────────
# 预加载层
# ──────────────────────────────────────────────────────────────
@dataclass
class StockSeries:
    code: str
    name: str
    industry: str
    is_st: bool
    dates: list[str]               # 升序
    dates_np: np.ndarray           # for searchsorted（用 str array）
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    vol: np.ndarray
    amount: np.ndarray


def _load_all_series(
    session: Session,
    start_date: str,
    end_date: str,
    warmup_days: int = 420,
    allowed_codes: set[str] | None = None,
) -> dict[str, StockSeries]:
    cutoff = (date.fromisoformat(start_date) - timedelta(days=warmup_days)).strftime("%Y-%m-%d")

    stocks = list(session.execute(select(Stock)).scalars().all())
    stocks = [s for s in stocks if _is_main_board(s.code)]
    stock_map = {s.code: s for s in stocks}
    codes = list(stock_map.keys())
    if allowed_codes is not None:
        codes = [c for c in codes if c in allowed_codes]
        stock_map = {c: stock_map[c] for c in codes}

    if not codes:
        return {}

    # 一次 SQL 取所有股票全部 K 线
    rows = session.execute(
        select(DailyCandle)
        .where(
            DailyCandle.code.in_(codes),
            DailyCandle.trade_date >= cutoff,
            DailyCandle.trade_date <= end_date,
        )
        .order_by(DailyCandle.code, DailyCandle.trade_date.asc())
    ).scalars().all()
    bucket: dict[str, list[DailyCandle]] = defaultdict(list)
    for r in rows:
        bucket[r.code].append(r)

    series_map: dict[str, StockSeries] = {}
    for code, candles in bucket.items():
        if len(candles) < 30:  # 起码 30 个 bar 才有意义
            continue
        s = stock_map[code]
        dates = [c.trade_date for c in candles]
        close = np.array([c.close or 0.0 for c in candles], dtype=float)
        vol = np.array([c.volume or 0.0 for c in candles], dtype=float)
        series_map[code] = StockSeries(
            code=code,
            name=s.name,
            industry=s.industry or "",
            is_st=bool(s.is_st),
            dates=dates,
            dates_np=np.array(dates, dtype="U10"),
            open=np.array([c.open or 0.0 for c in candles], dtype=float),
            high=np.array([c.high or 0.0 for c in candles], dtype=float),
            low=np.array([c.low or 0.0 for c in candles], dtype=float),
            close=close,
            vol=vol,
            amount=close * vol,
        )
    return series_map


def _slice_ctx(s: StockSeries, idx: int) -> dict[str, Any]:
    """切片到 [:idx+1]（零拷贝 view）。idx 必须 >= 0。"""
    sl = slice(0, idx + 1)
    return {
        "open": s.open[sl],
        "high": s.high[sl],
        "low": s.low[sl],
        "close": s.close[sl],
        "vol": s.vol[sl],
        "volume": s.vol[sl],
        "amount": s.amount[sl],
        "is_st": s.is_st,
    }


def _idx_for_date(s: StockSeries, d: str) -> int:
    """返回 dates 中 <= d 的最后一个索引。不存在则返回 -1。"""
    pos = int(np.searchsorted(s.dates_np, d, side="right")) - 1
    return pos


# ──────────────────────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────────────────────
def run_backtest(
    system: QuantSystem,
    start_date: str,
    end_date: str,
    *,
    commission_bps: float = 2.5,   # 0.025% 单边
    slippage_bps: float = 5.0,     # 0.05% 单边
    progress_cb=None,
) -> dict:
    """执行一次回测，返回 result dict（不写库；router 写）。

    progress_cb: 可选 callable(done, total, message)。
    """
    t0 = time.time()
    universe_cfg = system.universe_cfg or {}
    signal_cfg = system.signal_cfg or {}
    risk_cfg = system.risk_cfg or {}
    exec_cfg = system.exec_cfg or {}
    initial_capital = float(system.initial_capital or 1000000.0)
    max_orders_per_day = int(exec_cfg.get("max_orders_per_day", 5))

    filters = universe_cfg.get("filters") or []
    exclude_codes = set(universe_cfg.get("exclude_codes") or [])
    max_universe = int(universe_cfg.get("max_size") or 200)
    total_position_max_pct = float(risk_cfg.get("total_position_max_pct", 80.0))
    drawdown_breaker_pct = float(risk_cfg.get("drawdown_breaker_pct", 0.0) or 0.0)

    # 赛道池白名单（如果配了，仅在池内股票走回测）
    sector_pool_ids = [int(x) for x in (universe_cfg.get("sector_pool_ids") or [])]
    sector_tier_max = int(universe_cfg.get("sector_pool_tier_max") or 3)

    eng = _get_engine()
    with Session(eng) as session:
        allowed_codes: set[str] | None = None
        if sector_pool_ids:
            allowed_codes = _load_sector_pool_codes(session, sector_pool_ids, sector_tier_max)
            logger.info(
                "[backtest] sector_pool filter: pools=%s tier_max=%d -> %d codes",
                sector_pool_ids, sector_tier_max, len(allowed_codes),
            )
            if not allowed_codes:
                return _empty_result(start_date, end_date, initial_capital, t0,
                                     err="赛道池内没有有效个股")
        series_map = _load_all_series(session, start_date, end_date, allowed_codes=allowed_codes)

    if not series_map:
        return _empty_result(start_date, end_date, initial_capital, t0,
                             err="区间内无任何主板股票 K 线数据")

    # 交易日：所有股票在 [start, end] 区间的 dates 并集
    all_dates: set[str] = set()
    for s in series_map.values():
        for d in s.dates:
            if start_date <= d <= end_date:
                all_dates.add(d)
    trading_dates = sorted(all_dates)
    if not trading_dates:
        return _empty_result(start_date, end_date, initial_capital, t0,
                             err="区间内无任何交易日")

    portfolio = Portfolio(cash=initial_capital, initial_capital=initial_capital,
                          peak_equity=initial_capital)
    equity_curve: list[dict] = []
    trades: list[dict] = []
    daily_stats: list[dict] = []

    commission_rate = commission_bps / 10000.0
    slippage_rate = slippage_bps / 10000.0
    total_days = len(trading_dates)

    for i, d in enumerate(trading_dates):
        if progress_cb and (i % 20 == 0 or i == total_days - 1):
            try:
                progress_cb(i + 1, total_days, d)
            except Exception:
                pass

        # ── (A) 先处理 SELL：已持仓的检查 止损 / 卖出信号 ──
        codes_to_close: list[tuple[str, str, float]] = []  # (code, reason, exit_price)
        for code, pos in list(portfolio.positions.items()):
            s = series_map.get(code)
            if s is None:
                continue
            idx = _idx_for_date(s, d)
            if idx < 0:
                continue
            # T+1：当日开仓的不卖
            if pos.entry_date == d:
                continue
            today_low = float(s.low[idx])
            today_close = float(s.close[idx])
            today_open = float(s.open[idx])

            # 止损命中
            if pos.stop_price > 0 and today_low <= pos.stop_price:
                # 跳空缺口：以开盘价（更差） vs 止损价中的较差者成交
                exit_px = min(today_open, pos.stop_price)
                codes_to_close.append((code, f"止损命中 (stop={pos.stop_price:.2f})", exit_px))
                continue

            # 卖出信号
            ctx = _slice_ctx(s, idx)
            report = evaluate_signal(signal_cfg, ctx, code, d)
            if report.sell.triggered:
                rules_desc = " / ".join(r.desc or r.expr for r in report.sell.rules if r.passed)
                codes_to_close.append((code, f"卖出信号: {rules_desc}", today_close))

        # 执行平仓
        for code, reason, exit_price in codes_to_close:
            pos = portfolio.positions[code]
            gross = pos.qty * exit_price
            commission = gross * commission_rate
            slippage = gross * slippage_rate
            net_proceeds = gross - commission - slippage
            portfolio.cash += net_proceeds
            pnl = net_proceeds - pos.cost_basis
            pnl_pct = (pnl / pos.cost_basis * 100) if pos.cost_basis > 0 else 0.0
            hold_days = _count_bars_between(series_map.get(code), pos.entry_date, d)
            trades.append({
                "code": code,
                "name": pos.name,
                "side": "close",
                "qty": pos.qty,
                "price": round(exit_price, 3),
                "date": d,
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "hold_days": hold_days,
                "reason": reason,
            })
            del portfolio.positions[code]

        # ── (B) 计算 当日 持仓市值（用今日收盘）→ equity ──
        positions_value = 0.0
        for code, pos in portfolio.positions.items():
            s = series_map.get(code)
            if s is None:
                continue
            idx = _idx_for_date(s, d)
            if idx < 0:
                continue
            positions_value += pos.qty * float(s.close[idx])
        equity = portfolio.cash + positions_value

        # 更新峰值 + 回撤熔断检查
        if equity > portfolio.peak_equity:
            portfolio.peak_equity = equity
            portfolio.drawdown_breaker_active = False
        current_dd_pct = (
            (portfolio.peak_equity - equity) / portfolio.peak_equity * 100
            if portfolio.peak_equity > 0 else 0.0
        )
        if drawdown_breaker_pct > 0 and current_dd_pct >= drawdown_breaker_pct:
            portfolio.drawdown_breaker_active = True

        # ── (C) BUY 阶段：universe filter → buy signal → 风控 → 开仓 ──
        new_orders_today = 0
        buy_signals_today = 0
        candidates_today = 0

        if not portfolio.drawdown_breaker_active:
            # 已持仓的不重复进
            held = set(portfolio.positions.keys())

            # universe filter
            candidates: list[tuple[str, float]] = []  # (code, amount)
            for code, s in series_map.items():
                if code in exclude_codes or code in held:
                    continue
                idx = _idx_for_date(s, d)
                if idx < 30:  # 数据不够
                    continue
                ctx = _slice_ctx(s, idx)
                ok = True
                for rule in filters:
                    res = eval_rule(rule["expr"], ctx, rule.get("desc", ""))
                    if not res.passed:
                        ok = False
                        break
                if ok:
                    candidates.append((code, float(s.amount[idx])))
            # 按当日成交额排序，截断
            candidates.sort(key=lambda x: x[1], reverse=True)
            candidates = candidates[:max_universe]
            candidates_today = len(candidates)

            # buy signal
            buy_hits: list[dict] = []
            for code, _amt in candidates:
                s = series_map[code]
                idx = _idx_for_date(s, d)
                ctx = _slice_ctx(s, idx)
                report = evaluate_signal(signal_cfg, ctx, code, d)
                if report.buy.triggered:
                    buy_hits.append({
                        "code": code,
                        "name": s.name,
                        "price": float(s.close[idx]),
                        "rules_hit": [r for r in report.buy.rules if r.passed],
                        "ctx": ctx,
                    })
            buy_signals_today = len(buy_hits)
            # 排序：命中规则数 desc → 价格 desc
            buy_hits.sort(key=lambda h: (len(h["rules_hit"]), h["price"]), reverse=True)

            # 风控开仓
            total_cap_limit = portfolio.initial_capital * total_position_max_pct / 100.0
            current_position_value = positions_value
            for hit in buy_hits:
                if new_orders_today >= max_orders_per_day:
                    break
                # 计算还能用多少资金（总仓位上限 - 当前持仓市值；且不超过现金）
                room = max(0.0, total_cap_limit - current_position_value)
                available = min(portfolio.cash, room)
                if available <= 0:
                    break
                proposal = size_order(
                    code=hit["code"],
                    name=hit["name"],
                    price=hit["price"],
                    ctx=hit["ctx"],
                    risk_cfg=risk_cfg,
                    total_capital=portfolio.initial_capital,
                    available_capital=available,
                    reason=f"信号命中 {len(hit['rules_hit'])} 条",
                )
                if proposal.rejected or proposal.qty <= 0:
                    continue
                # 成交
                gross = proposal.qty * proposal.price
                commission = gross * commission_rate
                slippage = gross * slippage_rate
                cost = gross + commission + slippage
                if cost > portfolio.cash:
                    continue
                portfolio.cash -= cost
                portfolio.positions[proposal.code] = Position(
                    code=proposal.code,
                    name=proposal.name,
                    qty=proposal.qty,
                    entry_price=proposal.price,
                    entry_date=d,
                    stop_price=proposal.stop_price,
                    cost_basis=cost,
                )
                current_position_value += gross
                new_orders_today += 1
                trades.append({
                    "code": proposal.code,
                    "name": proposal.name,
                    "side": "open",
                    "qty": proposal.qty,
                    "price": proposal.price,
                    "date": d,
                    "stop_price": proposal.stop_price,
                    "est_loss": proposal.est_loss,
                    "notional": proposal.notional,
                    "reason": proposal.reason,
                })

        # 重新计算 equity（开仓后现金变了，但持仓估值用收盘价；这里 equity 已用收盘价算过）
        # 实际开仓后 equity 不变（cash → position），只是再算一次便于一致
        positions_value_eod = 0.0
        for code, pos in portfolio.positions.items():
            s = series_map.get(code)
            if s is None:
                continue
            idx = _idx_for_date(s, d)
            if idx < 0:
                continue
            positions_value_eod += pos.qty * float(s.close[idx])
        equity_eod = portfolio.cash + positions_value_eod

        if equity_eod > portfolio.peak_equity:
            portfolio.peak_equity = equity_eod
        dd_eod = (
            (portfolio.peak_equity - equity_eod) / portfolio.peak_equity * 100
            if portfolio.peak_equity > 0 else 0.0
        )

        equity_curve.append({
            "date": d,
            "equity": round(equity_eod, 2),
            "cash": round(portfolio.cash, 2),
            "positions_value": round(positions_value_eod, 2),
            "n_positions": len(portfolio.positions),
            "drawdown_pct": round(dd_eod, 2),
        })
        daily_stats.append({
            "date": d,
            "candidates": candidates_today,
            "buy_signals": buy_signals_today,
            "new_orders": new_orders_today,
            "closed": len(codes_to_close),
        })

    # ── 收盘：未平仓位 mark-to-market ──
    last_date = trading_dates[-1]
    positions_end: list[dict] = []
    for code, pos in portfolio.positions.items():
        s = series_map.get(code)
        if s is None:
            continue
        idx = _idx_for_date(s, last_date)
        if idx < 0:
            continue
        last_px = float(s.close[idx])
        mv = pos.qty * last_px
        pnl = mv - pos.cost_basis
        pnl_pct = (pnl / pos.cost_basis * 100) if pos.cost_basis > 0 else 0.0
        positions_end.append({
            "code": code,
            "name": pos.name,
            "qty": pos.qty,
            "entry_price": pos.entry_price,
            "entry_date": pos.entry_date,
            "stop_price": pos.stop_price,
            "last_price": round(last_px, 3),
            "market_value": round(mv, 2),
            "cost_basis": round(pos.cost_basis, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "hold_days": _count_bars_between(s, pos.entry_date, last_date),
        })

    metrics = _compute_metrics(
        equity_curve, trades, initial_capital, trading_dates
    )

    duration_ms = int((time.time() - t0) * 1000)
    return {
        "start_date": start_date,
        "end_date": end_date,
        "initial_capital": initial_capital,
        "trading_days": len(trading_dates),
        "equity_curve": equity_curve,
        "trades": trades,
        "daily_stats": daily_stats,
        "positions_end": positions_end,
        "metrics": metrics,
        "duration_ms": duration_ms,
        "error": "",
    }


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────
def _count_bars_between(s: StockSeries | None, d1: str, d2: str) -> int:
    if s is None:
        return 0
    i1 = _idx_for_date(s, d1)
    i2 = _idx_for_date(s, d2)
    if i1 < 0 or i2 < 0:
        return 0
    return max(0, i2 - i1)


def _empty_result(start, end, cap, t0, err: str) -> dict:
    return {
        "start_date": start, "end_date": end, "initial_capital": cap,
        "trading_days": 0, "equity_curve": [], "trades": [], "daily_stats": [],
        "positions_end": [],
        "metrics": _empty_metrics(cap),
        "duration_ms": int((time.time() - t0) * 1000),
        "error": err,
    }


def _empty_metrics(cap: float) -> dict:
    return {
        "total_return_pct": 0.0, "cagr_pct": 0.0, "max_drawdown_pct": 0.0,
        "sharpe": 0.0, "win_rate_pct": 0.0,
        "trade_count": 0, "win_count": 0, "loss_count": 0,
        "avg_win_pct": 0.0, "avg_loss_pct": 0.0,
        "profit_factor": 0.0, "exposure_pct": 0.0,
        "final_equity": cap,
    }


def _compute_metrics(
    equity_curve: list[dict], trades: list[dict], initial: float, trading_dates: list[str]
) -> dict:
    if not equity_curve:
        return _empty_metrics(initial)

    eq = np.array([p["equity"] for p in equity_curve], dtype=float)
    final = float(eq[-1])
    total_return_pct = (final - initial) / initial * 100

    # CAGR：按交易日数估算年化（A 股 ~242 交易日/年）
    n = len(trading_dates)
    years = n / 242.0
    cagr_pct = ((final / initial) ** (1 / years) - 1) * 100 if years > 0 and final > 0 else 0.0

    # Max drawdown
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / np.where(peak > 0, peak, 1)
    max_dd_pct = float(dd.max() * 100) if dd.size else 0.0

    # Sharpe（日收益率，无风险利率忽略；× sqrt(242)）
    daily_ret = np.diff(eq) / eq[:-1]
    if daily_ret.size > 1 and daily_ret.std() > 0:
        sharpe = float(daily_ret.mean() / daily_ret.std() * np.sqrt(242))
    else:
        sharpe = 0.0

    # 交易统计：只看 close
    closes = [t for t in trades if t["side"] == "close"]
    win = [t for t in closes if t["pnl"] > 0]
    loss = [t for t in closes if t["pnl"] <= 0]
    win_rate = len(win) / len(closes) * 100 if closes else 0.0
    avg_win = float(np.mean([t["pnl_pct"] for t in win])) if win else 0.0
    avg_loss = float(np.mean([t["pnl_pct"] for t in loss])) if loss else 0.0
    sum_win = sum(t["pnl"] for t in win)
    sum_loss = abs(sum(t["pnl"] for t in loss))
    profit_factor = sum_win / sum_loss if sum_loss > 0 else (float("inf") if sum_win > 0 else 0.0)

    # 仓位暴露
    pos_vals = np.array([p["positions_value"] for p in equity_curve], dtype=float)
    exposure = float((pos_vals / np.where(eq > 0, eq, 1)).mean() * 100) if eq.size else 0.0

    return {
        "total_return_pct": round(total_return_pct, 2),
        "cagr_pct": round(cagr_pct, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "sharpe": round(sharpe, 3),
        "win_rate_pct": round(win_rate, 2),
        "trade_count": len(closes),
        "win_count": len(win),
        "loss_count": len(loss),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "profit_factor": (
            round(profit_factor, 2) if profit_factor != float("inf") else 999.99
        ),
        "exposure_pct": round(exposure, 2),
        "final_equity": round(final, 2),
    }


# ──────────────────────────────────────────────────────────────
# 单股回测（K 线页用）
# ──────────────────────────────────────────────────────────────
def _load_single_series(
    session: Session, code: str, start_date: str, end_date: str, warmup_days: int = 420
) -> StockSeries | None:
    """只加载单只股票的 K 线序列。"""
    stock = session.execute(select(Stock).where(Stock.code == code)).scalar_one_or_none()
    if stock is None:
        return None
    cutoff = (date.fromisoformat(start_date) - timedelta(days=warmup_days)).strftime("%Y-%m-%d")
    rows = session.execute(
        select(DailyCandle)
        .where(DailyCandle.code == code, DailyCandle.trade_date >= cutoff, DailyCandle.trade_date <= end_date)
        .order_by(DailyCandle.trade_date.asc())
    ).scalars().all()
    if len(rows) < 30:
        return None
    dates = [c.trade_date for c in rows]
    close = np.array([c.close or 0.0 for c in rows], dtype=float)
    vol = np.array([c.volume or 0.0 for c in rows], dtype=float)
    return StockSeries(
        code=code,
        name=stock.name,
        industry=stock.industry or "",
        is_st=bool(stock.is_st),
        dates=dates,
        dates_np=np.array(dates, dtype="U10"),
        open=np.array([c.open or 0.0 for c in rows], dtype=float),
        high=np.array([c.high or 0.0 for c in rows], dtype=float),
        low=np.array([c.low or 0.0 for c in rows], dtype=float),
        close=close,
        vol=vol,
        amount=close * vol,
    )


def run_single_stock_backtest(
    system: QuantSystem,
    code: str,
    start_date: str,
    end_date: str,
    *,
    commission_bps: float = 2.5,
    slippage_bps: float = 5.0,
) -> dict:
    """对单只股票执行回测，跳过 universe 阶段，直接跑信号 + 风控。"""
    t0 = time.time()
    signal_cfg = system.signal_cfg or {}
    risk_cfg = system.risk_cfg or {}
    exec_cfg = system.exec_cfg or {}
    initial_capital = float(system.initial_capital or 1000000.0)

    eng = _get_engine()
    with Session(eng) as session:
        s = _load_single_series(session, code, start_date, end_date)

    if s is None:
        return _empty_result(start_date, end_date, initial_capital, t0,
                             err=f"股票 {code} 无足够 K 线数据")

    # 交易日
    trading_dates = [d for d in s.dates if start_date <= d <= end_date]
    if not trading_dates:
        return _empty_result(start_date, end_date, initial_capital, t0,
                             err="区间内无交易日")

    portfolio = Portfolio(cash=initial_capital, initial_capital=initial_capital,
                          peak_equity=initial_capital)
    equity_curve: list[dict] = []
    trades: list[dict] = []
    commission_rate = commission_bps / 10000.0
    slippage_rate = slippage_bps / 10000.0
    total_position_max_pct = float(risk_cfg.get("total_position_max_pct", 80.0))

    for d in trading_dates:
        idx = _idx_for_date(s, d)
        if idx < 30:
            # 数据不够 warmup
            eq_val = portfolio.cash
            equity_curve.append({
                "date": d, "equity": round(eq_val, 2), "cash": round(portfolio.cash, 2),
                "positions_value": 0.0, "n_positions": 0, "drawdown_pct": 0.0,
            })
            continue

        ctx = _slice_ctx(s, idx)
        today_close = float(s.close[idx])
        today_low = float(s.low[idx])
        today_open = float(s.open[idx])

        # ── SELL ──
        pos = portfolio.positions.get(code)
        if pos and pos.entry_date != d:
            closed = False
            # 止损
            if pos.stop_price > 0 and today_low <= pos.stop_price:
                exit_px = min(today_open, pos.stop_price)
                gross = pos.qty * exit_px
                comm = gross * commission_rate
                slip = gross * slippage_rate
                net = gross - comm - slip
                portfolio.cash += net
                pnl = net - pos.cost_basis
                pnl_pct = (pnl / pos.cost_basis * 100) if pos.cost_basis > 0 else 0.0
                trades.append({
                    "code": code, "name": s.name, "side": "close", "qty": pos.qty,
                    "price": round(exit_px, 3), "date": d,
                    "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2),
                    "hold_days": _count_bars_between(s, pos.entry_date, d),
                    "reason": f"止损命中 (stop={pos.stop_price:.2f})",
                })
                del portfolio.positions[code]
                closed = True

            if not closed:
                report = evaluate_signal(signal_cfg, ctx, code, d)
                if report.sell.triggered:
                    rules_desc = " / ".join(r.desc or r.expr for r in report.sell.rules if r.passed)
                    gross = pos.qty * today_close
                    comm = gross * commission_rate
                    slip = gross * slippage_rate
                    net = gross - comm - slip
                    portfolio.cash += net
                    pnl = net - pos.cost_basis
                    pnl_pct = (pnl / pos.cost_basis * 100) if pos.cost_basis > 0 else 0.0
                    trades.append({
                        "code": code, "name": s.name, "side": "close", "qty": pos.qty,
                        "price": round(today_close, 3), "date": d,
                        "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2),
                        "hold_days": _count_bars_between(s, pos.entry_date, d),
                        "reason": f"卖出信号: {rules_desc}",
                    })
                    del portfolio.positions[code]

        # ── BUY ──
        if code not in portfolio.positions:
            report = evaluate_signal(signal_cfg, ctx, code, d)
            if report.buy.triggered:
                total_cap_limit = initial_capital * total_position_max_pct / 100.0
                positions_value = 0.0
                for pc, pp in portfolio.positions.items():
                    pidx = _idx_for_date(s, d)
                    if pidx >= 0:
                        positions_value += pp.qty * float(s.close[pidx])
                room = max(0.0, total_cap_limit - positions_value)
                available = min(portfolio.cash, room)
                if available > 0:
                    proposal = size_order(
                        code=code, name=s.name, price=today_close,
                        ctx=ctx, risk_cfg=risk_cfg,
                        total_capital=initial_capital, available_capital=available,
                        reason=f"买入信号",
                    )
                    if not proposal.rejected and proposal.qty > 0:
                        gross = proposal.qty * proposal.price
                        comm = gross * commission_rate
                        slip = gross * slippage_rate
                        cost = gross + comm + slip
                        if cost <= portfolio.cash:
                            portfolio.cash -= cost
                            portfolio.positions[code] = Position(
                                code=code, name=s.name, qty=proposal.qty,
                                entry_price=proposal.price, entry_date=d,
                                stop_price=proposal.stop_price, cost_basis=cost,
                            )
                            trades.append({
                                "code": code, "name": s.name, "side": "open",
                                "qty": proposal.qty, "price": proposal.price, "date": d,
                                "stop_price": proposal.stop_price,
                                "est_loss": proposal.est_loss,
                                "notional": proposal.notional,
                                "reason": "买入信号",
                            })

        # ── EOD equity ──
        positions_value_eod = 0.0
        for pc, pp in portfolio.positions.items():
            pidx = _idx_for_date(s, d)
            if pidx >= 0:
                positions_value_eod += pp.qty * float(s.close[pidx])
        equity_eod = portfolio.cash + positions_value_eod
        if equity_eod > portfolio.peak_equity:
            portfolio.peak_equity = equity_eod
        dd_eod = ((portfolio.peak_equity - equity_eod) / portfolio.peak_equity * 100
                   if portfolio.peak_equity > 0 else 0.0)
        equity_curve.append({
            "date": d, "equity": round(equity_eod, 2), "cash": round(portfolio.cash, 2),
            "positions_value": round(positions_value_eod, 2),
            "n_positions": len(portfolio.positions), "drawdown_pct": round(dd_eod, 2),
        })

    # ── 未平仓位 ──
    last_date = trading_dates[-1]
    positions_end: list[dict] = []
    for pc, pp in portfolio.positions.items():
        idx = _idx_for_date(s, last_date)
        if idx < 0:
            continue
        last_px = float(s.close[idx])
        mv = pp.qty * last_px
        pnl = mv - pp.cost_basis
        pnl_pct = (pnl / pp.cost_basis * 100) if pp.cost_basis > 0 else 0.0
        positions_end.append({
            "code": pc, "name": pp.name, "qty": pp.qty,
            "entry_price": pp.entry_price, "entry_date": pp.entry_date,
            "stop_price": pp.stop_price, "last_price": round(last_px, 3),
            "market_value": round(mv, 2), "cost_basis": round(pp.cost_basis, 2),
            "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2),
            "hold_days": _count_bars_between(s, pp.entry_date, last_date),
        })

    metrics = _compute_metrics(equity_curve, trades, initial_capital, trading_dates)
    return {
        "start_date": start_date, "end_date": end_date,
        "initial_capital": initial_capital,
        "trading_days": len(trading_dates),
        "equity_curve": equity_curve, "trades": trades,
        "positions_end": positions_end, "metrics": metrics,
        "duration_ms": int((time.time() - t0) * 1000), "error": "",
    }
