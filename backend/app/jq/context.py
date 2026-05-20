"""JoinQuant 兼容的 Context 体系。

用户代码通过 context 对象访问账户状态：
    context.portfolio.cash
    context.portfolio.total_value
    context.portfolio.positions['000001.XSHE'].total_amount
    context.current_dt          -> datetime
    context.run_params          -> RunParams
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


# ─────────────────────── 持仓 ───────────────────────

@dataclass
class JQPosition:
    """单个持仓，对齐聚宽 Position 对象。"""
    security: str           # e.g. '000001.XSHE'
    total_amount: int       # 持有股数
    closeable_amount: int   # 可卖股数（T+1：今日买入的不可卖）
    avg_cost: float         # 持仓均价（含手续费）
    price: float            # 当前市价
    acc_avg_cost: float     # 累计均价（记录一直以来的平均成本）
    side: str = "long"      # 目前只支持 long

    @property
    def value(self) -> float:
        return self.total_amount * self.price

    @property
    def init_time(self) -> str:
        return ""

    def __repr__(self) -> str:
        return (
            f"Position({self.security} qty={self.total_amount} "
            f"avg={self.avg_cost:.3f} price={self.price:.3f})"
        )


# ─────────────────────── 组合 ───────────────────────

@dataclass
class JQPortfolio:
    """账户组合，对齐聚宽 Portfolio 对象。"""
    cash: float
    starting_cash: float
    positions: dict[str, JQPosition] = field(default_factory=dict)
    # 当日已实现收益（平仓盈亏，含手续费）
    pnl: float = 0.0
    # 当日未实现收益
    returns: float = 0.0

    @property
    def total_value(self) -> float:
        pos_value = sum(p.value for p in self.positions.values())
        return self.cash + pos_value

    @property
    def positions_value(self) -> float:
        return sum(p.value for p in self.positions.values())

    def __repr__(self) -> str:
        return (
            f"Portfolio(cash={self.cash:.2f} "
            f"total={self.total_value:.2f} "
            f"positions={len(self.positions)})"
        )


# ─────────────────────── 运行参数 ───────────────────────

@dataclass
class RunParams:
    """context.run_params"""
    start_date: str             # 'YYYY-MM-DD'
    end_date: str
    frequency: str = "daily"    # 'daily' / 'minute'
    initial_cash: float = 1_000_000.0


# ─────────────────────── 委托记录 ───────────────────────

@dataclass
class OrderRecord:
    """内部委托记录，不直接暴露给用户（聚宽的 order() 返回 Order 对象，这里对齐）。"""
    order_id: str
    security: str
    amount: int          # 正=买 负=卖
    filled: int
    price: float         # 成交价
    status: str          # 'open' / 'filled' / 'cancelled'
    created_dt: datetime
    side: str            # 'buy' / 'sell'
    commission: float = 0.0


# ─────────────────────── 主 Context ───────────────────────

class JQContext:
    """回测/实盘运行时的上下文，注入用户策略。

    用户可在 initialize(context) 中随意绑定自定义属性：
        context.my_stock = '000001.XSHE'
    """

    def __init__(self, run_params: RunParams, initial_cash: float):
        self.run_params: RunParams = run_params
        self.portfolio: JQPortfolio = JQPortfolio(
            cash=initial_cash,
            starting_cash=initial_cash,
        )
        self.current_dt: datetime = datetime.strptime(run_params.start_date, "%Y-%m-%d")
        self.universe: list[str] = []          # set_universe 设置的股票池
        self.benchmark: str = "000300.XSHG"   # 基准
        self.commission_ratio: float = 0.00025 # 手续费率（单边）
        self.slippage: float = 0.001           # 滑点
        self.min_commission: float = 5.0       # 最低手续费（元）

        # 内部状态
        self._orders: list[OrderRecord] = []
        self._today_bought: set[str] = set()   # T+1：今日买入，不可当日卖
        self._logs: list[dict[str, Any]] = []
        self._pending_orders: list[dict] = []  # 当日待撮合委托

    def _advance_to(self, dt: datetime, prices: dict[str, float]) -> None:
        """推进到新交易日：更新当前时间、刷新持仓市价。"""
        self.current_dt = dt
        self._today_bought = set()
        self._pending_orders = []
        # 刷新持仓市价
        for code, pos in self.portfolio.positions.items():
            if code in prices:
                pos.price = prices[code]

    def _open_positions(self) -> dict[str, JQPosition]:
        """持有股数 > 0 的仓位。"""
        return {k: v for k, v in self.portfolio.positions.items() if v.total_amount > 0}
