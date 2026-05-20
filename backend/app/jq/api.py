"""注入用户策略的全局函数集，对齐聚宽 API。

在回测执行期间，这些函数被绑定到用户代码的全局命名空间：
    order_target_percent(security, percent)
    order_target_value(security, value)
    order_target(security, amount)
    order_value(security, value)
    order(security, amount)
    get_price(...)
    attribute_history(...)
    history(...)
    get_current_data()
    get_all_securities()
    get_index_stocks(...)
    set_benchmark(security)
    set_commission(commission)
    set_slippage(slippage)
    log.info / log.warn / log.error

调用方式：
    ns = build_api_namespace(context)
    exec(user_code, ns)
"""
from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime
from typing import Any

from .context import JQContext, JQPosition, OrderRecord
from .data_bridge import (
    attribute_history as _attr_hist,
    get_all_securities as _get_all_sec,
    get_current_data as _get_current,
    get_index_stocks as _get_index,
    get_price as _get_price,
    history as _history,
    to_db_code,
    to_jq_code,
)

logger = logging.getLogger(__name__)

_ROUND_LOT = 100  # A 股最小买入单位 100 股


def _round_to_lot(n: float) -> int:
    """向下取整到 100 股的整数倍（A 股买入限制）。"""
    return int(n // _ROUND_LOT) * _ROUND_LOT


# ─────────────────────── log 对象 ───────────────────────

class _Logger:
    def __init__(self, ctx: JQContext):
        self._ctx = ctx

    def _append(self, level: str, msg: str) -> None:
        self._ctx._logs.append({
            "level": level,
            "dt": self._ctx.current_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "msg": str(msg),
        })

    def info(self, msg): self._append("INFO", msg)
    def warn(self, msg): self._append("WARN", msg)
    def warning(self, msg): self._append("WARN", msg)
    def error(self, msg): self._append("ERROR", msg)
    def debug(self, msg): self._append("DEBUG", msg)


# ─────────────────────── 内部撮合 ───────────────────────

def _execute_order(
    ctx: JQContext,
    security: str,
    amount: int,       # 正=买 负=卖
    price: float,
) -> OrderRecord | None:
    """立即以 price 成交，更新 portfolio。"""
    if amount == 0 or price <= 0:
        return None

    side = "buy" if amount > 0 else "sell"
    commission = max(
        abs(amount) * price * ctx.commission_ratio,
        ctx.min_commission if side == "buy" else 0.0,
    )
    # 应用滑点
    exec_price = price * (1 + ctx.slippage) if side == "buy" else price * (1 - ctx.slippage)

    total_cost = abs(amount) * exec_price + (commission if side == "buy" else -commission)

    pf = ctx.portfolio

    if side == "buy":
        if pf.cash < total_cost:
            ctx._logs.append({
                "level": "WARN",
                "dt": ctx.current_dt.strftime("%Y-%m-%d"),
                "msg": f"现金不足，无法买入 {security}: 需要 {total_cost:.2f}，剩余 {pf.cash:.2f}",
            })
            return None
        pf.cash -= total_cost
        if security in pf.positions:
            pos = pf.positions[security]
            new_qty = pos.total_amount + amount
            pos.avg_cost = (pos.avg_cost * pos.total_amount + exec_price * amount) / new_qty
            pos.total_amount = new_qty
            pos.price = exec_price
        else:
            pf.positions[security] = JQPosition(
                security=security,
                total_amount=amount,
                closeable_amount=0,  # T+1：今日买入不可卖
                avg_cost=exec_price,
                price=exec_price,
                acc_avg_cost=exec_price,
            )
        ctx._today_bought.add(security)
    else:  # sell
        pos = pf.positions.get(security)
        if pos is None or pos.closeable_amount < abs(amount):
            ctx._logs.append({
                "level": "WARN",
                "dt": ctx.current_dt.strftime("%Y-%m-%d"),
                "msg": f"可卖持仓不足，无法卖出 {security}: "
                       f"需要 {abs(amount)}，可卖 {pos.closeable_amount if pos else 0}",
            })
            return None
        sell_qty = abs(amount)
        proceeds = sell_qty * exec_price - commission
        pf.cash += proceeds
        pos.total_amount -= sell_qty
        pos.closeable_amount -= sell_qty
        pf.pnl += proceeds - sell_qty * pos.avg_cost
        if pos.total_amount <= 0:
            del pf.positions[security]

    rec = OrderRecord(
        order_id=str(uuid.uuid4())[:8],
        security=security,
        amount=amount,
        filled=amount,
        price=exec_price,
        status="filled",
        created_dt=ctx.current_dt,
        side=side,
        commission=commission,
    )
    ctx._orders.append(rec)
    return rec


def _get_current_price(ctx: JQContext, security: str) -> float:
    """从已缓存市价或数据库取当前收盘价。"""
    pos = ctx.portfolio.positions.get(security)
    if pos and pos.price > 0:
        return pos.price
    # fallback: 查数据库最新一根 K 线
    end_date = ctx.current_dt.strftime("%Y-%m-%d")
    data = _get_current([security], end_date=end_date)
    return data[security].last_price if security in data else 0.0


# ─────────────────────── 下单函数 ───────────────────────

def _make_order(ctx: JQContext, security: str, amount: int) -> OrderRecord | None:
    """按股数下单（正=买 负=卖）。"""
    jq_code = to_jq_code(security)
    price = _get_current_price(ctx, jq_code)
    if price <= 0:
        return None
    return _execute_order(ctx, jq_code, amount, price)


def _order(ctx: JQContext, security: str, amount: int) -> OrderRecord | None:
    """order(security, amount) — 按股数下单。"""
    return _make_order(ctx, security, amount)


def _order_value(ctx: JQContext, security: str, value: float) -> OrderRecord | None:
    """order_value(security, value) — 按金额下单。"""
    jq_code = to_jq_code(security)
    price = _get_current_price(ctx, jq_code)
    if price <= 0:
        return None
    amount = int(value / price)
    if amount == 0:
        return None
    amount = _round_to_lot(abs(amount)) * (1 if amount > 0 else -1)
    return _execute_order(ctx, jq_code, amount, price)


def _order_target(ctx: JQContext, security: str, amount: int) -> OrderRecord | None:
    """order_target(security, amount) — 调仓到目标股数。"""
    jq_code = to_jq_code(security)
    pos = ctx.portfolio.positions.get(jq_code)
    current_qty = pos.total_amount if pos else 0
    delta = amount - current_qty
    if delta == 0:
        return None
    return _make_order(ctx, jq_code, delta)


def _order_target_value(ctx: JQContext, security: str, value: float) -> OrderRecord | None:
    """order_target_value(security, value) — 调仓到目标市值。"""
    jq_code = to_jq_code(security)
    price = _get_current_price(ctx, jq_code)
    if price <= 0:
        return None
    target_qty = _round_to_lot(value / price)
    return _order_target(ctx, jq_code, target_qty)


def _order_target_percent(ctx: JQContext, security: str, percent: float) -> OrderRecord | None:
    """order_target_percent(security, percent) — 调仓到总资产的目标比例。

    percent: 0.0 ~ 1.0
    """
    total = ctx.portfolio.total_value
    return _order_target_value(ctx, security, total * percent)


def _cancel_order(ctx: JQContext, order_id: str) -> None:
    """cancel_order(order_id) — 撤单（回测中所有单日内即时成交，此处为空操作）。"""
    pass


# ─────────────────────── 配置函数 ───────────────────────

def _set_benchmark(ctx: JQContext, security: str) -> None:
    ctx.benchmark = to_jq_code(security)


def _set_commission(ctx: JQContext, commission) -> None:
    """set_commission(PerTrade(...)) 或直接传浮点比例。"""
    if hasattr(commission, "open_tax"):
        ctx.commission_ratio = getattr(commission, "open_tax", 0.00025)
    elif isinstance(commission, (int, float)):
        ctx.commission_ratio = float(commission)


def _set_slippage(ctx: JQContext, slippage) -> None:
    """set_slippage(PriceRelatedSlippage(0.001)) 或直接传浮点。"""
    if hasattr(slippage, "value"):
        ctx.slippage = float(slippage.value)
    elif isinstance(slippage, (int, float)):
        ctx.slippage = float(slippage)


def _set_universe(ctx: JQContext, security_list: list[str]) -> None:
    ctx.universe = [to_jq_code(s) for s in security_list]


# ─────────────────────── 数据函数包装（绑定 end_date） ───────────────────────

def _make_data_funcs(ctx: JQContext):
    """返回与 ctx.current_dt 绑定的数据访问函数（用于回测时间隔离）。"""
    end_date = ctx.current_dt.strftime("%Y-%m-%d")

    def get_price(security, start_date=None, end_date_=end_date,
                  frequency="daily", fields=None, count=None, panel=True):
        return _get_price(security, start_date=start_date, end_date=end_date_,
                          frequency=frequency, fields=fields, count=count, panel=panel)

    def attribute_history(security, count, unit="1d", fields=None):
        return _attr_hist(security, count, unit=unit, fields=fields, end_date=end_date)

    def history(count, unit="1d", field="close", security_list=None):
        return _history(count, unit=unit, field=field,
                        security_list=security_list, end_date=end_date)

    def get_current_data(security_list=None):
        codes = security_list or list(ctx.portfolio.positions.keys())
        return _get_current(codes, end_date=end_date)

    return get_price, attribute_history, history, get_current_data


# ─────────────────────── 聚宽 PerTrade / Slippage 兼容类 ───────────────────────

class PerTrade:
    """set_commission(PerTrade(open_tax=0.00025, close_tax=0.0001))"""
    def __init__(self, open_tax: float = 0.00025, close_tax: float = 0.0001,
                 close_today_tax: float = 0.0):
        self.open_tax = open_tax
        self.close_tax = close_tax
        self.close_today_tax = close_today_tax


class PriceRelatedSlippage:
    """set_slippage(PriceRelatedSlippage(0.001))"""
    def __init__(self, value: float = 0.001):
        self.value = value


class FixedSlippage:
    """set_slippage(FixedSlippage(0.02))"""
    def __init__(self, value: float = 0.02):
        self.value = value


# ─────────────────────── 命名空间构建 ───────────────────────

def build_api_namespace(ctx: JQContext) -> dict[str, Any]:
    """构建注入用户代码的全局命名空间。"""
    get_price, attribute_history, history, get_current_data = _make_data_funcs(ctx)

    ns: dict[str, Any] = {
        # 下单
        "order":                 lambda s, a: _order(ctx, s, int(a)),
        "order_value":           lambda s, v: _order_value(ctx, s, float(v)),
        "order_target":          lambda s, a: _order_target(ctx, s, int(a)),
        "order_target_value":    lambda s, v: _order_target_value(ctx, s, float(v)),
        "order_target_percent":  lambda s, p: _order_target_percent(ctx, s, float(p)),
        "cancel_order":          lambda oid: _cancel_order(ctx, oid),

        # 数据
        "get_price":             get_price,
        "attribute_history":     attribute_history,
        "history":               history,
        "get_current_data":      get_current_data,
        "get_all_securities":    _get_all_sec,
        "get_index_stocks":      _get_index,

        # 配置
        "set_benchmark":         lambda s: _set_benchmark(ctx, s),
        "set_commission":        lambda c: _set_commission(ctx, c),
        "set_slippage":          lambda s: _set_slippage(ctx, s),
        "set_universe":          lambda lst: _set_universe(ctx, lst),

        # 手续费/滑点类
        "PerTrade":              PerTrade,
        "PriceRelatedSlippage":  PriceRelatedSlippage,
        "FixedSlippage":         FixedSlippage,

        # 日志
        "log":                   _Logger(ctx),

        # 常用工具
        "math":                  math,
    }
    return ns


def refresh_api_namespace(ns: dict[str, Any], ctx: JQContext) -> None:
    """每个交易日开始时刷新数据函数的 end_date 绑定。"""
    get_price, attribute_history, history, get_current_data = _make_data_funcs(ctx)
    ns["get_price"]         = get_price
    ns["attribute_history"] = attribute_history
    ns["history"]           = history
    ns["get_current_data"]  = get_current_data
    ns["log"]               = _Logger(ctx)
