"""交易系统数据模型 (M1 + M3 + M4 + M5)

M1: quant_systems         — 一个完整的系统配置
M3: quant_system_runs     — 每次 daily_run 的全留痕
M4: quant_backtests       — 历史回测结果
M5: quant_positions       — 实盘持仓 (open/closed)
M5: quant_nav_daily       — 实盘每日净值快照
"""
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class QuantSystem(Base):
    """一个完整的交易系统 = 选股 + 信号 + 风控 + 执行 配置的快照。"""

    __tablename__ = "quant_systems"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="draft")

    universe_cfg: Mapped[dict] = mapped_column(JSON, default=dict)
    signal_cfg: Mapped[dict] = mapped_column(JSON, default=dict)
    risk_cfg: Mapped[dict] = mapped_column(JSON, default=dict)
    exec_cfg: Mapped[dict] = mapped_column(JSON, default=dict)

    initial_capital: Mapped[float] = mapped_column(Float, default=1000000.0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class QuantSystemRun(Base):
    """每次跑 Pipeline 的留痕（实盘日跑 or 回测的某日切片）。

    candidates: list[{code, name, last_close, last_date}]
    signals:    list[{code, side: "buy"/"sell", price, date, rules: [...]}]
    orders:     list[{code, action, qty, price, stop_price, est_loss, reason}]
    metrics:    回测才填（M4）
    """

    __tablename__ = "quant_system_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    system_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("quant_systems.id", ondelete="CASCADE"), nullable=False
    )
    run_type: Mapped[str] = mapped_column(String(20), default="live_daily")  # live_daily / backtest
    trade_date: Mapped[str] = mapped_column(String(10), nullable=False)      # YYYY-MM-DD

    universe_count: Mapped[int] = mapped_column(Integer, default=0)          # 选股层最终候选数
    signal_count: Mapped[int] = mapped_column(Integer, default=0)            # 触发买入的信号数
    order_count: Mapped[int] = mapped_column(Integer, default=0)             # 风控后的委托数

    candidates: Mapped[list] = mapped_column(JSON, default=list)
    signals: Mapped[list] = mapped_column(JSON, default=list)
    orders: Mapped[list] = mapped_column(JSON, default=list)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)

    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_quant_runs_system_date", "system_id", "trade_date"),
    )


class QuantBacktest(Base):
    """M4: 一次历史回测的完整记录。

    equity_curve: list[{date, equity, cash, positions_value, n_positions, drawdown}]
    trades:       list[{code, name, side: open/close, qty, price, date, pnl, reason}]
    positions_end: list[{code, qty, entry_price, last_price, market_value, pnl_pct}]
    metrics:      {total_return_pct, cagr_pct, max_drawdown_pct, sharpe, win_rate_pct,
                   trade_count, win_count, loss_count, avg_win_pct, avg_loss_pct,
                   profit_factor, exposure_pct, final_equity}
    """

    __tablename__ = "quant_backtests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    system_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("quant_systems.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), default="")

    start_date: Mapped[str] = mapped_column(String(10), nullable=False)
    end_date: Mapped[str] = mapped_column(String(10), nullable=False)
    initial_capital: Mapped[float] = mapped_column(Float, default=1000000.0)

    # 系统配置在回测时刻的快照（用户后续改了系统，老回测仍可重看）
    system_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    params: Mapped[dict] = mapped_column(JSON, default=dict)   # {commission_bps, slippage_bps, fill_price_mode}

    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/running/done/failed

    equity_curve: Mapped[list] = mapped_column(JSON, default=list)
    trades: Mapped[list] = mapped_column(JSON, default=list)
    positions_end: Mapped[list] = mapped_column(JSON, default=list)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)

    trading_days: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_quant_backtests_system", "system_id", "created_at"),
    )


class QuantPosition(Base):
    """M5: 实盘持仓 / 已平仓记录。

    open 状态：qty>0，exit_* 为空。
    closed 状态：qty=入场手数，exit_price/date 已填，pnl 已结算。
    """

    __tablename__ = "quant_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    system_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("quant_systems.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(50), default="")

    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    entry_date: Mapped[str] = mapped_column(String(10), nullable=False)
    stop_price: Mapped[float] = mapped_column(Float, default=0.0)
    cost_basis: Mapped[float] = mapped_column(Float, nullable=False)  # qty*price + 手续费
    commission_in: Mapped[float] = mapped_column(Float, default=0.0)

    status: Mapped[str] = mapped_column(String(10), default="open")   # open / closed

    exit_price: Mapped[float] = mapped_column(Float, default=0.0)
    exit_date: Mapped[str] = mapped_column(String(10), default="")
    exit_reason: Mapped[str] = mapped_column(String(100), default="")
    commission_out: Mapped[float] = mapped_column(Float, default=0.0)
    pnl: Mapped[float] = mapped_column(Float, default=0.0)
    pnl_pct: Mapped[float] = mapped_column(Float, default=0.0)
    hold_days: Mapped[int] = mapped_column(Integer, default=0)

    # 关联到生成它的 run / order
    source_run_id: Mapped[int] = mapped_column(Integer, default=0)
    source_order_index: Mapped[int] = mapped_column(Integer, default=-1)
    notes: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        Index("ix_quant_positions_system_status", "system_id", "status"),
        Index("ix_quant_positions_system_code", "system_id", "code"),
    )


class QuantNavDaily(Base):
    """M5: 实盘每日净值快照。

    可由 /nav/snapshot 端点调用生成或更新；触发条件：每天收盘后手动 / 跑 daily_run 时自动。
    """

    __tablename__ = "quant_nav_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    system_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("quant_systems.id", ondelete="CASCADE"), nullable=False
    )
    trade_date: Mapped[str] = mapped_column(String(10), nullable=False)

    cash: Mapped[float] = mapped_column(Float, default=0.0)
    positions_value: Mapped[float] = mapped_column(Float, default=0.0)
    equity: Mapped[float] = mapped_column(Float, default=0.0)
    n_positions: Mapped[int] = mapped_column(Integer, default=0)

    realized_pnl_total: Mapped[float] = mapped_column(Float, default=0.0)   # 累计已实现
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    drawdown_pct: Mapped[float] = mapped_column(Float, default=0.0)

    snapshot: Mapped[list] = mapped_column(JSON, default=list)              # 当日持仓快照（mark to market）

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_quant_nav_system_date", "system_id", "trade_date", unique=True),
    )


class QuantTemplate(Base):
    """用户自定义策略模板。内置模板在 defaults.py 中，这里存用户创建 / 保存的模板。"""

    __tablename__ = "quant_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    emoji: Mapped[str] = mapped_column(String(10), default="📋")
    desc: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    config: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
