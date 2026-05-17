"""Pipeline 编排器 (M3)

daily_run(system, end_date?) → SystemRun 全留痕

流程：
  1. Universe scan → 候选池
  2. 对每个候选股跑 signal 评估 → 触发买/卖的 signals
  3. 触发买入的 → 进 risk.size_order 计算具体委托
  4. 按 exec_cfg.max_orders_per_day 截断
  5. 全程写入 QuantSystemRun
"""
from __future__ import annotations

import logging
import time
from dataclasses import asdict
from datetime import date

import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from ...database import DATABASE_URL
from ...models import DailyCandle
from ..models import QuantSystem, QuantSystemRun
from .context import load_stock_context
from .risk import size_order
from .signal import evaluate_signal
from .universe import scan_universe, _get_engine, _load_candles_bulk

logger = logging.getLogger(__name__)


def _candidate_ctx(code: str, end_date: str | None, candle_map: dict | None = None) -> dict | None:
    """从已 bulk load 的 candle_map 取 ctx；fallback 单股查询。"""
    if candle_map is not None and code in candle_map:
        rows = candle_map[code]
        if end_date:
            rows = [r for r in rows if r.trade_date <= end_date]
        if len(rows) < 5:
            return None
        close = np.array([r.close or 0.0 for r in rows], dtype=float)
        vol = np.array([r.volume or 0.0 for r in rows], dtype=float)
        return {
            "open": np.array([r.open or 0.0 for r in rows], dtype=float),
            "high": np.array([r.high or 0.0 for r in rows], dtype=float),
            "low": np.array([r.low or 0.0 for r in rows], dtype=float),
            "close": close,
            "vol": vol,
            "volume": vol,
            "amount": close * vol,
            "_last_date": rows[-1].trade_date,
        }
    # fallback
    ctx = load_stock_context(code, end_date=end_date)
    if ctx is None:
        return None
    d = ctx.as_dict()
    d["_last_date"] = ctx.last_date
    return d


def daily_run(
    system: QuantSystem,
    end_date: str | None = None,
    *,
    open_positions: list[dict] | None = None,
) -> dict:
    """跑一次完整 pipeline，返回完整 run record（不写库；由 router 写）。

    open_positions: 当前已持仓 [{code, name, qty, entry_price, entry_date, stop_price, cost_basis}]
        - 持仓中的 code 不会出现在 buy 候选中（避免重复加仓）
        - 持仓中的 code 会单独评估 sell signal 与止损命中，产出 SELL 建议
    """
    t0 = time.time()
    trade_date = end_date or date.today().strftime("%Y-%m-%d")
    universe_cfg = system.universe_cfg or {}
    signal_cfg = system.signal_cfg or {}
    risk_cfg = system.risk_cfg or {}
    exec_cfg = system.exec_cfg or {}
    initial_capital = float(system.initial_capital or 100000.0)
    max_orders = int(exec_cfg.get("max_orders_per_day", 5))

    open_positions = open_positions or []
    held_codes = {p["code"] for p in open_positions}
    pos_by_code = {p["code"]: p for p in open_positions}

    # 1. Universe
    uni = scan_universe(universe_cfg, end_date=end_date)
    # 把已持仓的剔除（避免重复进同一支）
    candidates: list[dict] = [c for c in uni["candidates"] if c["code"] not in held_codes]

    # 2a. 已持仓：检查止损 + 卖出信号（独立加载这些 code 的 ctx）
    position_signals: list[dict] = []
    if open_positions:
        from datetime import datetime as _dt, timedelta as _td
        cutoff_pos = (_dt.fromisoformat(trade_date) - _td(days=420)).strftime("%Y-%m-%d")
        eng_pos = _get_engine()
        with Session(eng_pos) as session:
            pos_candle_map = _load_candles_bulk(session, list(held_codes), cutoff_pos, 260)

        for pos in open_positions:
            code = pos["code"]
            ctx = _candidate_ctx(code, end_date, pos_candle_map)
            if ctx is None:
                continue
            last_date = ctx.pop("_last_date", trade_date)
            last_low = float(ctx["low"][-1])
            last_close = float(ctx["close"][-1])
            stop = float(pos.get("stop_price") or 0.0)

            sell_reasons: list[str] = []
            # 止损命中
            if stop > 0 and last_low <= stop:
                sell_reasons.append(f"止损命中 (low={last_low:.2f} ≤ stop={stop:.2f})")
            # 卖出信号
            report = evaluate_signal(signal_cfg, ctx, code, last_date)
            if report.sell.triggered:
                desc = " / ".join(r.desc or r.expr for r in report.sell.rules if r.passed)
                sell_reasons.append(f"卖出信号: {desc}")

            if sell_reasons:
                entry_price = float(pos.get("entry_price") or 0.0)
                qty = int(pos.get("qty") or 0)
                pnl_pct = ((last_close - entry_price) / entry_price * 100) if entry_price else 0.0
                position_signals.append({
                    "code": code,
                    "name": pos.get("name", ""),
                    "side": "sell",
                    "price": last_close,
                    "date": last_date,
                    "qty": qty,
                    "entry_price": entry_price,
                    "stop_price": stop,
                    "pnl_pct": round(pnl_pct, 2),
                    "rules_hit": [
                        {"expr": r.expr, "desc": r.desc, "value": r.value}
                        for r in report.sell.rules if r.passed
                    ],
                    "reasons": sell_reasons,
                    "position_id": pos.get("id"),
                })

    if not candidates and not position_signals:
        duration_ms = int((time.time() - t0) * 1000)
        return {
            "trade_date": trade_date,
            "candidates": [],
            "signals": position_signals,
            "orders": [],
            "universe_count": 0,
            "signal_count": len(position_signals),
            "order_count": 0,
            "duration_ms": duration_ms,
            "error": "",
            "metrics": {
                "universe_total_scanned": uni["total_scanned"],
                "universe_passed": uni["passed"],
                "held_codes": len(held_codes),
                "position_sells": len(position_signals),
            },
        }

    # 2b. 候选股信号求值（bulk load candles for the candidates first）
    cand_codes = [c["code"] for c in candidates]
    candle_map: dict = {}
    if cand_codes:
        from datetime import datetime as _dt, timedelta as _td
        cutoff = (_dt.fromisoformat(trade_date) - _td(days=420)).strftime("%Y-%m-%d")
        eng = _get_engine()
        with Session(eng) as session:
            candle_map = _load_candles_bulk(session, cand_codes, cutoff, 260)

    signals: list[dict] = list(position_signals)  # 把持仓卖出信号先放进去
    for c in candidates:
        code = c["code"]
        ctx = _candidate_ctx(code, end_date, candle_map)
        if ctx is None:
            continue
        last_date = ctx.pop("_last_date", trade_date)
        report = evaluate_signal(signal_cfg, ctx, code, last_date)
        if report.buy.triggered:
            signals.append({
                "code": code,
                "name": c["name"],
                "side": "buy",
                "price": float(ctx["close"][-1]),
                "date": last_date,
                "rules_hit": [
                    {"expr": r.expr, "desc": r.desc, "value": r.value}
                    for r in report.buy.rules if r.passed
                ],
            })
        if report.sell.triggered:
            signals.append({
                "code": code,
                "name": c["name"],
                "side": "sell",
                "price": float(ctx["close"][-1]),
                "date": last_date,
                "rules_hit": [
                    {"expr": r.expr, "desc": r.desc, "value": r.value}
                    for r in report.sell.rules if r.passed
                ],
            })

    # 3. 风控：买入信号 → 委托。可用资金需扣除当前持仓占用
    buy_signals = [s for s in signals if s["side"] == "buy" and s["code"] not in held_codes]
    buy_signals.sort(key=lambda s: (len(s["rules_hit"]), s["price"]), reverse=True)

    total_position_max = initial_capital * float(risk_cfg.get("total_position_max_pct", 80.0)) / 100.0
    # 已占用 = 当前持仓的成本之和
    used_capital = sum(float(p.get("cost_basis") or 0.0) for p in open_positions)
    available_capital = max(0.0, total_position_max - used_capital)
    orders: list[dict] = []

    for sig in buy_signals:
        if len(orders) >= max_orders:
            break
        ctx = _candidate_ctx(sig["code"], end_date, candle_map)
        if ctx is None:
            continue
        proposal = size_order(
            code=sig["code"],
            name=sig["name"],
            price=sig["price"],
            ctx=ctx,
            risk_cfg=risk_cfg,
            total_capital=initial_capital,
            available_capital=available_capital,
            reason=f"信号命中 {len(sig['rules_hit'])} 条",
        )
        orders.append(asdict(proposal))
        if not proposal.rejected:
            available_capital -= proposal.notional

    duration_ms = int((time.time() - t0) * 1000)
    accepted_orders = [o for o in orders if not o["rejected"]]
    sell_signals_count = sum(1 for s in signals if s["side"] == "sell")

    return {
        "trade_date": trade_date,
        "candidates": candidates,
        "signals": signals,
        "orders": orders,
        "universe_count": len(candidates),
        "signal_count": len(signals),
        "order_count": len(accepted_orders),
        "duration_ms": duration_ms,
        "error": "",
        "metrics": {
            "universe_total_scanned": uni["total_scanned"],
            "universe_passed": uni["passed"],
            "buy_signals": len(buy_signals),
            "sell_signals": sell_signals_count,
            "position_sells": len(position_signals),
            "held_codes": len(held_codes),
            "orders_rejected": sum(1 for o in orders if o["rejected"]),
            "capital_used": float(round(total_position_max - available_capital, 2)),
            "capital_used_pct": float(
                round((total_position_max - available_capital) / initial_capital * 100, 2)
            ),
        },
    }
