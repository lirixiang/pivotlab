"""风控 / 资金管理层 (M3)

把 "信号 + 账户状态 + 风控配置" → 具体委托手数。

核心规则：
  1. 单票最大占用 = total_capital * per_stock_max_pct%
  2. 单笔最大亏损 = total_capital * per_trade_max_loss_pct%
     反推手数：max_qty_by_risk = max_loss / (price - stop_price)
     若 stop_price >= price，视为风控异常 → 拒单
  3. 总仓位上限 = total_capital * total_position_max_pct%
     运行时累加：超过则丢弃后续单子
  4. A 股最小 100 股，向下取整到 100 的整数倍

止损价计算（基于 stop_loss.type）：
  - ma     : ma(close, ma_period) 当前值
  - percent: price * (1 - percent/100)
  - atr    : price - atr(high, low, close, atr_period) * atr_mult

注：M3 暂不考虑当前持仓（M5 完成 journal 后补）；假设全部资金可用。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from ..dsl.functions import atr as _atr_fn, ma as _ma_fn


@dataclass
class OrderProposal:
    code: str
    name: str
    action: str          # "BUY"
    price: float         # 拟成交价（限价）
    qty: int             # 手数（向下取整 100）
    stop_price: float
    est_loss: float      # 触发止损时的预估亏损（元）
    notional: float      # 占用资金（price * qty）
    risk_used_pct: float # 该单占资金的百分比
    reason: str          # 触发理由摘要
    rejected: bool = False
    reject_reason: str = ""


def _compute_stop_price(
    stop_cfg: dict, ctx: dict[str, np.ndarray], price: float
) -> tuple[float, str]:
    """返回 (stop_price, 计算依据描述)"""
    stype = (stop_cfg or {}).get("type", "ma")
    if stype == "ma":
        n = int(stop_cfg.get("ma_period", 20))
        s = _ma_fn(ctx["close"], n)
        sp = float(s[-1]) if s.size and not np.isnan(s[-1]) else price * 0.95
        return sp, f"跌破 {n} 日线 (={sp:.2f})"
    if stype == "percent":
        pct = float(stop_cfg.get("percent", 8.0))
        return price * (1 - pct / 100.0), f"跌幅 {pct}%"
    if stype == "atr":
        n = int(stop_cfg.get("atr_period", 14))
        m = float(stop_cfg.get("atr_mult", 2.0))
        a = _atr_fn(ctx["high"], ctx["low"], ctx["close"], n)
        av = float(a[-1]) if a.size and not np.isnan(a[-1]) else price * 0.05
        return price - av * m, f"价 - ATR({n}) × {m} (ATR={av:.2f})"
    return price * 0.95, "默认 -5%"


def size_order(
    code: str,
    name: str,
    price: float,
    ctx: dict[str, np.ndarray],
    risk_cfg: dict,
    total_capital: float,
    available_capital: float,
    reason: str = "",
) -> OrderProposal:
    """根据风控规则计算单笔委托。"""
    risk_cfg = risk_cfg or {}
    per_stock_pct = float(risk_cfg.get("per_stock_max_pct", 10.0))
    per_loss_pct = float(risk_cfg.get("per_trade_max_loss_pct", 2.0))
    stop_cfg = risk_cfg.get("stop_loss") or {"type": "ma", "ma_period": 20}

    stop_price, stop_reason = _compute_stop_price(stop_cfg, ctx, price)

    # 校验：止损价必须低于买入价
    if stop_price >= price:
        return OrderProposal(
            code=code, name=name, action="BUY", price=price, qty=0,
            stop_price=stop_price, est_loss=0, notional=0, risk_used_pct=0,
            reason=reason, rejected=True,
            reject_reason=f"止损价 {stop_price:.2f} ≥ 买入价 {price:.2f}（{stop_reason}）",
        )

    risk_per_share = price - stop_price
    # 按单笔最大亏损反推
    max_loss = total_capital * per_loss_pct / 100.0
    qty_by_risk = int(max_loss / risk_per_share)
    # 按单票仓位上限
    per_stock_cap = total_capital * per_stock_pct / 100.0
    qty_by_cap = int(per_stock_cap / price)
    # 按当前可用资金
    qty_by_cash = int(available_capital / price)

    qty = max(0, min(qty_by_risk, qty_by_cap, qty_by_cash))
    # A 股向下取整到 100 股
    qty = (qty // 100) * 100

    if qty == 0:
        return OrderProposal(
            code=code, name=name, action="BUY", price=price, qty=0,
            stop_price=stop_price, est_loss=0, notional=0, risk_used_pct=0,
            reason=reason, rejected=True,
            reject_reason=(
                f"手数为 0（风控={qty_by_risk}股、单票={qty_by_cap}股、现金={qty_by_cash}股，"
                f"min={min(qty_by_risk, qty_by_cap, qty_by_cash)}股 取整100=0）"
            ),
        )

    notional = qty * price
    est_loss = qty * risk_per_share
    return OrderProposal(
        code=code, name=name, action="BUY",
        price=round(price, 3), qty=qty,
        stop_price=round(stop_price, 3),
        est_loss=round(est_loss, 2),
        notional=round(notional, 2),
        risk_used_pct=round(notional / total_capital * 100, 2),
        reason=f"{reason} | 止损：{stop_reason}",
    )
