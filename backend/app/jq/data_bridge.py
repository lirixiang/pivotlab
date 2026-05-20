"""DB → JoinQuant 格式数据桥接层。

把 DailyCandle 表和 Stock 表的数据，转换成聚宽风格的数据结构：
  - get_price() → pd.DataFrame  columns=['open','high','low','close','volume','money']
  - attribute_history() → pd.DataFrame  columns=fields
  - get_all_securities() → list[str]
  - get_index_stocks()   → list[str]

内部代码格式：
  DB: '000001'
  JQ: '000001.XSHE' / '600000.XSHG'

聚宽 security 格式约定（仅 A 股日线）：
  .XSHG = 上交所（6开头）
  .XSHE = 深交所（0/3开头）
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ..database import DATABASE_URL
from ..models import DailyCandle, Stock

logger = logging.getLogger(__name__)

_engine = None


def _sync_url() -> str:
    return (
        str(DATABASE_URL)
        .replace("sqlite+aiosqlite", "sqlite")
        .replace("postgresql+asyncpg", "postgresql+psycopg2")
    )


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(_sync_url(), echo=False, pool_pre_ping=True)
    return _engine


# ─────────────────────── 代码格式转换 ───────────────────────

def to_jq_code(code: str) -> str:
    """'000001' → '000001.XSHE'，'600000' → '600000.XSHG'"""
    if "." in code:
        return code
    if code.startswith("6"):
        return f"{code}.XSHG"
    return f"{code}.XSHE"


def to_db_code(jq_code: str) -> str:
    """'000001.XSHE' → '000001'"""
    return jq_code.split(".")[0]


def normalize_codes(securities) -> list[str]:
    """统一为 JQ 格式列表。"""
    if isinstance(securities, str):
        securities = [securities]
    return [to_jq_code(s) for s in securities]


# ─────────────────────── 日期工具 ───────────────────────

def _date_str(d) -> str:
    if isinstance(d, datetime):
        return d.strftime("%Y-%m-%d")
    if isinstance(d, date):
        return d.strftime("%Y-%m-%d")
    return str(d)[:10]


def _offset_date(d: str, offset_days: int) -> str:
    """日期字符串加减天数。"""
    dt = datetime.strptime(d, "%Y-%m-%d") + timedelta(days=offset_days)
    return dt.strftime("%Y-%m-%d")


# ─────────────────────── 核心查询 ───────────────────────

def _query_candles(
    codes_db: list[str],
    start_date: str,
    end_date: str,
    session: Session,
) -> dict[str, list[DailyCandle]]:
    """批量查询 K 线，返回 {db_code: [DailyCandle...]} 按日期升序。"""
    if not codes_db:
        return {}
    rows = session.execute(
        select(DailyCandle)
        .where(
            DailyCandle.code.in_(codes_db),
            DailyCandle.trade_date >= start_date,
            DailyCandle.trade_date <= end_date,
        )
        .order_by(DailyCandle.code, DailyCandle.trade_date)
    ).scalars().all()

    result: dict[str, list[DailyCandle]] = {}
    for r in rows:
        result.setdefault(r.code, []).append(r)
    return result


def _candles_to_df(candles: list[DailyCandle]) -> pd.DataFrame:
    """DailyCandle 列表 → DataFrame，index=trade_date。"""
    if not candles:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "money"])
    data = {
        "open":   [c.open   for c in candles],
        "high":   [c.high   for c in candles],
        "low":    [c.low    for c in candles],
        "close":  [c.close  for c in candles],
        "volume": [c.volume for c in candles],
        "money":  [c.close * c.volume for c in candles],
    }
    df = pd.DataFrame(data, index=pd.to_datetime([c.trade_date for c in candles]))
    df.index.name = "date"
    return df


# ─────────────────────── 公开 API ───────────────────────

def get_price(
    security,
    start_date: str | None = None,
    end_date: str | None = None,
    frequency: str = "daily",
    fields: list[str] | None = None,
    count: int | None = None,
    panel: bool = True,
) -> pd.DataFrame | dict[str, pd.DataFrame]:
    """模拟聚宽 get_price。

    单标的: 返回 DataFrame(index=date, columns=fields)
    多标的: 返回 dict{security: DataFrame} 或 panel
    """
    codes = normalize_codes(security)
    codes_db = [to_db_code(c) for c in codes]
    _fields = fields or ["open", "high", "low", "close", "volume", "money"]

    # 如果传 count，end_date 默认今天，往前推
    if end_date is None:
        end_date = datetime.today().strftime("%Y-%m-%d")
    if start_date is None and count is not None:
        start_date = _offset_date(end_date, -(count * 2))  # 多取一些交易日
    if start_date is None:
        start_date = "2020-01-01"

    with Session(_get_engine()) as session:
        candle_map = _query_candles(codes_db, start_date, end_date, session)

    result: dict[str, pd.DataFrame] = {}
    for jq_code, db_code in zip(codes, codes_db):
        candles = candle_map.get(db_code, [])
        if count is not None:
            candles = candles[-count:]
        df = _candles_to_df(candles)
        # 只保留请求字段
        existing = [f for f in _fields if f in df.columns]
        result[jq_code] = df[existing] if existing else df

    if len(codes) == 1:
        return result[codes[0]]
    return result


def attribute_history(
    security: str,
    count: int,
    unit: str = "1d",
    fields: list[str] | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    """模拟聚宽 attribute_history。

    返回 DataFrame(index=date, columns=fields)，最多 count 行。
    """
    if end_date is None:
        end_date = datetime.today().strftime("%Y-%m-%d")
    # 多拉 2 倍日历天数保证够交易日
    start_date = _offset_date(end_date, -(count * 3))

    df = get_price(
        security,
        start_date=start_date,
        end_date=end_date,
        fields=fields,
        panel=False,
    )
    return df.iloc[-count:] if len(df) >= count else df


def history(
    count: int,
    unit: str = "1d",
    field: str = "close",
    security_list: list[str] | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    """模拟聚宽 history。

    返回 DataFrame(index=date, columns=security_list)。
    """
    if not security_list:
        return pd.DataFrame()
    if end_date is None:
        end_date = datetime.today().strftime("%Y-%m-%d")
    start_date = _offset_date(end_date, -(count * 3))
    codes = normalize_codes(security_list)
    codes_db = [to_db_code(c) for c in codes]

    with Session(_get_engine()) as session:
        candle_map = _query_candles(codes_db, start_date, end_date, session)

    result: dict[str, pd.Series] = {}
    for jq_code, db_code in zip(codes, codes_db):
        candles = candle_map.get(db_code, [])[-count:]
        values = [getattr(c, field, c.close) for c in candles]
        dates = pd.to_datetime([c.trade_date for c in candles])
        result[jq_code] = pd.Series(values, index=dates)

    df = pd.DataFrame(result)
    df.index.name = "date"
    return df


def get_current_data(
    security_list: list[str],
    end_date: str | None = None,
) -> dict[str, Any]:
    """模拟聚宽 get_current_data()。

    返回 {security: CurrentData}，CurrentData 有 .last_price / .high_limit / .low_limit。
    """
    if end_date is None:
        end_date = datetime.today().strftime("%Y-%m-%d")
    codes = normalize_codes(security_list)
    codes_db = [to_db_code(c) for c in codes]
    start_date = _offset_date(end_date, -5)

    with Session(_get_engine()) as session:
        candle_map = _query_candles(codes_db, start_date, end_date, session)

    class CurrentData:
        def __init__(self, candle: DailyCandle | None):
            if candle:
                self.last_price = candle.close
                self.high_limit = round(candle.close * 1.10, 2)
                self.low_limit  = round(candle.close * 0.90, 2)
                self.paused = False
            else:
                self.last_price = 0.0
                self.high_limit = 0.0
                self.low_limit  = 0.0
                self.paused = True

    result = {}
    for jq_code, db_code in zip(codes, codes_db):
        candles = candle_map.get(db_code, [])
        result[jq_code] = CurrentData(candles[-1] if candles else None)
    return result


def get_all_securities(
    types: list[str] | None = None,
    date: str | None = None,
) -> pd.DataFrame:
    """模拟聚宽 get_all_securities，返回 DataFrame(index=jq_code, columns=[display_name,...])。"""
    with Session(_get_engine()) as session:
        stocks = session.execute(select(Stock)).scalars().all()

    rows = []
    for s in stocks:
        rows.append({
            "code": to_jq_code(s.code),
            "display_name": s.name,
            "name": s.name,
            "start_date": s.list_date or "",
            "end_date": "2200-01-01",
            "type": "stock",
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.set_index("code")
    return df


def get_index_stocks(index_symbol: str, date: str | None = None) -> list[str]:
    """简单映射常用指数成分股（本地 DB 无指数成分表时，返回主板前 N 只）。"""
    _index_map = {
        "000300.XSHG": ("6", "0"),   # 沪深 300 近似：沪深主板
        "000001.XSHG": ("6",),       # 上证指数
        "399001.XSHE": ("0", "3"),   # 深证成指
    }
    prefixes = _index_map.get(index_symbol, ("6", "0"))

    with Session(_get_engine()) as session:
        stocks = session.execute(select(Stock)).scalars().all()

    codes = [
        to_jq_code(s.code)
        for s in stocks
        if any(s.code.startswith(p) for p in prefixes) and not s.is_st
    ]
    return codes[:300]


# ─────────────────────── 回测专用：批量预加载 ───────────────────────

def bulk_load_candles(
    start_date: str,
    end_date: str,
    codes_db: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """批量加载 K 线，返回 {db_code: {dates, open, high, low, close, volume, money}}。

    供回测引擎预热，避免逐股查库。
    """
    with Session(_get_engine()) as session:
        query = select(DailyCandle).where(
            DailyCandle.trade_date >= start_date,
            DailyCandle.trade_date <= end_date,
        )
        if codes_db:
            query = query.where(DailyCandle.code.in_(codes_db))
        query = query.order_by(DailyCandle.code, DailyCandle.trade_date)
        rows = session.execute(query).scalars().all()

    series_map: dict[str, dict[str, Any]] = {}
    for r in rows:
        s = series_map.setdefault(r.code, {
            "dates": [], "open": [], "high": [], "low": [],
            "close": [], "volume": [], "money": [],
        })
        s["dates"].append(r.trade_date)
        s["open"].append(r.open or 0.0)
        s["high"].append(r.high or 0.0)
        s["low"].append(r.low or 0.0)
        s["close"].append(r.close or 0.0)
        s["volume"].append(r.volume or 0.0)
        s["money"].append((r.close or 0.0) * (r.volume or 0.0))

    for code, s in series_map.items():
        for key in ("open", "high", "low", "close", "volume", "money"):
            s[key] = np.array(s[key], dtype=float)

    return series_map


def get_trading_dates(start_date: str, end_date: str) -> list[str]:
    """从 DailyCandle 提取 [start, end] 的所有交易日，升序。"""
    with Session(_get_engine()) as session:
        rows = session.execute(
            select(DailyCandle.trade_date)
            .where(
                DailyCandle.trade_date >= start_date,
                DailyCandle.trade_date <= end_date,
            )
            .distinct()
            .order_by(DailyCandle.trade_date)
        ).scalars().all()
    return list(rows)


def get_stock_info(code_db: str) -> dict[str, str]:
    """返回股票基础信息字典。"""
    with Session(_get_engine()) as session:
        s = session.get(Stock, code_db)
        if s:
            return {"code": s.code, "name": s.name, "industry": s.industry, "market": s.market}
    return {"code": code_db, "name": code_db, "industry": "", "market": ""}
