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


class Level(BaseModel):
    label: str  # R3/R2/R1/S1/S2/S3
    price: float
    kind: str  # resistance / support
    strength: int  # 1-5
    touches: int
    note: str = ""
    distance_pct: float = 0.0


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
