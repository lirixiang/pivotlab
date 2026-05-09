"""Build leakage-free training samples for ML models.

Strategy
--------
- Pick N "snapshot dates" across recent history (e.g. every 5 trading days
  over the past 2 years).
- For each snapshot date T, for each stock with at least 60 prior bars:
    - Compute FeatureSet using ONLY candles on or before T  (no peek).
    - Forward label = (close[T+H] / close[T] - 1) * 100, in %.
    - Cross-sectional rank label = relative rank within that snapshot date.
- Output:
    X      : (N_samples, n_features)  float32
    y_ret  : (N_samples,)             forward H-day return %
    y_rank : (N_samples,)             percentile rank within group [0,1]
    groups : (N_dates,) int32         group sizes for LightGBM ranker
    dates  : (N_samples,) str
    codes  : (N_samples,) str
    seq    : (N_samples, 60, 5)       OHLCV window (normalized) for seq models

Cost: ~2-3 minutes for 500 stocks × 80 dates on a modern box.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

import numpy as np
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ...database import DATABASE_URL
from ...models import DailyCandle, FinancialSnapshot, Stock
from ...schemas import Candle
from ..features import FeatureSet, extract

logger = logging.getLogger(__name__)


# Feature columns used for tabular ML (must be a stable, ordered list).
FEATURE_COLS: list[str] = [
    "ma_aligned", "above_ma20_days", "trend_slope_20",
    "ret_1d", "ret_5d", "ret_20d", "ret_60d",
    "rsi14", "macd_dif", "macd_hist", "macd_golden",
    "atr_pct", "vol_20d_pct", "drawdown_60d",
    "vol_ratio_5", "vol_ratio_20", "vol_zscore_20",
    "near_ma20_pct", "near_high_60_pct", "near_low_60_pct", "bb_position",
    "is_breakout_20", "is_breakout_60", "is_pullback_to_ma10",
    "is_three_white_soldiers", "is_doji",
    "pe_ratio", "market_cap_bil",
    "roe", "revenue_yoy", "net_profit_yoy",
    "concept_heat", "concept_inflow_bil",
]


# ──────────────────────────────────────────────────────────────
@dataclass
class Sample:
    code: str
    snap_date: str
    fs: FeatureSet
    fwd_ret: float           # forward H-day return %
    seq_window: np.ndarray   # (60, 5) OHLCV normalized


@dataclass
class Dataset:
    X: np.ndarray            # (N, F) float32 - tabular features
    y_ret: np.ndarray        # (N,)   float32 - forward return %
    y_rank: np.ndarray       # (N,)   float32 - cross-sectional rank [0,1]
    groups: np.ndarray       # (G,)   int32   - group sizes (per snap_date)
    seq: np.ndarray          # (N, 60, 5) float32 - normalised OHLCV
    codes: list[str]
    dates: list[str]

    def split_time(self, val_frac: float = 0.2) -> tuple["Dataset", "Dataset"]:
        """Time-ordered split.  Validation = the last `val_frac` of dates."""
        unique_dates = sorted(set(self.dates))
        cut_idx = int(len(unique_dates) * (1 - val_frac))
        train_dates = set(unique_dates[:cut_idx])
        val_dates = set(unique_dates[cut_idx:])

        def _slice(keep: set[str]) -> "Dataset":
            mask = np.array([d in keep for d in self.dates], dtype=bool)
            # Recompute groups by counting per kept date in original order
            kept_dates = [self.dates[i] for i in range(len(self.dates)) if mask[i]]
            groups = []
            cur, cnt = None, 0
            for d in kept_dates:
                if d != cur:
                    if cnt:
                        groups.append(cnt)
                    cur, cnt = d, 1
                else:
                    cnt += 1
            if cnt:
                groups.append(cnt)
            return Dataset(
                X=self.X[mask],
                y_ret=self.y_ret[mask],
                y_rank=self.y_rank[mask],
                groups=np.array(groups, dtype=np.int32),
                seq=self.seq[mask],
                codes=[self.codes[i] for i in range(len(self.codes)) if mask[i]],
                dates=kept_dates,
            )

        return _slice(train_dates), _slice(val_dates)


# ──────────────────────────────────────────────────────────────
def _sync_url() -> str:
    return (
        str(DATABASE_URL)
        .replace("sqlite+aiosqlite", "sqlite")
        .replace("postgresql+asyncpg", "postgresql+psycopg2")
    )


_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(_sync_url(), echo=False, pool_pre_ping=True)
    return _engine


def _normalize_window(window: np.ndarray) -> np.ndarray:
    """Normalize a (60,5) OHLCV window: prices -> log-return from first close,
    volume -> z-score within window. Robust to zeros."""
    out = np.zeros_like(window, dtype=np.float32)
    base = window[0, 3] if window[0, 3] > 0 else 1.0
    out[:, 0:4] = np.log(np.clip(window[:, 0:4] / base, 1e-6, None)).astype(np.float32)
    v = window[:, 4]
    vm = v.mean()
    vs = v.std() + 1e-6
    out[:, 4] = ((v - vm) / vs).astype(np.float32)
    return out


def _candle_objs(rows) -> list[Candle]:
    return [
        Candle(
            date=r.trade_date,
            open=r.open or 0.0, high=r.high or 0.0,
            low=r.low or 0.0, close=r.close or 0.0,
            volume=r.volume or 0.0,
        )
        for r in rows
    ]


def _build_feature_with_aux(
    code: str,
    cl_subset: list[Candle],
    fund: FinancialSnapshot | None,
) -> FeatureSet | None:
    fs = extract(cl_subset, code=code)
    if not fs:
        return None
    if fund:
        fs.roe = float(fund.roe or 0.0)
        fs.revenue_yoy = float(fund.revenue_yoy or 0.0)
        fs.net_profit_yoy = float(fund.net_profit_yoy or 0.0)
        if fund.pe_ratio_ttm:
            fs.pe_ratio = float(fund.pe_ratio_ttm)
    return fs


def _features_to_row(fs: FeatureSet) -> np.ndarray:
    return np.array(
        [getattr(fs, c, 0.0) for c in FEATURE_COLS],
        dtype=np.float32,
    )


# ──────────────────────────────────────────────────────────────
def build_dataset(
    *,
    horizon_days: int = 10,
    snapshot_step: int = 5,        # one snapshot every N trading days
    history_years: float = 2.0,
    universe_limit: int | None = 600,
    min_bars: int = 80,
    progress_cb=None,
) -> Dataset:
    """Build a labelled dataset.  See module docstring for label semantics."""
    eng = _get_engine()
    with Session(eng) as session:
        stocks = list(session.execute(
            select(Stock).where(Stock.is_st == False)  # noqa: E712
        ).scalars().all())
        if universe_limit:
            stocks = stocks[:universe_limit]
        codes = [s.code for s in stocks]
        if not codes:
            raise RuntimeError("dataset: empty universe")

        # Bulk load all candles in window
        cutoff = (date.today() - timedelta(days=int(history_years * 365 + 365))).strftime("%Y-%m-%d")
        rows = session.execute(
            select(DailyCandle).where(
                DailyCandle.code.in_(codes),
                DailyCandle.trade_date >= cutoff,
            ).order_by(DailyCandle.code, DailyCandle.trade_date.asc())
        ).scalars().all()

        funds = {
            r.code: r for r in session.execute(
                select(FinancialSnapshot).where(FinancialSnapshot.code.in_(codes))
            ).scalars().all()
        }

    by_code: dict[str, list] = defaultdict(list)
    for r in rows:
        by_code[r.code].append(r)
    logger.info("dataset: loaded %d stocks, %d total candles", len(by_code), len(rows))

    # Determine the universe of trade-dates from the union (use any code as anchor;
    # use the longest one to be safe).
    if not by_code:
        raise RuntimeError("dataset: no candles loaded")
    anchor = max(by_code.values(), key=len)
    all_dates = [r.trade_date for r in anchor]

    # Choose snapshot dates (skip last `horizon_days` so labels fit)
    if len(all_dates) <= horizon_days + 80:
        raise RuntimeError("dataset: not enough history")
    snap_indices = list(range(min_bars, len(all_dates) - horizon_days, snapshot_step))
    snap_dates = [all_dates[i] for i in snap_indices]
    logger.info("dataset: %d snapshot dates from %s to %s",
                len(snap_dates), snap_dates[0], snap_dates[-1])

    samples: list[Sample] = []
    n_dates_processed = 0
    for snap_date in snap_dates:
        per_date_count = 0
        per_date: list[Sample] = []
        for code, cl_rows in by_code.items():
            # Find index of snap_date in this code's series
            # (linear scan; could binary search but sizes are small per stock)
            idx = None
            # Skip stocks that lack data at this date
            # Use a quick check: only stocks whose first date <= snap_date
            if cl_rows[0].trade_date > snap_date or cl_rows[-1].trade_date < snap_date:
                continue
            # Binary search for snap_date
            lo, hi = 0, len(cl_rows) - 1
            while lo <= hi:
                mid = (lo + hi) // 2
                d = cl_rows[mid].trade_date
                if d == snap_date:
                    idx = mid
                    break
                elif d < snap_date:
                    lo = mid + 1
                else:
                    hi = mid - 1
            if idx is None:
                continue
            if idx < min_bars or idx + horizon_days >= len(cl_rows):
                continue

            cl_subset = _candle_objs(cl_rows[: idx + 1])
            fs = _build_feature_with_aux(code, cl_subset, funds.get(code))
            if not fs:
                continue
            close_T = cl_rows[idx].close or 0.0
            close_TH = cl_rows[idx + horizon_days].close or 0.0
            if close_T <= 0:
                continue
            fwd_ret = (close_TH / close_T - 1.0) * 100.0
            # Drop extreme outliers (likely data glitches)
            if abs(fwd_ret) > 80:
                continue

            # 60-bar OHLCV window
            window = np.array([
                [c.open, c.high, c.low, c.close, c.volume]
                for c in cl_subset[-60:]
            ], dtype=np.float64)
            seq = _normalize_window(window)

            per_date.append(Sample(
                code=code, snap_date=snap_date,
                fs=fs, fwd_ret=fwd_ret, seq_window=seq,
            ))
            per_date_count += 1

        if per_date_count >= 30:
            samples.extend(per_date)
        n_dates_processed += 1
        if progress_cb and n_dates_processed % 5 == 0:
            progress_cb({
                "phase": "build_dataset",
                "pct": int(100 * n_dates_processed / len(snap_dates)),
                "snap_dates_done": n_dates_processed,
                "snap_dates_total": len(snap_dates),
                "samples": len(samples),
            })

    if not samples:
        raise RuntimeError("dataset: zero samples (insufficient data?)")

    # Build numpy arrays
    samples.sort(key=lambda s: (s.snap_date, s.code))
    N = len(samples)
    X = np.stack([_features_to_row(s.fs) for s in samples])
    y_ret = np.array([s.fwd_ret for s in samples], dtype=np.float32)
    seq = np.stack([s.seq_window for s in samples])
    codes_out = [s.code for s in samples]
    dates_out = [s.snap_date for s in samples]

    # Per-date rank label (percentile in [0,1])
    y_rank = np.zeros(N, dtype=np.float32)
    groups: list[int] = []
    i = 0
    while i < N:
        j = i
        while j < N and samples[j].snap_date == samples[i].snap_date:
            j += 1
        n = j - i
        groups.append(n)
        rets = y_ret[i:j]
        order = np.argsort(rets)
        ranks = np.empty(n, dtype=np.float32)
        ranks[order] = np.arange(n, dtype=np.float32) / max(n - 1, 1)
        y_rank[i:j] = ranks
        i = j

    logger.info(
        "dataset: built N=%d samples over %d dates; mean fwd ret %.2f%%, std %.2f%%",
        N, len(groups), y_ret.mean(), y_ret.std(),
    )

    return Dataset(
        X=X, y_ret=y_ret, y_rank=y_rank,
        groups=np.array(groups, dtype=np.int32),
        seq=seq, codes=codes_out, dates=dates_out,
    )


def features_to_row(fs: FeatureSet) -> np.ndarray:
    """Public helper used by predictors at inference time."""
    return _features_to_row(fs)


def normalize_window(window: np.ndarray) -> np.ndarray:
    return _normalize_window(window)
