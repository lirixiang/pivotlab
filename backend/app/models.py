from datetime import datetime
from sqlalchemy import Boolean, String, Float, Integer, Text, DateTime, JSON, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


# --------------- Stock master list ---------------
class Stock(Base):
    __tablename__ = "stocks"

    code: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    industry: Mapped[str] = mapped_column(String(50), default="")
    market: Mapped[str] = mapped_column(String(20), default="")       # 沪A / 深A / 创业板 / 科创板
    is_st: Mapped[bool] = mapped_column(Boolean, default=False)
    list_date: Mapped[str] = mapped_column(String(10), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# --------------- Fundamental snapshot ---------------
class FinancialSnapshot(Base):
    __tablename__ = "financial_snapshots"

    code: Mapped[str] = mapped_column(String(10), primary_key=True)
    report_period: Mapped[str] = mapped_column(String(10), default="")
    eps_ttm: Mapped[float] = mapped_column(Float, default=0.0)
    roe: Mapped[float] = mapped_column(Float, default=0.0)
    revenue_yoy: Mapped[float] = mapped_column(Float, default=0.0)
    net_profit_yoy: Mapped[float] = mapped_column(Float, default=0.0)
    pe_ratio_ttm: Mapped[float] = mapped_column(Float, default=0.0)
    total_revenue: Mapped[float] = mapped_column(Float, default=0.0)
    net_profit: Mapped[float] = mapped_column(Float, default=0.0)
    fundamental_status: Mapped[str] = mapped_column(String(20), default="unknown")  # healthy/neutral/weak/risk/unknown
    fundamental_summary: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# --------------- Stock concepts / themes ---------------
class StockConcept(Base):
    __tablename__ = "stock_concepts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(10), nullable=False)
    concept: Mapped[str] = mapped_column(String(80), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("code", "concept"),
        Index("idx_stock_concepts_code", "code"),
    )


# --------------- Historical financial reports ---------------
class FinancialHistory(Base):
    __tablename__ = "financial_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(10), nullable=False)
    report_period: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD (quarter end)
    eps: Mapped[float] = mapped_column(Float, default=0.0)
    roe: Mapped[float] = mapped_column(Float, default=0.0)
    revenue: Mapped[float] = mapped_column(Float, default=0.0)       # total revenue (yuan)
    net_profit: Mapped[float] = mapped_column(Float, default=0.0)    # net profit (yuan)
    revenue_yoy: Mapped[float] = mapped_column(Float, default=0.0)   # revenue yoy growth %
    net_profit_yoy: Mapped[float] = mapped_column(Float, default=0.0)  # net profit yoy growth %
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("code", "report_period"),
        Index("idx_fin_history_code", "code"),
        Index("idx_fin_history_period", "report_period"),
    )


# --------------- Sync task tracking ---------------
class SyncTask(Base):
    __tablename__ = "sync_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_type: Mapped[str] = mapped_column(String(30))   # stocks/candles/quotes/financials/concepts
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/running/done/error
    total: Mapped[int] = mapped_column(Integer, default=0)
    processed: Mapped[int] = mapped_column(Integer, default=0)
    error_msg: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)


# --------------- Existing models (unchanged) ---------------
class WatchlistItem(Base):
    __tablename__ = "watchlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    note: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DailyCandle(Base):
    __tablename__ = "daily_candles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(10), nullable=False)
    trade_date: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    # -- Quote fields (populated for today's row by sync_quotes) --
    amount: Mapped[float] = mapped_column(Float, nullable=True)
    change_pct: Mapped[float] = mapped_column(Float, nullable=True)
    change_amt: Mapped[float] = mapped_column(Float, nullable=True)
    prev_close: Mapped[float] = mapped_column(Float, nullable=True)
    turnover_rate: Mapped[float] = mapped_column(Float, nullable=True)
    pe_ratio: Mapped[float] = mapped_column(Float, nullable=True)
    market_cap: Mapped[float] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("code", "trade_date"),
        Index("idx_candles_code_date", "code", "trade_date"),
    )


class ScanResult(Base):
    __tablename__ = "scan_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    pattern: Mapped[str] = mapped_column(String(32), index=True)  # breakout_pullback / bottom_stabilize
    score: Mapped[float] = mapped_column(Float, default=0.0)
    price: Mapped[float] = mapped_column(Float, default=0.0)
    change_pct: Mapped[float] = mapped_column(Float, default=0.0)
    volume_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    scanned_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


# --------------- Analyst consensus (target price / rating) ---------------
class AnalystConsensus(Base):
    __tablename__ = "analyst_consensus"

    code: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(String(50), default="")
    target_price_high: Mapped[float] = mapped_column(Float, nullable=True)
    target_price_low: Mapped[float] = mapped_column(Float, nullable=True)
    analyst_count: Mapped[int] = mapped_column(Integer, default=0)
    buy_count: Mapped[int] = mapped_column(Integer, default=0)
    overweight_count: Mapped[int] = mapped_column(Integer, default=0)
    neutral_count: Mapped[int] = mapped_column(Integer, default=0)
    underweight_count: Mapped[int] = mapped_column(Integer, default=0)
    sell_count: Mapped[int] = mapped_column(Integer, default=0)
    eps_current_year: Mapped[float] = mapped_column(Float, nullable=True)
    eps_next_year: Mapped[float] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# --------------- User settings (persistent config) ---------------
class UserSettings(Base):
    __tablename__ = "user_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
