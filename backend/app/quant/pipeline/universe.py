"""选股层 Universe (M3)

输入：universe_cfg（来自系统配置）
输出：候选股票列表 [{code, name, last_date, last_close, ...}]

实现：
  1. 从 stocks 表拉所有股票（主板优先 + 排除 ST 可由 filter "not is_st" 处理）
  2. 一次 SQL 拉所有股票最近 N 天 K 线（bulk）
  3. 对每只股票构造 DSL context（含 is_st 标量 + 各价量序列）
  4. 应用 universe_cfg.filters 中的每条表达式
  5. 全部通过 + 不在 exclude_codes → 保留；按 last_amount 排序取前 max_size 只
"""
from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

import numpy as np
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ...database import DATABASE_URL
from ...models import DailyCandle, Stock
from ..dsl import eval_rule

logger = logging.getLogger(__name__)

# ── 同步引擎（与 strategy/recommender 同款做法） ──
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


# 主板（沪 600/601/603/605 + 深 000/001/002/003）；过滤北交所、科创板（默认；用户可在 filters 里反过来允许）
_MAIN_BOARD_RE = re.compile(r"^(6[0-3]\d{4}|000\d{3}|001\d{3}|002\d{3}|003\d{3})$")


def _is_main_board(code: str) -> bool:
    return bool(_MAIN_BOARD_RE.match(code))


def _load_stocks(session: Session) -> list[Stock]:
    rows = list(session.execute(select(Stock)).scalars().all())
    return [s for s in rows if _is_main_board(s.code)]


def _load_candles_bulk(
    session: Session, codes: list[str], cutoff_date: str, lookback_days: int
) -> dict[str, list[DailyCandle]]:
    """一次 SQL 拉所有 codes 在 [cutoff, end_date] 区间的 K 线。"""
    rows = session.execute(
        select(DailyCandle)
        .where(
            DailyCandle.code.in_(codes),
            DailyCandle.trade_date >= cutoff_date,
        )
        .order_by(DailyCandle.code, DailyCandle.trade_date.asc())
    ).scalars().all()
    bucket: dict[str, list[DailyCandle]] = defaultdict(list)
    for r in rows:
        bucket[r.code].append(r)
    return bucket


def _build_ctx(stock: Stock, candles: list[DailyCandle], end_date: str | None) -> dict[str, Any] | None:
    if end_date:
        candles = [c for c in candles if c.trade_date <= end_date]
    if len(candles) < 5:
        return None
    close = np.array([c.close or 0.0 for c in candles], dtype=float)
    vol = np.array([c.volume or 0.0 for c in candles], dtype=float)
    return {
        "open": np.array([c.open or 0.0 for c in candles], dtype=float),
        "high": np.array([c.high or 0.0 for c in candles], dtype=float),
        "low": np.array([c.low or 0.0 for c in candles], dtype=float),
        "close": close,
        "vol": vol,
        "volume": vol,
        "amount": close * vol,
        # 标量字段
        "is_st": bool(stock.is_st),
    }


def scan_universe(cfg: dict, end_date: str | None = None) -> dict[str, Any]:
    """跑一次 universe 扫描。

    返回 {
      "candidates": [{code, name, last_date, last_close, last_amount}, ...],
      "total_scanned": int,
      "passed": int,
      "duration_ms": int,
    }
    """
    t0 = time.time()
    cfg = cfg or {}
    filters = cfg.get("filters") or []
    exclude_codes = set(cfg.get("exclude_codes") or [])
    max_size = int(cfg.get("max_size") or 200)
    lookback = 260  # 至少要够 200 日线 + 缓冲

    cutoff = (
        date.fromisoformat(end_date) if end_date else date.today()
    ) - timedelta(days=int(lookback * 1.6))
    cutoff_s = cutoff.strftime("%Y-%m-%d")

    eng = _get_engine()
    with Session(eng) as session:
        stocks = _load_stocks(session)
        stock_map = {s.code: s for s in stocks}
        codes = [s.code for s in stocks if s.code not in exclude_codes]
        if not codes:
            return {"candidates": [], "total_scanned": 0, "passed": 0, "duration_ms": 0}

        candle_map = _load_candles_bulk(session, codes, cutoff_s, lookback)

    passed: list[dict] = []
    for code in codes:
        stock = stock_map[code]
        candles = candle_map.get(code, [])
        ctx = _build_ctx(stock, candles, end_date)
        if ctx is None:
            continue

        # 应用所有 filter
        all_ok = True
        for rule in filters:
            res = eval_rule(rule["expr"], ctx, rule.get("desc", ""))
            if not res.passed:
                all_ok = False
                break
        if not all_ok:
            continue

        last_close = float(ctx["close"][-1])
        last_amount = float(ctx["amount"][-1])
        passed.append({
            "code": code,
            "name": stock.name,
            "industry": stock.industry,
            "last_date": candles[-1].trade_date,
            "last_close": last_close,
            "last_amount": last_amount,
        })

    # 按成交额降序取前 max_size
    passed.sort(key=lambda x: x["last_amount"], reverse=True)
    candidates = passed[:max_size]

    duration_ms = int((time.time() - t0) * 1000)
    logger.info(
        "[quant.universe] scanned=%d passed=%d kept=%d in %dms",
        len(codes), len(passed), len(candidates), duration_ms,
    )
    return {
        "candidates": candidates,
        "total_scanned": len(codes),
        "passed": len(passed),
        "duration_ms": duration_ms,
    }
