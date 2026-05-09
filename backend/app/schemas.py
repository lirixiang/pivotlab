from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field


class Candle(BaseModel):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class StockQuote(BaseModel):
    code: str
    name: str
    price: float
    change: float
    change_pct: float
    volume: float
    amount: float
    volume_ratio: float = 0.0
    turnover: float = 0.0
    industry: str = ""
    market: str = ""
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    prev_close: float = 0.0
    turnover_rate: float = 0.0
    pe_ratio: float = 0.0
    market_cap: float = 0.0
    industry_pe: dict | None = None
    concepts: list[str] = Field(default_factory=list)
    concept_details: list[dict] = Field(default_factory=list)
    fundamentals: dict | None = None
    analyst_consensus: dict | None = None


class Level(BaseModel):
    label: str  # R3/R2/R1/S1/S2/S3
    price: float
    kind: str  # resistance / support
    strength: int  # 1-5
    touches: int
    note: str = ""
    distance_pct: float = 0.0
    score: float = 0.0       # 0-100 confidence score (normalised)
    factors: dict[str, Any] = Field(default_factory=dict)  # score breakdown details
    reasons: list[str] = Field(default_factory=list)  # human-readable reason tags


class StockDetail(BaseModel):
    quote: StockQuote
    candles: list[Candle]
    levels: list[Level]


class ScreenerItem(BaseModel):
    code: str
    name: str
    pattern: str
    score: float
    price: float
    change_pct: float
    volume_ratio: float
    breakout_price: float | None = None
    pullback_price: float | None = None
    distance_to_support_pct: float | None = None
    triggers: list[str] = Field(default_factory=list)
    market: str = ""
    industry: str = ""
    market_cap: float = 0  # 亿元
    amount: float = 0  # 成交额（万元）
    rr_ratio: float = 0  # 盈亏比
    support_score: float = 0  # 支撑位强度 (0-100)
    concept: str = ""  # 概念板块
    fundamental_status: str = ""  # healthy/neutral/weak/risk
    fundamental_summary: str = ""  # 基本面摘要


class ScreenerResponse(BaseModel):
    pattern: str
    total: int
    scanned: int
    scanned_at: datetime
    items: list[ScreenerItem]


class WatchlistCreate(BaseModel):
    code: str
    name: str = ""
    note: str = ""


class WatchlistOut(BaseModel):
    id: int
    code: str
    name: str
    note: str
    created_at: datetime

    class Config:
        from_attributes = True


class IndexQuote(BaseModel):
    code: str
    name: str
    price: float
    change_pct: float


class MarketOverview(BaseModel):
    indices: list[IndexQuote]
    total_amount: float = 0.0
    server_time: datetime
