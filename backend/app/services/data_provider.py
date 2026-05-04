"""Data provider: akshare wrappers with graceful mock fallback.

Real network access may be flaky (proxy, blocked). Each call tries akshare first,
then degrades to deterministic mock data so the API stays usable for the prototype.
"""
from __future__ import annotations

import logging
import math
import random
import time
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Iterable

import pandas as pd

from ..schemas import Candle, IndexQuote, StockQuote

logger = logging.getLogger(__name__)

try:
    import akshare as ak  # type: ignore
    _HAS_AK = True
except Exception as e:  # pragma: no cover
    logger.warning("akshare unavailable: %s", e)
    ak = None  # type: ignore
    _HAS_AK = False


# ---------- Mock universe ----------
MOCK_UNIVERSE: list[tuple[str, str, str]] = [
    ("600519", "贵州茅台", "白酒"),
    ("300750", "宁德时代", "电池"),
    ("600036", "招商银行", "银行"),
    ("002594", "比亚迪", "汽车"),
    ("688041", "海光信息", "半导体"),
    ("300308", "中际旭创", "光模块"),
    ("600900", "长江电力", "电力"),
    ("688012", "中微公司", "半导体设备"),
    ("600570", "恒生电子", "金融科技"),
    ("002463", "沪电股份", "PCB"),
    ("601899", "紫金矿业", "有色"),
    ("000858", "五粮液", "白酒"),
    ("600276", "恒瑞医药", "医药"),
    ("601318", "中国平安", "保险"),
    ("000333", "美的集团", "家电"),
]


def _seeded_random(code: str) -> random.Random:
    return random.Random(int(code))


def _mock_quote(code: str, name: str, industry: str) -> StockQuote:
    rng = _seeded_random(code)
    base = 10 + rng.random() * 200
    change_pct = rng.uniform(-3.0, 3.5)
    price = round(base * (1 + change_pct / 100), 2)
    change = round(price * change_pct / 100, 2)
    return StockQuote(
        code=code,
        name=name,
        price=price,
        change=change,
        change_pct=round(change_pct, 2),
        volume=round(rng.uniform(1e6, 5e7), 0),
        amount=round(price * rng.uniform(1e6, 5e7), 0),
        volume_ratio=round(rng.uniform(0.6, 2.5), 2),
        turnover=round(rng.uniform(0.05, 3.5), 2),
        industry=industry,
    )


def _mock_candles(code: str, days: int = 180) -> list[Candle]:
    rng = _seeded_random(code)
    base = 30 + rng.random() * 100
    candles: list[Candle] = []
    today = datetime.now().date()
    price = base
    for i in range(days):
        d = today - timedelta(days=days - i)
        if d.weekday() >= 5:
            continue
        # gentle uptrend with noise + cyclical
        drift = math.sin(i / 12) * 0.6 + 0.05
        noise = rng.uniform(-1.2, 1.2)
        change = drift + noise
        o = price
        c = max(1.0, o * (1 + change / 100))
        h = max(o, c) * (1 + abs(rng.uniform(0, 1.0)) / 100)
        l = min(o, c) * (1 - abs(rng.uniform(0, 1.0)) / 100)
        v = rng.uniform(5e5, 5e7)
        candles.append(Candle(
            date=d.isoformat(),
            open=round(o, 2),
            high=round(h, 2),
            low=round(l, 2),
            close=round(c, 2),
            volume=round(v, 0),
        ))
        price = c
    return candles


def _normalize_code(code: str) -> str:
    code = code.strip().upper()
    if "." in code:
        code = code.split(".")[0]
    return code


def list_universe() -> list[tuple[str, str, str]]:
    return MOCK_UNIVERSE


# ---------- TTL snapshot caches ----------
# Whole-market quote/index calls (akshare) are heavy (1-3s, ~5000 rows each).
# Cache them in-process so per-stock quote calls reuse the snapshot within `_TTL`.
_TTL = 60.0  # seconds
_quote_snapshot: dict[str, StockQuote] = {}
_quote_snapshot_at: float = 0.0
_index_snapshot: list[IndexQuote] = []
_index_snapshot_at: float = 0.0


def _refresh_quote_snapshot() -> None:
    """Populate `_quote_snapshot` from akshare with one network call, or mock."""
    global _quote_snapshot, _quote_snapshot_at
    snap: dict[str, StockQuote] = {}
    industry_by_code = {c: ind for c, _, ind in MOCK_UNIVERSE}
    name_by_code = {c: n for c, n, _ in MOCK_UNIVERSE}
    if _HAS_AK:
        try:
            df = ak.stock_zh_a_spot_em()
            for _, r in df.iterrows():
                code = str(r.get("代码"))
                snap[code] = StockQuote(
                    code=code,
                    name=str(r.get("名称") or name_by_code.get(code, code)),
                    price=float(r.get("最新价", 0) or 0),
                    change=float(r.get("涨跌额", 0) or 0),
                    change_pct=float(r.get("涨跌幅", 0) or 0),
                    volume=float(r.get("成交量", 0) or 0),
                    amount=float(r.get("成交额", 0) or 0),
                    volume_ratio=float(r.get("量比", 0) or 0),
                    turnover=float(r.get("换手率", 0) or 0),
                    industry=industry_by_code.get(code, ""),
                )
        except Exception as e:
            logger.info("akshare snapshot failed: %s — using mock", e)
    if not snap:
        for code, name, ind in MOCK_UNIVERSE:
            snap[code] = _mock_quote(code, name, ind)
    _quote_snapshot = snap
    _quote_snapshot_at = time.time()


def _ensure_quote_snapshot() -> None:
    if not _quote_snapshot or time.time() - _quote_snapshot_at > _TTL:
        _refresh_quote_snapshot()


@lru_cache(maxsize=512)
def get_candles(code: str, period: str = "daily", days: int = 240) -> list[Candle]:
    code = _normalize_code(code)
    if _HAS_AK:
        try:
            end = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
            df = ak.stock_zh_a_hist(
                symbol=code, period=period, start_date=start, end_date=end, adjust="qfq"
            )
            if df is not None and len(df) > 0:
                df = df.tail(days)
                out: list[Candle] = []
                for _, row in df.iterrows():
                    out.append(Candle(
                        date=str(row.get("日期")),
                        open=float(row.get("开盘", 0)),
                        high=float(row.get("最高", 0)),
                        low=float(row.get("最低", 0)),
                        close=float(row.get("收盘", 0)),
                        volume=float(row.get("成交量", 0)),
                    ))
                if out:
                    return out
        except Exception as e:
            logger.info("akshare candles failed for %s: %s — using mock", code, e)
    return _mock_candles(code, days=days)


def get_quote(code: str) -> StockQuote:
    code = _normalize_code(code)
    _ensure_quote_snapshot()
    if code in _quote_snapshot:
        return _quote_snapshot[code]
    name, industry = "", ""
    for c, n, ind in MOCK_UNIVERSE:
        if c == code:
            name, industry = n, ind
            break
    return _mock_quote(code, name or code, industry)


def get_quotes_bulk(codes: Iterable[str]) -> dict[str, StockQuote]:
    """Resolve many codes against the shared snapshot in one shot."""
    _ensure_quote_snapshot()
    out: dict[str, StockQuote] = {}
    for code in codes:
        c = _normalize_code(code)
        if c in _quote_snapshot:
            out[c] = _quote_snapshot[c]
    return out


def get_indices() -> list[IndexQuote]:
    global _index_snapshot, _index_snapshot_at
    if _index_snapshot and time.time() - _index_snapshot_at <= _TTL:
        return _index_snapshot
    items: list[tuple[str, str]] = [
        ("000001", "上证指数"),
        ("399001", "深证成指"),
        ("399006", "创业板指"),
        ("000688", "科创50"),
    ]
    if _HAS_AK:
        try:
            df = ak.stock_zh_index_spot_em(symbol="沪深重要指数")
            results: list[IndexQuote] = []
            for code, name in items:
                row = df[df["代码"] == code]
                if len(row) > 0:
                    r = row.iloc[0]
                    results.append(IndexQuote(
                        code=code, name=name,
                        price=float(r.get("最新价", 0)),
                        change_pct=float(r.get("涨跌幅", 0)),
                    ))
            if results:
                _index_snapshot = results
                _index_snapshot_at = time.time()
                return results
        except Exception as e:
            logger.info("akshare indices failed: %s — using mock", e)
    rng = random.Random(int(datetime.now().strftime("%Y%m%d")))
    fallback = [
        IndexQuote(code=c, name=n, price=round(2000 + rng.random() * 2000, 2),
                   change_pct=round(rng.uniform(-1.5, 1.8), 2))
        for c, n in items
    ]
    _index_snapshot = fallback
    _index_snapshot_at = time.time()
    return fallback
