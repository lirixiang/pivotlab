"""Feature engineering for the recommender.

Given a list of daily candles, compute a flat dict of ~30 numeric features
covering price action, momentum, volatility, volume, and structure.

All features are robust to missing data (return 0.0 / nan-safe).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

import numpy as np

from ..schemas import Candle


# ──────────────────────────────────────────────────────────────
#  Low-level indicators (pure numpy, no pandas dependency)
# ──────────────────────────────────────────────────────────────

def _arr(candles: list[Candle]) -> dict[str, np.ndarray]:
    o = np.array([c.open for c in candles], dtype=np.float64)
    h = np.array([c.high for c in candles], dtype=np.float64)
    l = np.array([c.low for c in candles], dtype=np.float64)
    c = np.array([c.close for c in candles], dtype=np.float64)
    v = np.array([c.volume for c in candles], dtype=np.float64)
    return {"o": o, "h": h, "l": l, "c": c, "v": v}


def sma(x: np.ndarray, n: int) -> np.ndarray:
    if len(x) < n:
        return np.full_like(x, np.nan)
    out = np.full_like(x, np.nan)
    cs = np.cumsum(x)
    out[n - 1:] = (cs[n - 1:] - np.concatenate(([0.0], cs[:-n]))) / n
    return out


def ema(x: np.ndarray, n: int) -> np.ndarray:
    if len(x) == 0:
        return x
    alpha = 2.0 / (n + 1)
    out = np.empty_like(x)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = alpha * x[i] + (1 - alpha) * out[i - 1]
    return out


def rsi(closes: np.ndarray, n: int = 14) -> float:
    if len(closes) < n + 1:
        return 50.0
    diff = np.diff(closes[-(n + 1):])
    up = np.clip(diff, 0, None).mean()
    dn = -np.clip(diff, None, 0).mean()
    if dn == 0:
        return 100.0 if up > 0 else 50.0
    rs = up / dn
    return float(100 - 100 / (1 + rs))


def macd(closes: np.ndarray) -> tuple[float, float, float]:
    """Return (DIF, DEA, HIST) at the latest bar."""
    if len(closes) < 35:
        return 0.0, 0.0, 0.0
    e12 = ema(closes, 12)
    e26 = ema(closes, 26)
    dif = e12 - e26
    dea = ema(dif, 9)
    hist = (dif - dea) * 2
    return float(dif[-1]), float(dea[-1]), float(hist[-1])


def atr(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int = 14) -> float:
    if len(c) < n + 1:
        return 0.0
    tr = np.maximum.reduce([
        h[1:] - l[1:],
        np.abs(h[1:] - c[:-1]),
        np.abs(l[1:] - c[:-1]),
    ])
    return float(tr[-n:].mean())


def boll(closes: np.ndarray, n: int = 20, k: float = 2.0) -> tuple[float, float, float]:
    """Return (mid, upper, lower) Bollinger bands at latest bar."""
    if len(closes) < n:
        m = float(closes.mean())
        return m, m, m
    seg = closes[-n:]
    m = float(seg.mean())
    s = float(seg.std(ddof=0))
    return m, m + k * s, m - k * s


def slope_pct(x: np.ndarray, n: int) -> float:
    """Linear regression slope over last n bars, expressed as % of last value."""
    if len(x) < n or n < 2:
        return 0.0
    y = x[-n:]
    t = np.arange(n)
    slope = float(np.polyfit(t, y, 1)[0])
    if y[-1] == 0:
        return 0.0
    return slope / y[-1] * 100  # %/bar


# ──────────────────────────────────────────────────────────────
#  Feature record
# ──────────────────────────────────────────────────────────────

@dataclass
class FeatureSet:
    code: str
    price: float
    # trend
    ma5: float
    ma10: float
    ma20: float
    ma60: float
    ma_aligned: float       # 1 if ma5>ma10>ma20>ma60 else fractional
    above_ma20_days: int
    trend_slope_20: float   # %/bar slope of close over 20 bars
    # momentum
    ret_1d: float
    ret_5d: float
    ret_20d: float
    ret_60d: float
    rsi14: float
    macd_dif: float
    macd_hist: float
    macd_golden: int        # 1 if golden cross within last 3 bars
    # volatility / risk
    atr14: float
    atr_pct: float          # atr14 / price
    vol_20d_pct: float      # std(ret_1d) over 20d in %
    drawdown_60d: float     # current price vs 60d high, % (negative number)
    # volume
    vol_ratio_5: float      # today's volume / mean(last 5)
    vol_ratio_20: float
    vol_zscore_20: float
    # structure
    near_ma20_pct: float    # (price - ma20) / ma20 in %
    near_high_60_pct: float # (price - max60) / max60 in %  (≤0)
    near_low_60_pct: float  # (price - min60) / min60 in %  (≥0)
    bb_position: float      # (price - lower) / (upper - lower)  ∈ [0,1+]
    # pattern flags
    is_breakout_20: int     # close > prior 20-day high
    is_breakout_60: int     # close > prior 60-day high
    is_pullback_to_ma10: int
    is_three_white_soldiers: int
    is_doji: int
    # ── Tradability / data quality flags (set by recommender) ──
    change_pct_today: float = 0.0   # today's % change vs prev close
    is_limit_up: int = 0            # closed at limit up today (cannot buy)
    is_limit_down: int = 0
    is_recently_xrxd: int = 0       # suspected ex-dividend within last 5 bars
    data_stale_days: int = 0        # how many days the latest bar lags real today
    # ── Distance to nearest SR levels (set later by recommender for top-N) ──
    dist_to_resistance_pct: float = 100.0  # +∞ if no resistance nearby
    dist_to_support_pct: float = 100.0
    resistance_strength: float = 0.0       # 0..1
    support_strength: float = 0.0
    # ── Market environment context (set by recommender) ──
    market_trend: float = 0.0       # +1 bull / 0 neutral / -1 bear
    market_atr_pct: float = 0.0
    is_friday: int = 0              # weekend gap risk
    days_to_holiday: int = 99
    # fundamental (optional, set by recommender from quote.fundamentals)
    pe_ratio: float = 0.0
    market_cap_bil: float = 0.0   # 亿元
    roe: float = 0.0
    revenue_yoy: float = 0.0
    net_profit_yoy: float = 0.0
    # concept heat (optional, set by recommender)
    concept_heat: float = 0.0     # max change_pct_5d among concepts
    concept_inflow_bil: float = 0.0  # max net_inflow / 1e8

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


# ──────────────────────────────────────────────────────────────
#  Main extractor
# ──────────────────────────────────────────────────────────────

def extract(candles: list[Candle], code: str = "") -> FeatureSet | None:
    """Extract features from a candle list.

    Returns None if not enough data (< 60 bars).
    """
    if not candles or len(candles) < 60:
        return None

    a = _arr(candles)
    o, h, l, c, v = a["o"], a["h"], a["l"], a["c"], a["v"]
    price = float(c[-1])

    ma5_arr = sma(c, 5)
    ma10_arr = sma(c, 10)
    ma20_arr = sma(c, 20)
    ma60_arr = sma(c, 60)
    ma5 = float(ma5_arr[-1])
    ma10 = float(ma10_arr[-1])
    ma20 = float(ma20_arr[-1])
    ma60 = float(ma60_arr[-1])

    # MA alignment score: how many of the (ma5>ma10, ma10>ma20, ma20>ma60) hold
    aligned = sum([ma5 > ma10, ma10 > ma20, ma20 > ma60]) / 3.0

    # Days continuously above ma20
    above = c[-30:] > ma20_arr[-30:]
    above_days = 0
    for ok in above[::-1]:
        if ok:
            above_days += 1
        else:
            break

    trend_slope = slope_pct(c, 20)

    def _ret(n: int) -> float:
        if len(c) <= n or c[-(n + 1)] == 0:
            return 0.0
        return float((c[-1] / c[-(n + 1)] - 1) * 100)

    rsi14 = rsi(c, 14)
    dif, dea, hist = macd(c)
    # Golden cross within last 3 bars: dif crossed up through dea
    macd_golden = 0
    if len(c) >= 35:
        e12 = ema(c, 12); e26 = ema(c, 26)
        dif_arr = e12 - e26
        dea_arr = ema(dif_arr, 9)
        for i in (-1, -2, -3):
            if dif_arr[i] > dea_arr[i] and dif_arr[i - 1] <= dea_arr[i - 1]:
                macd_golden = 1
                break

    atr14 = atr(h, l, c, 14)
    atr_pct = (atr14 / price) if price > 0 else 0.0

    rets = np.diff(c[-21:]) / np.maximum(c[-21:-1], 1e-9)
    vol_20 = float(rets.std(ddof=0) * 100) if len(rets) > 1 else 0.0

    high_60 = float(c[-60:].max())
    low_60 = float(c[-60:].min())
    drawdown_60 = (price / high_60 - 1) * 100 if high_60 > 0 else 0.0

    vol_5_mean = float(v[-6:-1].mean()) if len(v) >= 6 else float(v.mean())
    vol_20_mean = float(v[-21:-1].mean()) if len(v) >= 21 else float(v.mean())
    vol_20_std = float(v[-21:-1].std(ddof=0)) if len(v) >= 21 else 1.0
    vol_ratio_5 = (v[-1] / vol_5_mean) if vol_5_mean > 0 else 1.0
    vol_ratio_20 = (v[-1] / vol_20_mean) if vol_20_mean > 0 else 1.0
    vol_z = ((v[-1] - vol_20_mean) / vol_20_std) if vol_20_std > 0 else 0.0

    near_ma20 = (price / ma20 - 1) * 100 if ma20 > 0 else 0.0
    near_h60 = (price / high_60 - 1) * 100 if high_60 > 0 else 0.0
    near_l60 = (price / low_60 - 1) * 100 if low_60 > 0 else 0.0

    mid, up, lo = boll(c, 20, 2.0)
    bb_pos = (price - lo) / (up - lo) if up > lo else 0.5

    # Patterns
    prior_high_20 = float(h[-21:-1].max()) if len(h) >= 21 else price
    prior_high_60 = float(h[-61:-1].max()) if len(h) >= 61 else price
    is_brk_20 = int(price > prior_high_20)
    is_brk_60 = int(price > prior_high_60)

    # Pullback to MA10: low touched MA10 in last 3 bars but close still > MA10
    is_pullback = 0
    if len(c) >= 11:
        for i in (-1, -2, -3):
            if l[i] <= ma10_arr[i] * 1.005 and c[i] >= ma10_arr[i] * 0.995:
                is_pullback = 1
                break

    # Three white soldiers: last 3 bars all green & each close > prior close
    is_tws = 0
    if len(c) >= 3:
        if all(c[i] > o[i] for i in (-1, -2, -3)) and c[-1] > c[-2] > c[-3]:
            is_tws = 1

    # Doji: today's body < 20% of range
    rng = h[-1] - l[-1]
    body = abs(c[-1] - o[-1])
    is_doji = int(rng > 0 and body / rng < 0.2)

    # ── Tradability / data-quality heuristics (no external data needed) ──
    # Today's % change vs prior close (drives limit-up detection)
    if len(c) >= 2 and c[-2] > 0:
        chg_today = float((c[-1] / c[-2] - 1) * 100)
    else:
        chg_today = 0.0

    # Crude limit-up/-down detection.
    # Without a board-type lookup we use 9.7% / -9.7% as the conservative
    # threshold.  ChiNext / STAR (20%) and ST (5%) will be slightly miscalled
    # but the recommender always cross-checks with `change_pct` from quotes.
    is_lu = int(chg_today >= 9.5)
    is_ld = int(chg_today <= -9.5)

    # Suspected ex-dividend / split: any large overnight gap within last 5 bars
    # without commensurate volume.  Triggers when |open[i] / close[i-1] - 1|
    # > 15%.  10送X / 转股 will move price by ~30-50% so 15% is a safe band.
    is_xrxd = 0
    for i in range(-1, -6, -1):
        if i - 1 < -len(c):
            break
        prev_c = c[i - 1]
        if prev_c <= 0:
            continue
        if abs(o[i] / prev_c - 1) > 0.15:
            is_xrxd = 1
            break

    return FeatureSet(
        code=code,
        price=price,
        ma5=ma5, ma10=ma10, ma20=ma20, ma60=ma60,
        ma_aligned=aligned,
        above_ma20_days=int(above_days),
        trend_slope_20=trend_slope,
        ret_1d=_ret(1), ret_5d=_ret(5), ret_20d=_ret(20), ret_60d=_ret(60),
        rsi14=rsi14,
        macd_dif=dif, macd_hist=hist, macd_golden=macd_golden,
        atr14=atr14, atr_pct=atr_pct,
        vol_20d_pct=vol_20,
        drawdown_60d=drawdown_60,
        vol_ratio_5=vol_ratio_5, vol_ratio_20=vol_ratio_20, vol_zscore_20=vol_z,
        near_ma20_pct=near_ma20,
        near_high_60_pct=near_h60,
        near_low_60_pct=near_l60,
        bb_position=float(bb_pos),
        is_breakout_20=is_brk_20, is_breakout_60=is_brk_60,
        is_pullback_to_ma10=is_pullback,
        is_three_white_soldiers=is_tws,
        is_doji=is_doji,
        change_pct_today=chg_today,
        is_limit_up=is_lu,
        is_limit_down=is_ld,
        is_recently_xrxd=is_xrxd,
    )


def enrich_with_quote(fs: FeatureSet, quote_dict: dict | None) -> FeatureSet:
    """Populate fundamental + concept-heat fields from quote/fundamentals dicts."""
    if not quote_dict:
        return fs
    fs.pe_ratio = float(quote_dict.get("pe_ratio") or 0.0)
    mc = quote_dict.get("market_cap") or 0.0
    fs.market_cap_bil = float(mc) / 1e8 if mc else 0.0
    fund = quote_dict.get("fundamentals") or {}
    fs.roe = float(fund.get("roe") or 0.0)
    fs.revenue_yoy = float(fund.get("revenue_yoy") or 0.0)
    fs.net_profit_yoy = float(fund.get("net_profit_yoy") or 0.0)
    cds = quote_dict.get("concept_details") or []
    if cds:
        heats = [float(d.get("change_pct_5d") or 0.0) for d in cds]
        flows = [float(d.get("net_inflow") or 0.0) for d in cds]
        fs.concept_heat = max(heats) if heats else 0.0
        fs.concept_inflow_bil = (max(flows) if flows else 0.0) / 1e8
    return fs
