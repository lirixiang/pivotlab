"""JoinQuant 风格回测主循环。

执行流程（对齐聚宽）：
  1. 加载交易日历 + 批量预热 K 线数据
  2. 调用 initialize(context)
  3. 逐日循环：
       a. context._advance_to(dt, prices)    — 更新时间 + 持仓市价
       b. before_trading_start(context, data) — 可选
       c. handle_data(context, data)          — 核心逻辑（下单在此发生）
       d. after_trading_end(context, data)    — 可选
       e. T+1 处理：今日买入 → closeable_amount 更新
       f. 记录当日净值快照

返回 dict，结构与 M4 回测保持兼容：
    {
      "equity_curve":  [{date, equity, cash, positions_value, drawdown}],
      "trades":        [{date, security, side, qty, price, commission, pnl}],
      "stats":         {total_return, annual_return, max_drawdown, sharpe, ...},
      "logs":          [{level, dt, msg}],
    }
"""
from __future__ import annotations

import logging
import time
import traceback
from datetime import datetime
from typing import Any, Callable

import numpy as np

from .api import build_api_namespace, refresh_api_namespace
from .context import JQContext, RunParams
from .data_bridge import bulk_load_candles, get_trading_dates, to_jq_code
from .executor import CodeValidationError, extract_callbacks

logger = logging.getLogger(__name__)


# ─────────────────────── data 代理对象 ───────────────────────

class _DataProxy:
    """handle_data(context, data) 中的 data 对象。

    data[security] 返回一个有 .current() / .history() 的对象。
    由于已在命名空间中提供 get_price / attribute_history，
    这里仅提供简单的 current 价格访问即可。
    """

    def __init__(self, prices: dict[str, float]):
        self._prices = prices  # {jq_code: close_price}

    def current(self, security: str, field: str = "price") -> float:
        jq = to_jq_code(security)
        return self._prices.get(jq, 0.0)

    def __getitem__(self, security: str):
        jq = to_jq_code(security)
        price = self._prices.get(jq, 0.0)

        class _Bar:
            last_price = price
            high_limit = round(price * 1.1, 2)
            low_limit  = round(price * 0.9, 2)
            paused = (price == 0.0)

        return _Bar()


# ─────────────────────── 指标计算 ───────────────────────

def _compute_stats(
    equity_curve: list[dict],
    initial_cash: float,
) -> dict[str, Any]:
    if not equity_curve:
        return {}

    equities = np.array([e["equity"] for e in equity_curve])
    n = len(equities)
    total_return = (equities[-1] / initial_cash - 1) * 100

    # 年化收益（按 252 交易日）
    annual_return = ((equities[-1] / initial_cash) ** (252.0 / max(n, 1)) - 1) * 100

    # 最大回撤
    peak = np.maximum.accumulate(equities)
    drawdowns = (equities - peak) / peak
    max_drawdown = float(drawdowns.min()) * 100

    # 日收益率
    daily_returns = np.diff(equities) / equities[:-1]
    sharpe = 0.0
    if daily_returns.std() > 0:
        sharpe = float(daily_returns.mean() / daily_returns.std() * np.sqrt(252))

    return {
        "total_return":  round(total_return, 3),
        "annual_return": round(annual_return, 3),
        "max_drawdown":  round(max_drawdown, 3),
        "sharpe":        round(sharpe, 3),
        "total_days":    n,
        "initial_cash":  initial_cash,
        "final_equity":  round(float(equities[-1]), 2),
    }


# ─────────────────────── 主回测函数 ───────────────────────

def run_jq_backtest(
    code: str,
    start_date: str,
    end_date: str,
    initial_cash: float = 1_000_000.0,
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    """执行一次 JQ 风格回测。

    code: 用户策略代码字符串
    返回: 与 M4 结果兼容的 dict
    """
    t0 = time.time()

    # ── 1. 构建 context ──
    run_params = RunParams(
        start_date=start_date,
        end_date=end_date,
        initial_cash=initial_cash,
    )
    ctx = JQContext(run_params, initial_cash)

    # ── 2. 构建 API 命名空间 + 提取回调 ──
    ns = build_api_namespace(ctx)
    try:
        cbs = extract_callbacks(code, ns)
    except CodeValidationError as e:
        return {"error": str(e), "equity_curve": [], "trades": [], "stats": {}, "logs": []}

    initialize           = cbs["initialize"]
    handle_data          = cbs["handle_data"]
    before_trading_start = cbs["before_trading_start"]
    after_trading_end    = cbs["after_trading_end"]

    if handle_data is None:
        return {
            "error": "策略代码中未找到 handle_data(context, data) 函数",
            "equity_curve": [], "trades": [], "stats": {}, "logs": [],
        }

    # ── 3. 调用 initialize ──
    if initialize:
        try:
            initialize(ctx)
        except Exception as e:
            return {
                "error": f"initialize 执行出错：{e}\n{traceback.format_exc()}",
                "equity_curve": [], "trades": [], "stats": {}, "logs": ctx._logs,
            }

    # ── 4. 获取交易日历 ──
    # 预热需要更多历史数据（供均线等指标计算），提前 300 个自然日加载
    from .data_bridge import _offset_date
    warmup_start = _offset_date(start_date, -400)
    trading_dates = get_trading_dates(start_date, end_date)

    if not trading_dates:
        return {
            "error": f"区间 {start_date}~{end_date} 内无交易日数据",
            "equity_curve": [], "trades": [], "stats": {}, "logs": ctx._logs,
        }

    # ── 5. 批量预热 K 线 ──
    logger.info("[jq_backtest] 预热 K 线 %s ~ %s ...", warmup_start, end_date)
    series_map = bulk_load_candles(warmup_start, end_date)
    logger.info("[jq_backtest] 加载 %d 只股票 K 线，%d 个交易日", len(series_map), len(trading_dates))

    # ── 6. 逐日循环 ──
    equity_curve: list[dict] = []
    trades_list: list[dict] = []
    total = len(trading_dates)

    for i, trade_date in enumerate(trading_dates):
        if progress_cb:
            progress_cb(i, total, trade_date)

        # 当日收盘价字典 {jq_code: close}
        prices: dict[str, float] = {}
        for db_code, s in series_map.items():
            # 找到该日在 dates 中的索引
            dates = s["dates"]
            # 二分查找
            lo, hi = 0, len(dates)
            while lo < hi:
                mid = (lo + hi) // 2
                if dates[mid] <= trade_date:
                    lo = mid + 1
                else:
                    hi = mid
            idx = lo - 1
            if 0 <= idx < len(dates) and dates[idx] == trade_date:
                prices[to_jq_code(db_code)] = float(s["close"][idx])

        # 推进 context 时间轴 + 刷新持仓市价
        dt = datetime.strptime(trade_date, "%Y-%m-%d")
        ctx._advance_to(dt, prices)

        # 刷新 API 命名空间中的数据函数（绑定新的 end_date）
        refresh_api_namespace(ns, ctx)

        data_proxy = _DataProxy(prices)

        # before_trading_start
        if before_trading_start:
            try:
                before_trading_start(ctx, data_proxy)
            except Exception as e:
                ctx._logs.append({
                    "level": "ERROR",
                    "dt": trade_date,
                    "msg": f"before_trading_start 出错: {e}",
                })

        # handle_data（核心）
        try:
            handle_data(ctx, data_proxy)
        except Exception as e:
            ctx._logs.append({
                "level": "ERROR",
                "dt": trade_date,
                "msg": f"handle_data 出错: {e}\n{traceback.format_exc()}",
            })

        # after_trading_end
        if after_trading_end:
            try:
                after_trading_end(ctx, data_proxy)
            except Exception as e:
                ctx._logs.append({
                    "level": "ERROR",
                    "dt": trade_date,
                    "msg": f"after_trading_end 出错: {e}",
                })

        # T+1：今日买入的持仓解锁为可卖
        for jq_code in ctx._today_bought:
            pos = ctx.portfolio.positions.get(jq_code)
            if pos:
                pos.closeable_amount = pos.total_amount

        # 收集当日成交记录
        for rec in ctx._orders:
            if rec.created_dt.strftime("%Y-%m-%d") == trade_date:
                # 查持仓均价计算已实现盈亏（卖出时才有）
                pnl_val = None
                if rec.side == "sell":
                    pnl_val = round((rec.price - 0) * abs(rec.filled) - rec.commission, 2)
                trades_list.append({
                    "date":       trade_date,
                    "security":   rec.security,
                    "side":       rec.side,
                    "qty":        abs(rec.filled),
                    "price":      round(rec.price, 3),
                    "commission": round(rec.commission, 2),
                    "amount":     round(abs(rec.filled) * rec.price, 2),
                })

        # 净值快照
        pf = ctx.portfolio
        total_val = pf.total_value
        positions_val = pf.positions_value
        equity_curve.append({
            "date":             trade_date,
            "equity":           round(total_val, 2),
            "cash":             round(pf.cash, 2),
            "positions_value":  round(positions_val, 2),
            "n_positions":      len(pf.positions),
            "drawdown":         0.0,  # 填充后计算
        })

    # ── 7. 计算回撤 ──
    if equity_curve:
        equities = np.array([e["equity"] for e in equity_curve])
        peak = np.maximum.accumulate(equities)
        drawdowns = (equities - peak) / np.maximum(peak, 1e-9)
        for i, e in enumerate(equity_curve):
            e["drawdown"] = round(float(drawdowns[i]) * 100, 3)

    stats = _compute_stats(equity_curve, initial_cash)
    stats["elapsed_sec"] = round(time.time() - t0, 2)
    stats["trade_count"] = len(trades_list)

    return {
        "equity_curve": equity_curve,
        "trades":       trades_list,
        "stats":        stats,
        "logs":         ctx._logs[-500:],  # 最多返回最后 500 条日志
    }
