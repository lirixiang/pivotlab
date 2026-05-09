"""Four style-specific scoring functions, all rule-based v1.

Each scorer returns (score: 0-100, reasons: list[str], factor_breakdown: dict).
A score >= 60 is considered "actionable"; >= 80 is "strong signal".

These are intentionally transparent (no black-box ML) so the user can read
the reasons. The same FeatureSet input can later be fed to a LightGBM
ranker (see trainer.py) — the rule-based scorers serve as both the v1
production model AND a baseline for ML.
"""
from __future__ import annotations

from .features import FeatureSet


def _clip(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


# ──────────────────────────────────────────────────────────────
#  1. SHORT-TERM: 短线打板 (1–5 days, momentum + volume + concept)
# ──────────────────────────────────────────────────────────────

def score_short_term(fs: FeatureSet) -> tuple[float, list[str], dict]:
    reasons: list[str] = []
    f = {}

    # Momentum block (max 35)
    mom = 0.0
    if fs.ret_5d > 8: mom += 15; reasons.append(f"5日强势 +{fs.ret_5d:.1f}%")
    elif fs.ret_5d > 3: mom += 8
    if fs.macd_hist > 0 and fs.macd_dif > 0: mom += 10; reasons.append("MACD红柱在零轴上")
    if fs.macd_golden: mom += 10; reasons.append("MACD金叉新成")
    f["momentum"] = mom

    # Volume block (max 25)
    vol = 0.0
    if fs.vol_ratio_5 > 2.0: vol += 15; reasons.append(f"5日量比 {fs.vol_ratio_5:.1f}x")
    elif fs.vol_ratio_5 > 1.3: vol += 8
    if fs.vol_zscore_20 > 2.0: vol += 10; reasons.append("放量异常(z>2)")
    f["volume"] = vol

    # Breakout block (max 25)
    brk = 0.0
    if fs.is_breakout_20: brk += 15; reasons.append("突破20日新高")
    if fs.is_breakout_60: brk += 10; reasons.append("突破60日新高")
    f["breakout"] = brk

    # Concept heat (max 15)
    heat = 0.0
    if fs.concept_heat > 10: heat += 10; reasons.append(f"概念5日热度 {fs.concept_heat:.0f}%")
    elif fs.concept_heat > 5: heat += 5
    if fs.concept_inflow_bil > 1: heat += 5; reasons.append(f"主力净流入{fs.concept_inflow_bil:.1f}亿")
    f["heat"] = heat

    # Penalties
    pen = 0.0
    if fs.rsi14 > 80: pen += 10; reasons.append("RSI过热")
    if fs.atr_pct > 0.08: pen += 5
    if fs.market_cap_bil > 500: pen += 5  # 短线偏好中小盘
    f["penalty"] = -pen

    score = _clip(mom + vol + brk + heat - pen)
    return score, reasons, f


# ──────────────────────────────────────────────────────────────
#  2. SWING: 波段交易 (1–4 weeks, trend + pullback + SR)
# ──────────────────────────────────────────────────────────────

def score_swing(fs: FeatureSet) -> tuple[float, list[str], dict]:
    reasons: list[str] = []
    f = {}

    # Trend (max 35)
    tr = 0.0
    if fs.ma_aligned >= 1.0: tr += 15; reasons.append("均线多头排列")
    elif fs.ma_aligned >= 0.66: tr += 8
    if fs.trend_slope_20 > 0.3: tr += 10; reasons.append(f"20日斜率 {fs.trend_slope_20:.2f}%/日")
    if fs.above_ma20_days >= 10: tr += 10; reasons.append(f"持续 {fs.above_ma20_days} 日站上MA20")
    f["trend"] = tr

    # Pullback / entry quality (max 30)
    pb = 0.0
    if fs.is_pullback_to_ma10: pb += 15; reasons.append("回踩MA10企稳")
    if -3 < fs.near_ma20_pct < 5: pb += 10; reasons.append(f"贴近MA20 ({fs.near_ma20_pct:+.1f}%)")
    if 0.3 < fs.bb_position < 0.6: pb += 5  # 中轨附近
    f["pullback"] = pb

    # Momentum confirm (max 20)
    mc = 0.0
    if fs.macd_hist > 0: mc += 10
    if 40 < fs.rsi14 < 70: mc += 10; reasons.append(f"RSI健康 {fs.rsi14:.0f}")
    f["momentum"] = mc

    # Position vs 60-day range (max 15)
    pos = 0.0
    if -15 < fs.near_high_60_pct < -2: pos += 10; reasons.append("距60日高点合理回调")
    if fs.near_low_60_pct > 15: pos += 5
    f["position"] = pos

    # Penalties
    pen = 0.0
    if fs.rsi14 > 75: pen += 10; reasons.append("RSI偏高")
    if fs.drawdown_60d < -25: pen += 10; reasons.append("距60日高深度回撤")
    if fs.vol_20d_pct > 6: pen += 5
    f["penalty"] = -pen

    return _clip(tr + pb + mc + pos - pen), reasons, f


# ──────────────────────────────────────────────────────────────
#  3. VALUE: 中长线价值 (1–6 months, fundamental + tech filter)
# ──────────────────────────────────────────────────────────────

def score_value(fs: FeatureSet) -> tuple[float, list[str], dict]:
    reasons: list[str] = []
    f = {}

    # Fundamental quality (max 50)
    fq = 0.0
    if fs.roe > 15: fq += 15; reasons.append(f"ROE {fs.roe:.1f}%")
    elif fs.roe > 8: fq += 8
    if fs.net_profit_yoy > 30: fq += 15; reasons.append(f"净利同比 +{fs.net_profit_yoy:.0f}%")
    elif fs.net_profit_yoy > 10: fq += 8
    elif fs.net_profit_yoy < -20: fq -= 15; reasons.append("利润大幅下滑")
    if fs.revenue_yoy > 20: fq += 10; reasons.append(f"营收同比 +{fs.revenue_yoy:.0f}%")
    elif fs.revenue_yoy > 5: fq += 5
    if 0 < fs.pe_ratio < 25: fq += 10; reasons.append(f"PE {fs.pe_ratio:.0f}")
    elif fs.pe_ratio > 80 or fs.pe_ratio < 0: fq -= 10
    f["fundamental"] = fq

    # Trend safety (max 30): want price not in deep downtrend
    ts = 0.0
    if fs.price > fs.ma60: ts += 15; reasons.append("站稳MA60")
    if fs.ma_aligned >= 0.66: ts += 10
    if -10 < fs.near_high_60_pct < 0: ts += 5
    f["trend"] = ts

    # Size preference (max 20): mid-large cap for value play
    sp = 0.0
    if 50 < fs.market_cap_bil < 2000: sp += 15; reasons.append(f"市值 {fs.market_cap_bil:.0f}亿(适中)")
    elif fs.market_cap_bil >= 2000: sp += 10
    if fs.atr_pct < 0.04: sp += 5  # low-vol preference
    f["size"] = sp

    # Penalties
    pen = 0.0
    if fs.drawdown_60d < -30: pen += 10; reasons.append("近期跌幅过大,等企稳")
    if fs.rsi14 > 75: pen += 5
    f["penalty"] = -pen

    return _clip(fq + ts + sp - pen), reasons, f


# ──────────────────────────────────────────────────────────────
#  4. MULTI-FACTOR: 量化打分 (composite ranking factor)
# ──────────────────────────────────────────────────────────────

def score_multi_factor(fs: FeatureSet) -> tuple[float, list[str], dict]:
    """Balanced composite — mid-frequency rebalance candidate.

    Sub-factors (each normalised 0-1, then weighted):
      Momentum 0.30, Quality 0.25, Trend 0.20, LowVol 0.15, ConceptHeat 0.10
    """
    def _n(v: float, lo: float, hi: float) -> float:
        if hi <= lo: return 0.5
        return max(0.0, min(1.0, (v - lo) / (hi - lo)))

    mom = (_n(fs.ret_20d, -10, 25) * 0.5 + _n(fs.ret_60d, -15, 50) * 0.5)
    quality = (_n(fs.roe, 0, 25) * 0.5 + _n(fs.net_profit_yoy, -10, 60) * 0.5)
    trend = (fs.ma_aligned * 0.5 + _n(fs.above_ma20_days, 0, 30) * 0.5)
    lowvol = 1.0 - _n(fs.vol_20d_pct, 1, 8)
    heat = _n(fs.concept_heat, 0, 15)

    composite = (mom * 0.30 + quality * 0.25 + trend * 0.20
                 + lowvol * 0.15 + heat * 0.10)

    reasons: list[str] = []
    if mom > 0.7: reasons.append(f"动量强(20/60日 {fs.ret_20d:.0f}%/{fs.ret_60d:.0f}%)")
    if quality > 0.7: reasons.append(f"基本面优(ROE {fs.roe:.0f}, 利润+{fs.net_profit_yoy:.0f}%)")
    if trend > 0.7: reasons.append("趋势良好")
    if lowvol > 0.7: reasons.append(f"波动低 {fs.vol_20d_pct:.1f}%")
    if heat > 0.5: reasons.append("有题材热度")
    if not reasons: reasons.append("综合得分中等")

    f = {"momentum": mom, "quality": quality, "trend": trend,
         "lowvol": lowvol, "heat": heat}

    return _clip(composite * 100), reasons, f


# ──────────────────────────────────────────────────────────────
#  Universal quality gate — applied AFTER every style scorer.
#  Penalises picks with bad tradability or no room to profit
#  (close to resistance, just hit limit-up, market bear, …).
# ──────────────────────────────────────────────────────────────

def _apply_quality_gate(
    style: str,
    score: float,
    reasons: list[str],
    factors: dict,
    fs: FeatureSet,
) -> tuple[float, list[str], dict]:
    pen = 0.0
    notes: list[str] = []

    # ── Hard rejects → caller drops these ──
    if fs.is_limit_up:
        notes.append("⚠️ 今日封涨停,无法买入")
        factors["quality_gate"] = -100.0
        return 0.0, reasons + notes, factors
    if fs.is_recently_xrxd:
        notes.append("⚠️ 5日内疑似除权,数据失真")
        factors["quality_gate"] = -100.0
        return 0.0, reasons + notes, factors
    if fs.data_stale_days >= 7:
        notes.append(f"⚠️ 行情数据滞后 {fs.data_stale_days} 日")
        factors["quality_gate"] = -100.0
        return 0.0, reasons + notes, factors

    # ── Resistance proximity (the main fix). ATR-relative ──
    atr_pct = max(fs.atr_pct * 100, 1.0)
    d = fs.dist_to_resistance_pct
    if d < atr_pct * 0.5:
        if style == "short_term" and fs.is_breakout_20:
            notes.append(f"突破压力位 (距阻力 {d:.1f}%)")
            pen += 5
        else:
            pen += 20; notes.append(f"⚠️ 紧贴阻力位 ({d:.1f}%)")
    elif d < atr_pct * 1.0:
        pen += 10; notes.append(f"距阻力较近 ({d:.1f}%)")
    elif d < atr_pct * 1.5:
        pen += 3

    # ── Already ran today:追高风险 ──
    if style != "short_term" and fs.change_pct_today > 5:
        pen += 10; notes.append(f"今日已涨 {fs.change_pct_today:.1f}%,追高风险")
    if fs.change_pct_today > 7:
        pen += 10

    # ── Limit-down: 只有价值风格还能接 ──
    if fs.is_limit_down and style != "value":
        pen += 25; notes.append("今日跌停,等企稳")
    elif fs.is_limit_down:
        pen += 10

    # ── Market environment ──
    if fs.market_trend < -0.5:
        if style in ("short_term", "swing"):
            pen += 15; notes.append("大盘偏弱,降权")
        else:
            pen += 5
    elif fs.market_trend > 0.5 and style == "short_term":
        score += 3

    # ── Friday weekend gap risk for short-term ──
    if fs.is_friday and style == "short_term":
        pen += 8; notes.append("周五持仓过周末")
    if fs.days_to_holiday <= 1 and style == "short_term":
        pen += 10; notes.append(f"距假期{fs.days_to_holiday}日")

    factors["quality_gate"] = -pen
    final = max(0.0, min(100.0, score - pen))
    return final, reasons + notes, factors


def passes_style_filter(style: str, fs: FeatureSet) -> tuple[bool, str]:
    """Hard pre-filter; returns (ok, reject_reason)."""
    if fs.is_limit_up:
        return False, "limit_up"
    if fs.is_recently_xrxd:
        return False, "recently_xrxd"
    if fs.data_stale_days >= 7:
        return False, "data_stale"

    if style == "short_term":
        if fs.vol_ratio_5 < 1.2 and fs.ret_5d < 2:
            return False, "no_momentum"
        if fs.market_trend < -0.7:
            return False, "bear_market"
    elif style == "swing":
        if fs.ma_aligned < 0.33:
            return False, "no_trend"
        if fs.drawdown_60d < -30:
            return False, "deep_drawdown"
    elif style == "value":
        if fs.roe < 5 and fs.net_profit_yoy < -10 and fs.pe_ratio > 30:
            return False, "weak_fundamental"
    return True, ""


# ── Dispatch table ────────────────────────────────────────────
SCORERS = {
    "short_term": score_short_term,
    "swing": score_swing,
    "value": score_value,
    "multi_factor": score_multi_factor,
}


def score(style: str, fs: FeatureSet) -> tuple[float, list[str], dict]:
    if style == "ai_ensemble":
        from .ml.ensemble import score_ai_ensemble
        s, r, f = score_ai_ensemble(fs, seq_window=None)
    else:
        fn = SCORERS.get(style, score_swing)
        s, r, f = fn(fs)
    return _apply_quality_gate(style, s, r, f, fs)
