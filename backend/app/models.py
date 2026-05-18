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


# --------------- Concept boards (板块元数据) ---------------
class ConceptBoard(Base):
    __tablename__ = "concept_boards"

    board_code: Mapped[str] = mapped_column(String(20), primary_key=True)   # e.g. 301558
    concept: Mapped[str] = mapped_column(String(80), nullable=False)
    change_pct_1d: Mapped[float] = mapped_column(Float, nullable=True)
    change_pct_5d: Mapped[float] = mapped_column(Float, nullable=True)
    net_inflow: Mapped[float] = mapped_column(Float, nullable=True)         # 主力净流入 (元)
    rank: Mapped[int] = mapped_column(Integer, nullable=True)               # 当日涨幅排名 (1=最热)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# --------------- Stock concepts / themes ---------------
class StockConcept(Base):
    __tablename__ = "stock_concepts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(10), nullable=False)
    concept: Mapped[str] = mapped_column(String(80), nullable=False)
    board_code: Mapped[str] = mapped_column(String(20), nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="eastmoney")
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


# ============================================================
#  NEW: Recommendation Engine (v2 strategy layer)
# ============================================================

class Recommendation(Base):
    """A single stock recommendation produced by the recommender for one style on one day."""
    __tablename__ = "recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(50), default="")
    style: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # short_term/swing/value/multi_factor
    score: Mapped[float] = mapped_column(Float, default=0.0)        # 0–100 composite score
    rank: Mapped[int] = mapped_column(Integer, default=0)
    price: Mapped[float] = mapped_column(Float, default=0.0)        # snapshot price at scan time
    industry: Mapped[str] = mapped_column(String(50), default="")
    concept: Mapped[str] = mapped_column(String(120), default="")   # leading concept tag
    reasons: Mapped[list] = mapped_column(JSON, default=list)       # human-readable reason tags
    factors: Mapped[dict] = mapped_column(JSON, default=dict)       # factor-score breakdown
    scan_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)  # YYYY-MM-DD
    expires_date: Mapped[str] = mapped_column(String(10), default="")
    status: Mapped[str] = mapped_column(String(16), default="active")  # active/expired/triggered
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("code", "style", "scan_date", name="uq_reco_code_style_date"),
        Index("idx_reco_style_date", "style", "scan_date"),
    )


class TradePlan(Base):
    """Concrete buy/sell plan attached to a Recommendation."""
    __tablename__ = "trade_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recommendation_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    style: Mapped[str] = mapped_column(String(20), nullable=False)
    # ── 入场 ──
    buy_low: Mapped[float] = mapped_column(Float)        # 买入价区间下沿
    buy_high: Mapped[float] = mapped_column(Float)       # 买入价区间上沿
    buy_trigger: Mapped[str] = mapped_column(String(40), default="")  # e.g. "回踩MA10", "突破阻力20.5"
    # ── 出场 ──
    stop_loss: Mapped[float] = mapped_column(Float)
    take_profit_1: Mapped[float] = mapped_column(Float)  # 第一目标(部分止盈)
    take_profit_2: Mapped[float] = mapped_column(Float)  # 第二目标(全部止盈)
    # ── 仓位 & 时间 ──
    position_pct: Mapped[float] = mapped_column(Float)   # 建议仓位 0–1 (单只占总资金比)
    holding_days_min: Mapped[int] = mapped_column(Integer, default=1)
    holding_days_max: Mapped[int] = mapped_column(Integer, default=20)
    # ── 风险 ──
    risk_reward: Mapped[float] = mapped_column(Float, default=0.0)  # (TP1-buy_mid)/(buy_mid-SL)
    atr_pct: Mapped[float] = mapped_column(Float, default=0.0)      # ATR / price
    confidence: Mapped[float] = mapped_column(Float, default=0.0)   # 0–100
    # ── 解释 ──
    reason: Mapped[str] = mapped_column(Text, default="")
    factors: Mapped[dict] = mapped_column(JSON, default=dict)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_plan_code_style", "code", "style"),
    )


# ═══════════════════════════════════════════════════════════════
#  Recommendation lifecycle tracking
# ═══════════════════════════════════════════════════════════════

class RecommendationOutcome(Base):
    """Tracks each recommendation as it plays out in the market.

    Created lazily by the lifecycle cron the first time a reco is observed
    after being persisted; updated daily until terminal state.
    """
    __tablename__ = "recommendation_outcomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recommendation_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True, unique=True)
    code: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    style: Mapped[str] = mapped_column(String(20), nullable=False)
    scan_date: Mapped[str] = mapped_column(String(10), nullable=False)
    expires_date: Mapped[str] = mapped_column(String(10), default="")
    # plan snapshot at creation (immutable)
    buy_low: Mapped[float] = mapped_column(Float, default=0.0)
    buy_high: Mapped[float] = mapped_column(Float, default=0.0)
    stop_loss: Mapped[float] = mapped_column(Float, default=0.0)
    take_profit_1: Mapped[float] = mapped_column(Float, default=0.0)
    take_profit_2: Mapped[float] = mapped_column(Float, default=0.0)
    initial_price: Mapped[float] = mapped_column(Float, default=0.0)
    # state machine: pending → triggered → tp1/tp2/stopped/expired
    state: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    triggered_date: Mapped[str] = mapped_column(String(10), default="")
    triggered_price: Mapped[float] = mapped_column(Float, default=0.0)
    exit_date: Mapped[str] = mapped_column(String(10), default="")
    exit_price: Mapped[float] = mapped_column(Float, default=0.0)
    exit_reason: Mapped[str] = mapped_column(String(20), default="")
    max_favorable_pct: Mapped[float] = mapped_column(Float, default=0.0)
    max_adverse_pct: Mapped[float] = mapped_column(Float, default=0.0)
    realized_return_pct: Mapped[float] = mapped_column(Float, default=0.0)
    days_to_trigger: Mapped[int] = mapped_column(Integer, default=0)
    days_held: Mapped[int] = mapped_column(Integer, default=0)
    last_checked_date: Mapped[str] = mapped_column(String(10), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ═══════════════════════════════════════════════════════════════
#  Index k-line storage (大盘指数日线)
# ═══════════════════════════════════════════════════════════════

class IndexCandle(Base):
    __tablename__ = "index_candles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    trade_date: Mapped[str] = mapped_column(String(10), nullable=False)
    open: Mapped[float] = mapped_column(Float, default=0.0)
    high: Mapped[float] = mapped_column(Float, default=0.0)
    low: Mapped[float] = mapped_column(Float, default=0.0)
    close: Mapped[float] = mapped_column(Float, default=0.0)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    pct_change: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("code", "trade_date", name="uq_idx_code_date"),
        Index("idx_idx_code_date", "code", "trade_date"),
    )


# ═══════════════════════════════════════════════════════════════
#  龙头战法 (Dragon Head Strategy) tables
# ═══════════════════════════════════════════════════════════════

class ZtPoolDaily(Base):
    """涨停池日快照 - daily snapshot of limit-up/-down stocks."""
    __tablename__ = "zt_pool_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[str] = mapped_column(String(10), nullable=False)   # YYYY-MM-DD
    code: Mapped[str] = mapped_column(String(10), nullable=False)
    name: Mapped[str] = mapped_column(String(20), default="")
    pool_type: Mapped[str] = mapped_column(String(10), default="zt")     # zt(涨停) / dt(跌停) / zb(炸板)
    change_pct: Mapped[float] = mapped_column(Float, nullable=True)
    close: Mapped[float] = mapped_column(Float, nullable=True)
    amount: Mapped[float] = mapped_column(Float, nullable=True)          # 成交额 (元)
    market_cap: Mapped[float] = mapped_column(Float, nullable=True)      # 流通市值 (元)
    turnover_rate: Mapped[float] = mapped_column(Float, nullable=True)
    first_zt_time: Mapped[str] = mapped_column(String(10), default="")   # 首次封板时间 HH:MM:SS
    last_zt_time: Mapped[str] = mapped_column(String(10), default="")
    open_count: Mapped[int] = mapped_column(Integer, default=0)          # 开板次数
    seal_amount: Mapped[float] = mapped_column(Float, nullable=True)     # 封板资金 (元)
    zt_status: Mapped[str] = mapped_column(String(10), default="")       # 封板/炸板/T字
    consecutive: Mapped[int] = mapped_column(Integer, default=1)         # 连板天数
    concept: Mapped[str] = mapped_column(Text, default="")               # 涨停原因/题材
    industry: Mapped[str] = mapped_column(String(50), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("trade_date", "code", "pool_type", name="uq_zt_pool_date_code_type"),
        Index("idx_zt_pool_date", "trade_date"),
        Index("idx_zt_pool_code", "code"),
        Index("idx_zt_pool_consecutive", "consecutive"),
    )


class LhbRecord(Base):
    """龙虎榜记录 - daily Dragon-Tiger list (institutional/hot money disclosure)."""
    __tablename__ = "lhb_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[str] = mapped_column(String(10), nullable=False)
    code: Mapped[str] = mapped_column(String(10), nullable=False)
    name: Mapped[str] = mapped_column(String(20), default="")
    reason: Mapped[str] = mapped_column(Text, default="")               # 上榜原因
    close: Mapped[float] = mapped_column(Float, nullable=True)
    change_pct: Mapped[float] = mapped_column(Float, nullable=True)
    turnover: Mapped[float] = mapped_column(Float, nullable=True)       # 当日成交额
    buy_total: Mapped[float] = mapped_column(Float, default=0.0)        # 买入总额(元)
    sell_total: Mapped[float] = mapped_column(Float, default=0.0)       # 卖出总额
    net_amount: Mapped[float] = mapped_column(Float, default=0.0)       # 净买入
    net_rate: Mapped[float] = mapped_column(Float, nullable=True)       # 净买入占成交比
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("trade_date", "code", name="uq_lhb_date_code"),
        Index("idx_lhb_date", "trade_date"),
        Index("idx_lhb_code", "code"),
    )


class LhbSeatDetail(Base):
    """龙虎榜席位明细 - top-5 buyer/seller seats per LHB record."""
    __tablename__ = "lhb_seat_details"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[str] = mapped_column(String(10), nullable=False)
    code: Mapped[str] = mapped_column(String(10), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, default=0)               # 1-5
    side: Mapped[str] = mapped_column(String(4), default="buy")         # buy / sell
    seat_name: Mapped[str] = mapped_column(String(120), default="")
    buy_amount: Mapped[float] = mapped_column(Float, default=0.0)
    sell_amount: Mapped[float] = mapped_column(Float, default=0.0)
    net_amount: Mapped[float] = mapped_column(Float, default=0.0)
    is_known_hot: Mapped[bool] = mapped_column(Boolean, default=False)  # 是否知名游资席位
    hot_money_tag: Mapped[str] = mapped_column(String(50), default="")  # 游资标签 (赵老哥/章盟主...)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_lhb_seat_date_code", "trade_date", "code"),
        Index("idx_lhb_seat_name", "seat_name"),
    )


class ConceptHeatHistory(Base):
    """板块热度历史快照 - daily snapshot of board heat for time-series analysis."""
    __tablename__ = "concept_heat_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[str] = mapped_column(String(10), nullable=False)
    board_code: Mapped[str] = mapped_column(String(20), default="")
    concept: Mapped[str] = mapped_column(String(80), nullable=False)
    change_pct: Mapped[float] = mapped_column(Float, nullable=True)
    net_inflow: Mapped[float] = mapped_column(Float, nullable=True)
    heat_score: Mapped[float] = mapped_column(Float, nullable=True)     # 0-100
    heat_level: Mapped[str] = mapped_column(String(10), default="")     # core/hot/watch/observe
    rank: Mapped[int] = mapped_column(Integer, nullable=True)
    zt_count: Mapped[int] = mapped_column(Integer, default=0)           # 板块内涨停数
    up_ratio: Mapped[float] = mapped_column(Float, nullable=True)       # 上涨比例
    leader_code: Mapped[str] = mapped_column(String(10), default="")
    leader_name: Mapped[str] = mapped_column(String(20), default="")
    leader_change: Mapped[float] = mapped_column(Float, nullable=True)
    leader_consecutive: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("trade_date", "concept", name="uq_concept_heat_date"),
        Index("idx_concept_heat_date", "trade_date"),
        Index("idx_concept_heat_concept", "concept"),
    )


class DragonSignal(Base):
    """龙头交易信号 - generated buy/sell signals from the dragon model."""
    __tablename__ = "dragon_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[str] = mapped_column(String(10), nullable=False)
    code: Mapped[str] = mapped_column(String(10), nullable=False)
    name: Mapped[str] = mapped_column(String(20), default="")
    signal_type: Mapped[str] = mapped_column(String(10), default="hold")  # buy/sell/hold
    dragon_rank: Mapped[int] = mapped_column(Integer, default=0)           # 1=总龙头
    dragon_score: Mapped[float] = mapped_column(Float, default=0.0)        # 0-100
    concept: Mapped[str] = mapped_column(String(80), default="")
    consecutive: Mapped[int] = mapped_column(Integer, default=0)
    model_conf: Mapped[float] = mapped_column(Float, default=0.0)          # 0-1
    entry_price: Mapped[float] = mapped_column(Float, nullable=True)
    stop_price: Mapped[float] = mapped_column(Float, nullable=True)
    target_price: Mapped[float] = mapped_column(Float, nullable=True)
    market_cycle: Mapped[str] = mapped_column(String(20), default="")      # ice/warmup/peak/cooldown
    reason: Mapped[dict] = mapped_column(JSON, default=dict)               # 评分明细
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("trade_date", "code", name="uq_dragon_signal_date_code"),
        Index("idx_dragon_signal_date", "trade_date"),
        Index("idx_dragon_signal_score", "dragon_score"),
    )


# ═══════════════════════════════════════════════════════════════
#  Sector Pool (人工维护的"主线赛道池" — Universe filter source)
#
#  设计要点：
#   - 完全独立于自动抓取的 ConceptBoard/StockConcept（那是泛概念，几百个）
#   - 用户精选 20-30 个主线赛道，每个赛道 3-5 只龙头股
#   - 个股可归属多个赛道 (UNIQUE 含 removed_at 实现软删除友好)
#   - 软删除 (archived_at / removed_at) 保留历史快照，第一版前端不暴露
#   - 被 quant pipeline 在 Universe 段消费（不进入买卖信号层）
# ═══════════════════════════════════════════════════════════════

class SectorPool(Base):
    """人工维护的赛道（如 CPO / 液冷 / AI 服务器）。"""
    __tablename__ = "sector_pools"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    category: Mapped[str] = mapped_column(String(40), default="")          # 上级分组，如 "AI算力"
    description: Mapped[str] = mapped_column(Text, default="")             # 赛道逻辑/催化备注
    rank: Mapped[int] = mapped_column(Integer, default=0)                  # 手动排序，越小越靠前
    status: Mapped[str] = mapped_column(String(16), default="active")      # active / archived
    archived_at: Mapped[str] = mapped_column(String(32), default="")       # ISO ts, 空=未归档
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        # 同名赛道在 active 状态下唯一；归档版本不冲突（archived_at 不同）
        UniqueConstraint("name", "archived_at", name="uq_sector_pool_name_archived"),
        Index("idx_sector_pool_status", "status"),
        Index("idx_sector_pool_category", "category"),
    )


class SectorPoolStock(Base):
    """赛道-个股 多对多关系，带 tier 标签。"""
    __tablename__ = "sector_pool_stocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sector_id: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(String(10), nullable=False)
    tier: Mapped[int] = mapped_column(Integer, default=2)                  # 1=龙一 / 2=龙二 / 3=跟风补涨
    note: Mapped[str] = mapped_column(String(120), default="")
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    removed_at: Mapped[str] = mapped_column(String(32), default="")        # ISO ts, 空=未删除
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        # (sector_id, code, removed_at) 唯一：同赛道内同股票不重复，
        # 但被移除后可以重新加入（removed_at 不同）
        UniqueConstraint("sector_id", "code", "removed_at", name="uq_sector_stock_sid_code_removed"),
        Index("idx_sector_pool_stocks_sid", "sector_id"),
        Index("idx_sector_pool_stocks_code", "code"),
    )
