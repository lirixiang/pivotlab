"""龙头战法 (Dragon Head Strategy) — 2-stage model + market cycle judgment.

Architecture:
  Stage 1: DragonIdentifier (LightGBM)  — score each ZT pool stock 0-100
  Stage 2: DragonTimingModel (Transformer) — buy/sell/hold for identified dragons
  Plus:    MarketCycleJudge — global ice/warmup/peak/cooldown cycle filter
           HotMoneyKnowledgeBase — encoded trader playbook patterns
           DragonSignalGenerator — combine all into final actionable signals

Public functions:
  build_dragon_features(code, trade_date) -> 15-dim feature dict
  identify_dragons(trade_date, threshold) -> ranked list of dragon candidates
  generate_dragon_signal(code, trade_date) -> buy/sell/hold + price levels
  judge_market_cycle(trade_date) -> dict with phase/score/metrics
  train_dragon_models(start_date, end_date, ...) -> training stats
  dragon_model_status() -> trained model status
  backtest_dragon(start_date, end_date, ...) -> backtest report
"""
from __future__ import annotations

import json
import logging
import math
import pickle
import time as _time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, date as _date
from pathlib import Path
from typing import Optional

import numpy as np
from sqlalchemy import select, func, and_

from ..models import (
    ZtPoolDaily, LhbRecord, LhbSeatDetail, ConceptHeatHistory,
    DailyCandle, Stock, StockConcept, ConceptBoard, DragonSignal,
)
from ..schemas import Candle
from .sync_service import _get_session
from .data_provider import get_candles

logger = logging.getLogger(__name__)

_MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "models"
_MODEL_DIR.mkdir(exist_ok=True)

DRAGON_FEATURE_NAMES = [
    # 涨停特征 (4)
    "is_zt", "consecutive", "zt_strength", "zt_amount_rank",
    # 板块特征 (4)
    "sector_heat", "sector_rank_norm", "sector_zt_count", "sector_consecutive_days",
    # 资金/龙虎榜 (3)
    "lhb_net_buy_ratio", "hot_money_seats", "lhb_freq_5d",
    # 市场情绪 (4)
    "market_zt_count_norm", "market_high_consecutive", "market_blast_rate", "market_sentiment",
]
NUM_DRAGON_FEATURES = len(DRAGON_FEATURE_NAMES)
TIMING_SEQ_LEN = 10  # past 10 days for timing model

LIMIT_UP_THRESHOLDS = {
    "沪A": 0.099, "深A": 0.099,            # 10%
    "创业板": 0.199, "科创板": 0.199,        # 20%
    "北交所": 0.299,                        # 30%
}
DEFAULT_LIMIT_UP = 0.099


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def _last_trading_dates(end_date: str, n: int) -> list[str]:
    """Return up to n distinct trading dates <= end_date from daily_candles."""
    with _get_session() as s:
        rows = s.execute(
            select(DailyCandle.trade_date)
            .where(DailyCandle.trade_date <= end_date)
            .group_by(DailyCandle.trade_date)
            .order_by(DailyCandle.trade_date.desc())
            .limit(n)
        ).all()
    return sorted([r[0] for r in rows])


def _stock_market_map() -> dict[str, str]:
    with _get_session() as s:
        rows = s.execute(select(Stock.code, Stock.market)).all()
    return {c: m or "" for c, m in rows}


def _limit_up_ratio(market: str) -> float:
    return LIMIT_UP_THRESHOLDS.get(market, DEFAULT_LIMIT_UP)


# ═══════════════════════════════════════════════════════════════
# Market cycle judge
# ═══════════════════════════════════════════════════════════════

@dataclass
class MarketCycle:
    trade_date: str
    phase: str               # ice / warmup / peak / cooldown
    score: float             # 0-100 composite sentiment
    zt_count: int
    blast_count: int         # 炸板数
    blast_rate: float        # 炸板率
    high_consecutive: int    # 最高连板
    consecutive_3plus: int   # 3板及以上数
    yesterday_zt_today_perf: float  # 昨日涨停股今日平均涨幅 (情绪温度)


def judge_market_cycle(trade_date: str) -> MarketCycle:
    """Evaluate market cycle for the given trade_date based on ZT pool stats."""
    with _get_session() as s:
        zt = s.execute(
            select(ZtPoolDaily).where(
                ZtPoolDaily.trade_date == trade_date,
                ZtPoolDaily.pool_type == "zt",
            )
        ).scalars().all()
        zb = s.execute(
            select(func.count()).select_from(ZtPoolDaily).where(
                ZtPoolDaily.trade_date == trade_date,
                ZtPoolDaily.pool_type == "zb",
            )
        ).scalar() or 0

        zt_count = len(zt)
        blast_count = int(zb)
        total_attempts = zt_count + blast_count
        blast_rate = (blast_count / total_attempts) if total_attempts > 0 else 0.0
        high_consecutive = max((r.consecutive for r in zt), default=0)
        consecutive_3plus = sum(1 for r in zt if r.consecutive >= 3)

        # Yesterday's ZT today's performance (sentiment thermometer)
        prev_dates = _last_trading_dates(trade_date, 2)
        yt_perf = 0.0
        if len(prev_dates) >= 2:
            prev_d = prev_dates[-2]
            prev_zt = s.execute(
                select(ZtPoolDaily.code).where(
                    ZtPoolDaily.trade_date == prev_d,
                    ZtPoolDaily.pool_type == "zt",
                )
            ).scalars().all()
            if prev_zt:
                today_quotes = s.execute(
                    select(DailyCandle.code, DailyCandle.change_pct).where(
                        DailyCandle.trade_date == trade_date,
                        DailyCandle.code.in_(prev_zt),
                    )
                ).all()
                pcts = [p for _, p in today_quotes if p is not None]
                if pcts:
                    yt_perf = float(np.mean(pcts))

    # Composite sentiment 0-100
    score = 0.0
    score += min(zt_count, 100) / 100 * 30
    score += min(high_consecutive, 8) / 8 * 25
    score += min(consecutive_3plus, 15) / 15 * 15
    score += max(min((yt_perf + 5) / 10, 1.0), 0.0) * 20  # -5%..+5% maps to 0..1
    score += (1 - min(blast_rate, 0.7) / 0.7) * 10
    score = round(score, 1)

    # Phase classification (heuristic thresholds)
    if zt_count < 30 and high_consecutive < 3:
        phase = "ice"          # 冰点期
    elif yt_perf < -3 or (blast_rate > 0.5 and zt_count < 50):
        phase = "cooldown"     # 退潮期
    elif zt_count >= 70 and high_consecutive >= 5:
        phase = "peak"         # 高潮期
    else:
        phase = "warmup"       # 回暖期

    return MarketCycle(
        trade_date=trade_date, phase=phase, score=score,
        zt_count=zt_count, blast_count=blast_count, blast_rate=round(blast_rate, 4),
        high_consecutive=high_consecutive, consecutive_3plus=consecutive_3plus,
        yesterday_zt_today_perf=round(yt_perf, 2),
    )


# ═══════════════════════════════════════════════════════════════
# Feature engineering — 15-dim dragon feature vector
# ═══════════════════════════════════════════════════════════════

def build_dragon_features(code: str, trade_date: str,
                           market_cycle: MarketCycle | None = None) -> dict[str, float] | None:
    """Build 15-dim feature vector for one stock at one trade_date.

    Returns None if insufficient data (typically: stock not listed or no quote).
    """
    with _get_session() as s:
        # ZT pool entry (if any)
        zt_row = s.execute(
            select(ZtPoolDaily).where(
                ZtPoolDaily.trade_date == trade_date,
                ZtPoolDaily.code == code,
                ZtPoolDaily.pool_type == "zt",
            )
        ).scalar_one_or_none()

        # Today's candle
        candle = s.execute(
            select(DailyCandle).where(
                DailyCandle.trade_date == trade_date,
                DailyCandle.code == code,
            )
        ).scalar_one_or_none()
        if not candle:
            return None

        # Stock master
        stock = s.get(Stock, code)
        market = stock.market if stock else ""

        # Concept(s) — pick the most heat-rich one as primary
        cs = s.execute(
            select(StockConcept.concept).where(StockConcept.code == code)
        ).scalars().all()
        primary_concept = ""
        sector_heat = 0.0
        sector_rank_norm = 0.0
        sector_zt_count = 0
        sector_cons_days = 0
        if cs:
            heat_rows = s.execute(
                select(ConceptHeatHistory).where(
                    ConceptHeatHistory.trade_date == trade_date,
                    ConceptHeatHistory.concept.in_(cs),
                )
            ).scalars().all()
            if heat_rows:
                # Pick concept with highest heat_score
                best = max(heat_rows, key=lambda h: h.heat_score or 0)
                primary_concept = best.concept
                sector_heat = float(best.heat_score or 0)
                sector_zt_count = best.zt_count or 0
                # Rank normalize (1 = most-hot, lower=stronger)
                sector_rank_norm = max(0.0, 1.0 - (best.rank or 100) / 100.0) if best.rank else 0.0
                # consecutive days the sector has been "hot/core"
                hist = s.execute(
                    select(ConceptHeatHistory.heat_level).where(
                        ConceptHeatHistory.concept == best.concept,
                        ConceptHeatHistory.trade_date <= trade_date,
                    ).order_by(ConceptHeatHistory.trade_date.desc()).limit(10)
                ).scalars().all()
                for lvl in hist:
                    if lvl in ("core", "hot"):
                        sector_cons_days += 1
                    else:
                        break

        # LHB features
        lhb_row = s.execute(
            select(LhbRecord).where(
                LhbRecord.trade_date == trade_date, LhbRecord.code == code,
            )
        ).scalar_one_or_none()
        lhb_net_ratio = 0.0
        hot_money_seats = 0
        if lhb_row:
            if lhb_row.turnover and lhb_row.turnover > 0:
                lhb_net_ratio = float(lhb_row.net_amount or 0) / float(lhb_row.turnover)
            seat_rows = s.execute(
                select(LhbSeatDetail).where(
                    LhbSeatDetail.trade_date == trade_date,
                    LhbSeatDetail.code == code,
                )
            ).scalars().all()
            hot_money_seats = sum(1 for r in seat_rows if r.is_known_hot)

        # LHB frequency over last 5 trading days
        prev_dates = _last_trading_dates(trade_date, 5)
        lhb_freq = 0
        if prev_dates:
            lhb_freq = s.execute(
                select(func.count()).select_from(LhbRecord).where(
                    LhbRecord.code == code,
                    LhbRecord.trade_date.in_(prev_dates),
                )
            ).scalar() or 0

    # Market features (cached if provided)
    mc = market_cycle or judge_market_cycle(trade_date)

    # ZT-specific features
    is_zt = 1.0 if zt_row else 0.0
    consecutive = float(zt_row.consecutive) if zt_row else 0.0
    # Seal strength: time-based + open count
    if zt_row:
        try:
            t = zt_row.first_zt_time or "15:00:00"
            hh, mm, _ = (int(x) for x in t.split(":"))
            mins_from_open = max(0, (hh - 9) * 60 + mm - 30)
            time_score = max(0.0, 1.0 - mins_from_open / 240.0)
        except Exception:
            time_score = 0.5
        open_penalty = max(0.0, 1.0 - 0.2 * (zt_row.open_count or 0))
        zt_strength = round(time_score * open_penalty, 4)
    else:
        zt_strength = 0.0

    # ZT amount rank (within today's ZT pool)
    zt_amount_rank = 0.0
    if zt_row:
        with _get_session() as s:
            amounts = s.execute(
                select(ZtPoolDaily.amount).where(
                    ZtPoolDaily.trade_date == trade_date,
                    ZtPoolDaily.pool_type == "zt",
                )
            ).scalars().all()
        amts = sorted([a for a in amounts if a], reverse=True)
        if amts and zt_row.amount:
            try:
                rank = amts.index(zt_row.amount) + 1
                zt_amount_rank = round(1.0 - (rank - 1) / max(len(amts), 1), 4)
            except ValueError:
                zt_amount_rank = 0.0

    return {
        "is_zt": is_zt,
        "consecutive": consecutive,
        "zt_strength": zt_strength,
        "zt_amount_rank": zt_amount_rank,
        "sector_heat": sector_heat,
        "sector_rank_norm": sector_rank_norm,
        "sector_zt_count": float(sector_zt_count),
        "sector_consecutive_days": float(sector_cons_days),
        "lhb_net_buy_ratio": float(lhb_net_ratio),
        "hot_money_seats": float(hot_money_seats),
        "lhb_freq_5d": float(lhb_freq),
        "market_zt_count_norm": min(mc.zt_count, 150) / 150.0,
        "market_high_consecutive": float(mc.high_consecutive),
        "market_blast_rate": float(mc.blast_rate),
        "market_sentiment": mc.score / 100.0,
        "_primary_concept": primary_concept,  # extra metadata, not in vector
        "_market": market,
        "_close": candle.close,
        "_change_pct": candle.change_pct or 0.0,
    }


def _features_to_vector(feat: dict) -> np.ndarray:
    return np.array([feat[k] for k in DRAGON_FEATURE_NAMES], dtype=np.float32)


# ═══════════════════════════════════════════════════════════════
# Dataset building (Stage 1: dragon classifier)
# ═══════════════════════════════════════════════════════════════

def _build_stage1_dataset(start_date: str, end_date: str,
                           min_zt_per_day: int = 10) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    """Build Stage 1 dataset: for each ZT-pool entry, label as dragon (1) or not (0).

    Label rule:
      label=1 if next 2 days the stock continues to limit-up
              AND its change% is top-3 in its primary concept on the entry day.
    """
    market_map = _stock_market_map()
    X, y, meta = [], [], []

    with _get_session() as s:
        dates = s.execute(
            select(ZtPoolDaily.trade_date).where(
                ZtPoolDaily.trade_date.between(start_date, end_date),
                ZtPoolDaily.pool_type == "zt",
            ).group_by(ZtPoolDaily.trade_date).order_by(ZtPoolDaily.trade_date)
        ).scalars().all()

    for d in dates:
        mc = judge_market_cycle(d)
        with _get_session() as s:
            zt_codes = s.execute(
                select(ZtPoolDaily.code).where(
                    ZtPoolDaily.trade_date == d,
                    ZtPoolDaily.pool_type == "zt",
                )
            ).scalars().all()
        if len(zt_codes) < min_zt_per_day:
            continue

        # Future windows: next 2 trading days
        future = _trading_dates_after(d, 2)
        if len(future) < 2:
            continue

        for code in zt_codes:
            feat = build_dragon_features(code, d, market_cycle=mc)
            if feat is None:
                continue
            # Future limit-up check
            mkt = market_map.get(code, "")
            lu = _limit_up_ratio(mkt) * 100  # in %
            with _get_session() as s:
                fc = s.execute(
                    select(DailyCandle.trade_date, DailyCandle.change_pct).where(
                        DailyCandle.code == code,
                        DailyCandle.trade_date.in_(future),
                    )
                ).all()
            future_pcts = {dd: (pct or 0) for dd, pct in fc}
            zt_streak = sum(1 for dd in future if future_pcts.get(dd, 0) >= lu - 0.2)

            # Concept rank check
            top_in_concept = False
            primary = feat.get("_primary_concept", "")
            if primary:
                with _get_session() as s:
                    same_concept_codes = s.execute(
                        select(StockConcept.code).where(StockConcept.concept == primary)
                    ).scalars().all()
                    pcts = s.execute(
                        select(DailyCandle.code, DailyCandle.change_pct).where(
                            DailyCandle.trade_date == d,
                            DailyCandle.code.in_(same_concept_codes),
                        )
                    ).all()
                ranking = sorted([(c, p or 0) for c, p in pcts], key=lambda x: -x[1])
                top3_codes = {c for c, _ in ranking[:3]}
                top_in_concept = code in top3_codes

            label = 1 if (zt_streak >= 1 and top_in_concept) else 0
            X.append(_features_to_vector(feat))
            y.append(label)
            meta.append({"trade_date": d, "code": code, "label": label,
                         "zt_streak": zt_streak, "concept": primary})

    if not X:
        return np.empty((0, NUM_DRAGON_FEATURES), dtype=np.float32), np.empty((0,), dtype=np.int64), []
    return np.vstack(X), np.array(y, dtype=np.int64), meta


def _trading_dates_after(start_date: str, n: int) -> list[str]:
    with _get_session() as s:
        rows = s.execute(
            select(DailyCandle.trade_date).where(
                DailyCandle.trade_date > start_date
            ).group_by(DailyCandle.trade_date)
            .order_by(DailyCandle.trade_date).limit(n)
        ).all()
    return [r[0] for r in rows]


# ═══════════════════════════════════════════════════════════════
# Stage 1: LightGBM dragon identifier
# ═══════════════════════════════════════════════════════════════

def train_dragon_identifier(start_date: str, end_date: str,
                             progress_cb=None) -> dict:
    import lightgbm as lgb
    from sklearn.metrics import accuracy_score, roc_auc_score, classification_report

    t0 = _time.time()
    if progress_cb:
        progress_cb(5, "构建Stage 1数据集 (龙头识别)...")
    X, y, meta = _build_stage1_dataset(start_date, end_date)

    if len(X) < 200:
        return {"error": f"insufficient samples: {len(X)} (need >=200)"}

    pos = int((y == 1).sum())
    neg = int((y == 0).sum())
    if pos < 20 or neg < 20:
        return {"error": f"class imbalance: pos={pos}, neg={neg}"}

    if progress_cb:
        progress_cb(20, f"数据: {len(X)} 样本 (龙头={pos} 非龙头={neg})")

    split = int(len(X) * 0.8)
    Xtr, Xte = X[:split], X[split:]
    ytr, yte = y[:split], y[split:]

    weights = np.where(ytr == 1, neg / max(pos, 1), 1.0)
    dtrain = lgb.Dataset(Xtr, label=ytr, weight=weights, feature_name=DRAGON_FEATURE_NAMES)
    dtest = lgb.Dataset(Xte, label=yte, reference=dtrain)

    params = {
        "objective": "binary",
        "metric": "auc",
        "learning_rate": 0.05,
        "num_leaves": 31,
        "max_depth": 6,
        "min_child_samples": 10,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "verbose": -1,
    }

    def _cb(env):
        if progress_cb and env.iteration % 30 == 0:
            pct = 20 + int(env.iteration / 300 * 60)
            progress_cb(min(pct, 80), f"LightGBM: round {env.iteration}/300")

    model = lgb.train(
        params, dtrain,
        num_boost_round=300, valid_sets=[dtest],
        callbacks=[lgb.early_stopping(30, verbose=False), _cb],
    )

    proba = model.predict(Xte)
    pred = (proba >= 0.5).astype(int)
    acc = accuracy_score(yte, pred)
    try:
        auc = roc_auc_score(yte, proba)
    except Exception:
        auc = 0.5
    report = classification_report(yte, pred, target_names=["non_dragon", "dragon"],
                                   output_dict=True, zero_division=0)
    importance = dict(zip(DRAGON_FEATURE_NAMES,
                          model.feature_importance(importance_type="gain").tolist()))

    if progress_cb:
        progress_cb(90, "保存Stage 1模型...")
    model_path = _MODEL_DIR / "dragon_stage1.pkl"
    with open(model_path, "wb") as f:
        pickle.dump(model, f)

    elapsed = _time.time() - t0
    return {
        "stage": 1,
        "model": "lightgbm",
        "samples": int(len(X)),
        "positive": pos,
        "negative": neg,
        "accuracy": round(acc, 4),
        "auc": round(auc, 4),
        "dragon_precision": round(report["dragon"]["precision"], 4),
        "dragon_recall": round(report["dragon"]["recall"], 4),
        "feature_importance": {k: round(v, 1)
                                for k, v in sorted(importance.items(), key=lambda x: -x[1])},
        "elapsed_sec": round(elapsed, 1),
    }


def predict_dragon_score(code: str, trade_date: str,
                          market_cycle: MarketCycle | None = None) -> dict | None:
    """Return dict with dragon_prob (0-1), dragon_score (0-100), and feature snapshot."""
    model_path = _MODEL_DIR / "dragon_stage1.pkl"
    if not model_path.exists():
        return None
    feat = build_dragon_features(code, trade_date, market_cycle=market_cycle)
    if feat is None:
        return None
    with open(model_path, "rb") as f:
        model = pickle.load(f)
    X = _features_to_vector(feat).reshape(1, -1)
    prob = float(model.predict(X)[0])
    return {
        "code": code,
        "trade_date": trade_date,
        "dragon_prob": round(prob, 4),
        "dragon_score": round(prob * 100, 1),
        "is_zt": bool(feat["is_zt"]),
        "consecutive": int(feat["consecutive"]),
        "concept": feat.get("_primary_concept", ""),
        "sector_heat": feat["sector_heat"],
        "hot_money_seats": int(feat["hot_money_seats"]),
        "close": feat.get("_close"),
        "change_pct": feat.get("_change_pct"),
    }


# ═══════════════════════════════════════════════════════════════
# Stage 2: Transformer timing model
# ═══════════════════════════════════════════════════════════════

def _build_stage2_dataset(start_date: str, end_date: str) -> tuple[np.ndarray, np.ndarray]:
    """Build (N, seq_len, 16) sequences + labels for identified dragons.

    Sequence = past 10 days of [15-dim dragon features + dragon_score].
    Label:
       buy (1):  next 5d max return > 8% AND max drawdown > -5%
       sell (2): next 3d return < -3% OR consecutive breaks
       hold (0): otherwise
    """
    model_path = _MODEL_DIR / "dragon_stage1.pkl"
    if not model_path.exists():
        return np.empty((0, TIMING_SEQ_LEN, NUM_DRAGON_FEATURES + 1), dtype=np.float32), np.empty((0,), dtype=np.int64)
    with open(model_path, "rb") as f:
        stage1 = pickle.load(f)

    market_map = _stock_market_map()
    X_seqs, y_labels = [], []

    with _get_session() as s:
        dates = s.execute(
            select(ZtPoolDaily.trade_date).where(
                ZtPoolDaily.trade_date.between(start_date, end_date),
                ZtPoolDaily.pool_type == "zt",
                ZtPoolDaily.consecutive >= 2,  # focus on 2板+ stocks
            ).group_by(ZtPoolDaily.trade_date).order_by(ZtPoolDaily.trade_date)
        ).scalars().all()

    for d in dates:
        mc = judge_market_cycle(d)
        with _get_session() as s:
            codes = s.execute(
                select(ZtPoolDaily.code).where(
                    ZtPoolDaily.trade_date == d,
                    ZtPoolDaily.pool_type == "zt",
                    ZtPoolDaily.consecutive >= 2,
                )
            ).scalars().all()
        prev_dates = _last_trading_dates(d, TIMING_SEQ_LEN)
        if len(prev_dates) < TIMING_SEQ_LEN:
            continue
        future_dates = _trading_dates_after(d, 5)
        if len(future_dates) < 3:
            continue

        for code in codes:
            # Build sequence
            seq_rows = []
            valid = True
            for pd_ in prev_dates:
                f = build_dragon_features(code, pd_)
                if f is None:
                    valid = False
                    break
                vec = list(_features_to_vector(f))
                # Append stage1 score for that day
                X = np.array(vec, dtype=np.float32).reshape(1, -1)
                s1 = float(stage1.predict(X)[0])
                vec.append(s1)
                seq_rows.append(vec)
            if not valid or len(seq_rows) != TIMING_SEQ_LEN:
                continue

            # Label
            with _get_session() as s:
                fc = s.execute(
                    select(DailyCandle.trade_date, DailyCandle.close, DailyCandle.change_pct).where(
                        DailyCandle.code == code,
                        DailyCandle.trade_date.in_(future_dates),
                    ).order_by(DailyCandle.trade_date)
                ).all()
            base_close = None
            with _get_session() as s:
                today_c = s.execute(
                    select(DailyCandle.close).where(
                        DailyCandle.code == code, DailyCandle.trade_date == d,
                    )
                ).scalar()
                base_close = today_c
            if not base_close or not fc:
                continue
            closes = [c for _, c, _ in fc if c]
            if not closes:
                continue
            max_ret = (max(closes) - base_close) / base_close * 100
            min_ret = (min(closes) - base_close) / base_close * 100
            ret3d = ((fc[min(2, len(fc) - 1)][1] - base_close) / base_close * 100) if len(fc) >= 1 else 0.0

            mkt = market_map.get(code, "")
            lu = _limit_up_ratio(mkt) * 100
            future_pcts = [p or 0 for _, _, p in fc]
            cons_break = (future_pcts[0] < lu - 0.2)  # next day no longer ZT

            if max_ret > 8 and min_ret > -5:
                label = 1  # buy
            elif ret3d < -3 or cons_break and max_ret < 5:
                label = 2  # sell
            else:
                label = 0  # hold

            X_seqs.append(np.array(seq_rows, dtype=np.float32))
            y_labels.append(label)

    if not X_seqs:
        return (np.empty((0, TIMING_SEQ_LEN, NUM_DRAGON_FEATURES + 1), dtype=np.float32),
                np.empty((0,), dtype=np.int64))
    return np.stack(X_seqs), np.array(y_labels, dtype=np.int64)


def _build_timing_transformer(n_features: int):
    import torch
    import torch.nn as nn

    class TimingTransformer(nn.Module):
        def __init__(self):
            super().__init__()
            d_model = 64
            self.input_proj = nn.Linear(n_features, d_model)
            enc_layer = nn.TransformerEncoderLayer(
                d_model=d_model, nhead=4, dim_feedforward=128,
                dropout=0.1, batch_first=True,
            )
            self.encoder = nn.TransformerEncoder(enc_layer, num_layers=2)
            self.norm = nn.LayerNorm(d_model)
            self.cls = nn.Sequential(
                nn.Linear(d_model, 32), nn.ReLU(), nn.Dropout(0.1),
                nn.Linear(32, 3),
            )

        def forward(self, x):
            x = self.input_proj(x)
            x = self.encoder(x)
            x = self.norm(x[:, -1, :])
            return self.cls(x)

    return TimingTransformer


def train_dragon_timing(start_date: str, end_date: str, epochs: int = 30,
                         batch_size: int = 32, progress_cb=None) -> dict:
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset

    t0 = _time.time()
    if progress_cb:
        progress_cb(5, "构建Stage 2数据集 (买卖时机)...")
    X, y = _build_stage2_dataset(start_date, end_date)
    if len(X) < 100:
        return {"error": f"insufficient samples: {len(X)}"}

    n_feat = X.shape[2]
    counts = {int(c): int((y == c).sum()) for c in [0, 1, 2]}
    if progress_cb:
        progress_cb(15, f"数据: {len(X)} 序列, 类别: {counts}")

    # Normalize features (per-feature across all timesteps)
    mean = X.reshape(-1, n_feat).mean(axis=0)
    std = X.reshape(-1, n_feat).std(axis=0) + 1e-8
    Xn = (X - mean) / std

    norm_path = _MODEL_DIR / "dragon_stage2_norm.pkl"
    with open(norm_path, "wb") as f:
        pickle.dump({"mean": mean, "std": std}, f)

    split = int(len(Xn) * 0.8)
    Xtr, Xte = Xn[:split], Xn[split:]
    ytr, yte = y[:split], y[split:]

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    cls_w = torch.ones(3, device=device)
    for c in range(3):
        cnt = counts.get(c, 0)
        cls_w[c] = (len(y) / (3 * cnt)) if cnt > 0 else 1.0

    train_dl = DataLoader(
        TensorDataset(torch.from_numpy(Xtr), torch.from_numpy(ytr)),
        batch_size=batch_size, shuffle=True,
    )
    test_dl = DataLoader(
        TensorDataset(torch.from_numpy(Xte), torch.from_numpy(yte)),
        batch_size=batch_size,
    )

    Net = _build_timing_transformer(n_feat)
    model = Net().to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs)
    loss_fn = nn.CrossEntropyLoss(weight=cls_w)

    best_acc = 0.0
    best_state = None
    for ep in range(epochs):
        model.train()
        ep_loss = 0.0
        for xb, yb in train_dl:
            xb, yb = xb.to(device), yb.to(device)
            opt.zero_grad()
            logits = model(xb)
            loss = loss_fn(logits, yb)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            ep_loss += loss.item() * len(xb)
        sched.step()

        # Eval
        model.eval()
        correct, tot = 0, 0
        with torch.no_grad():
            for xb, yb in test_dl:
                xb, yb = xb.to(device), yb.to(device)
                pred = model(xb).argmax(1)
                correct += (pred == yb).sum().item()
                tot += len(yb)
        acc = correct / max(tot, 1)
        if progress_cb:
            pct = 20 + int((ep + 1) / epochs * 70)
            progress_cb(min(pct, 90), f"Transformer: ep {ep+1}/{epochs} acc={acc:.3f}")
        if acc > best_acc:
            best_acc = acc
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}

    if best_state is None:
        best_state = model.state_dict()
    torch.save(best_state, _MODEL_DIR / "dragon_stage2.pt")

    elapsed = _time.time() - t0
    return {
        "stage": 2,
        "model": "transformer",
        "device": str(device),
        "samples": int(len(X)),
        "class_counts": counts,
        "accuracy": round(best_acc, 4),
        "epochs": epochs,
        "elapsed_sec": round(elapsed, 1),
    }


def predict_dragon_timing(code: str, trade_date: str) -> dict | None:
    """Predict buy/sell/hold for a dragon candidate at trade_date."""
    import torch

    pt_path = _MODEL_DIR / "dragon_stage2.pt"
    norm_path = _MODEL_DIR / "dragon_stage2_norm.pkl"
    s1_path = _MODEL_DIR / "dragon_stage1.pkl"
    if not (pt_path.exists() and norm_path.exists() and s1_path.exists()):
        return None
    with open(s1_path, "rb") as f:
        stage1 = pickle.load(f)
    with open(norm_path, "rb") as f:
        norm = pickle.load(f)

    prev_dates = _last_trading_dates(trade_date, TIMING_SEQ_LEN)
    if len(prev_dates) < TIMING_SEQ_LEN:
        return None
    seq = []
    for d in prev_dates:
        f_ = build_dragon_features(code, d)
        if f_ is None:
            return None
        v = list(_features_to_vector(f_))
        x = np.array(v, dtype=np.float32).reshape(1, -1)
        v.append(float(stage1.predict(x)[0]))
        seq.append(v)
    arr = np.array([seq], dtype=np.float32)
    n_feat = arr.shape[2]
    arr = (arr - norm["mean"]) / norm["std"]

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    Net = _build_timing_transformer(n_feat)
    model = Net().to(device)
    model.load_state_dict(torch.load(pt_path, map_location=device, weights_only=True))
    model.eval()
    with torch.no_grad():
        logits = model(torch.from_numpy(arr).to(device))
        probs = torch.softmax(logits, dim=1)[0].cpu().numpy()
    action = ["hold", "buy", "sell"][int(np.argmax(probs))]
    return {
        "action": action,
        "hold_prob": round(float(probs[0]), 4),
        "buy_prob": round(float(probs[1]), 4),
        "sell_prob": round(float(probs[2]), 4),
        "confidence": round(float(np.max(probs)), 4),
    }


# ═══════════════════════════════════════════════════════════════
# Hot money knowledge base — encoded playbook patterns
# ═══════════════════════════════════════════════════════════════

HOT_MONEY_PATTERNS = {
    "buy": {
        "首板低吸":   "板块刚启动 + 低位首板 + 换手>10%",
        "二板确认":   "首板放量 + 二板缩量 → 趋势确认",
        "龙头反包":   "炸板次日高开 + 量能放大 → 强势反包",
        "补涨龙":    "总龙头见顶 + 同板块低位股补涨启动",
    },
    "sell": {
        "天地板":    "龙头高位天地板 → 周期结束",
        "核按钮":    "板块全线大面 → 主线退潮",
        "龙头分歧":   "连板断裂 + 放巨量 → 最后卖出机会",
        "补涨到位":   "补涨股涨幅接近龙头 → 性价比消失",
    },
    "cycle": {
        "ice":       "涨停<30只, 最高板<3 → 不参与",
        "warmup":    "涨停增加, 出现3板+ → 试探性参与",
        "peak":      "涨停>80只, 5板+ → 追龙头/做补涨",
        "cooldown":  "龙头断板, 炸板率>50% → 只卖不买",
    },
}


# ═══════════════════════════════════════════════════════════════
# Signal generation — combines models + cycle + risk control
# ═══════════════════════════════════════════════════════════════

@dataclass
class DragonSignalResult:
    code: str
    name: str
    trade_date: str
    signal_type: str             # buy / sell / hold
    dragon_score: float
    dragon_rank: int
    consecutive: int
    concept: str
    market_cycle: str
    model_confidence: float
    entry_price: float | None
    stop_price: float | None
    target_price: float | None
    reasons: list[str] = field(default_factory=list)
    feature_snapshot: dict = field(default_factory=dict)


def identify_dragons(trade_date: str, score_threshold: float = 60.0,
                      top_n: int = 30, persist: bool = False) -> list[dict]:
    """Score every ZT-pool stock for the given trade_date, return top N."""
    mc = judge_market_cycle(trade_date)
    with _get_session() as s:
        zt = s.execute(
            select(ZtPoolDaily).where(
                ZtPoolDaily.trade_date == trade_date,
                ZtPoolDaily.pool_type == "zt",
            )
        ).scalars().all()
        name_map = {c: n for c, n in s.execute(select(Stock.code, Stock.name)).all()}

    candidates = []
    for r in zt:
        pred = predict_dragon_score(r.code, trade_date, market_cycle=mc)
        if pred is None or pred["dragon_score"] < score_threshold:
            continue
        candidates.append({
            **pred,
            "name": name_map.get(r.code, r.name or ""),
            "consecutive": r.consecutive,
            "amount": r.amount,
            "industry": r.industry,
        })
    candidates.sort(key=lambda x: -x["dragon_score"])
    candidates = candidates[:top_n]
    for i, c in enumerate(candidates):
        c["dragon_rank"] = i + 1

    if persist and candidates:
        with _get_session() as s:
            for c in candidates:
                exists = s.execute(
                    select(DragonSignal).where(
                        DragonSignal.trade_date == trade_date,
                        DragonSignal.code == c["code"],
                    )
                ).scalar_one_or_none()
                payload = dict(
                    trade_date=trade_date, code=c["code"], name=c["name"],
                    signal_type="hold", dragon_rank=c["dragon_rank"],
                    dragon_score=c["dragon_score"], concept=c.get("concept", ""),
                    consecutive=c["consecutive"], model_conf=c["dragon_prob"],
                    entry_price=c.get("close"), stop_price=None, target_price=None,
                    market_cycle=mc.phase,
                    reason={"identifier_only": True},
                )
                if exists:
                    for k, v in payload.items():
                        setattr(exists, k, v)
                else:
                    s.add(DragonSignal(**payload))
            s.commit()
    return candidates


def generate_dragon_signal(code: str, trade_date: str) -> DragonSignalResult | None:
    """Full pipeline signal: identifier + timing + market cycle filter + risk levels."""
    mc = judge_market_cycle(trade_date)
    pred = predict_dragon_score(code, trade_date, market_cycle=mc)
    if pred is None:
        return None
    timing = predict_dragon_timing(code, trade_date)

    # Resolve action with cycle filter
    base_action = (timing or {}).get("action", "hold")
    confidence = (timing or {}).get("confidence", 0.0)
    reasons: list[str] = []

    if mc.phase == "ice":
        action = "hold" if base_action != "sell" else "sell"
        reasons.append(f"市场冰点期 (sentiment={mc.score})，禁止开仓")
    elif mc.phase == "cooldown":
        action = "sell" if base_action != "buy" else "hold"
        reasons.append(f"市场退潮期 (blast_rate={mc.blast_rate:.1%})，倾向减仓")
    else:
        action = base_action

    if pred["dragon_score"] < 50 and action == "buy":
        action = "hold"
        reasons.append(f"龙头分仅{pred['dragon_score']}, 低于买入阈值50")

    # Reason tracing
    reasons.append(f"龙头评分: {pred['dragon_score']}")
    reasons.append(f"连板数: {pred['consecutive']}")
    if pred.get("concept"):
        reasons.append(f"主题: {pred['concept']} (热度{pred['sector_heat']:.0f})")
    if pred["hot_money_seats"] > 0:
        reasons.append(f"游资席位: {pred['hot_money_seats']} 个")
    if timing:
        reasons.append(
            f"时机模型: {base_action} (buy={timing['buy_prob']:.2f} sell={timing['sell_prob']:.2f})"
        )
    reasons.append(f"市场周期: {mc.phase} (sentiment={mc.score})")

    # Risk levels (heuristic based on close price)
    close = pred.get("close") or 0.0
    entry = close
    stop = round(close * 0.95, 2) if close else None
    target = round(close * 1.10, 2) if close else None  # +10% short-term target

    name = ""
    with _get_session() as s:
        st = s.get(Stock, code)
        if st:
            name = st.name

    return DragonSignalResult(
        code=code, name=name, trade_date=trade_date,
        signal_type=action,
        dragon_score=pred["dragon_score"],
        dragon_rank=0,
        consecutive=pred["consecutive"],
        concept=pred.get("concept", ""),
        market_cycle=mc.phase,
        model_confidence=confidence,
        entry_price=entry, stop_price=stop, target_price=target,
        reasons=reasons,
        feature_snapshot={
            "dragon_prob": pred["dragon_prob"],
            "sector_heat": pred["sector_heat"],
            "hot_money_seats": pred["hot_money_seats"],
            "market_sentiment": mc.score,
            "market_phase": mc.phase,
        },
    )


# ═══════════════════════════════════════════════════════════════
# Backtest
# ═══════════════════════════════════════════════════════════════

def backtest_dragon(start_date: str, end_date: str,
                     score_threshold: float = 70.0,
                     hold_days: int = 5,
                     stop_pct: float = -5.0,
                     max_positions: int = 3,
                     filter_ice: bool = True,
                     filter_cooldown: bool = True,
                     init_cash: float = 1_000_000.0) -> dict:
    """Walk-forward backtest of the dragon strategy.

    On each trading day:
      1. Judge market cycle, optionally skip ice/cooldown
      2. Identify dragons (top max_positions with score >= threshold)
      3. Buy at next-day open (use today's close as proxy if no open)
      4. Hold for hold_days OR exit on stop loss
    """
    if not (_MODEL_DIR / "dragon_stage1.pkl").exists():
        return {"error": "Stage 1 model not trained yet"}

    with _get_session() as s:
        all_dates = s.execute(
            select(DailyCandle.trade_date).where(
                DailyCandle.trade_date.between(start_date, end_date)
            ).group_by(DailyCandle.trade_date).order_by(DailyCandle.trade_date)
        ).scalars().all()

    if not all_dates:
        return {"error": "no trading dates in range"}

    cash = init_cash
    positions: dict[str, dict] = {}   # code -> {entry_price, entry_date, shares, dragon_score}
    trades: list[dict] = []
    equity_curve: list[dict] = []

    for i, d in enumerate(all_dates):
        # 1) Mark-to-market & exit logic
        mc = judge_market_cycle(d)
        equity = cash
        for code, pos in list(positions.items()):
            with _get_session() as s:
                row = s.execute(
                    select(DailyCandle.close).where(
                        DailyCandle.code == code, DailyCandle.trade_date == d,
                    )
                ).scalar()
            if row:
                pos["last_close"] = row
                equity += row * pos["shares"]
                pnl_pct = (row - pos["entry_price"]) / pos["entry_price"] * 100
                hold_n = i - all_dates.index(pos["entry_date"])
                exit_reason = None
                if pnl_pct <= stop_pct:
                    exit_reason = f"stop_loss ({pnl_pct:.1f}%)"
                elif hold_n >= hold_days:
                    exit_reason = f"time_exit ({hold_n}d)"
                elif mc.phase == "cooldown" and pnl_pct < 3:
                    exit_reason = "market_cooldown"
                if exit_reason:
                    cash += row * pos["shares"]
                    equity = cash + sum(p["last_close"] * p["shares"]
                                         for c, p in positions.items() if c != code and p.get("last_close"))
                    trades.append({
                        "code": code, "entry_date": pos["entry_date"], "exit_date": d,
                        "entry_price": pos["entry_price"], "exit_price": row,
                        "pnl_pct": round(pnl_pct, 2), "reason": exit_reason,
                        "dragon_score": pos["dragon_score"],
                    })
                    del positions[code]

        # 2) Entry logic
        skip = (filter_ice and mc.phase == "ice") or (filter_cooldown and mc.phase == "cooldown")
        if not skip and len(positions) < max_positions:
            candidates = identify_dragons(d, score_threshold=score_threshold,
                                            top_n=max_positions * 3)
            slots = max_positions - len(positions)
            for c in candidates:
                if c["code"] in positions:
                    continue
                if slots <= 0:
                    break
                price = c.get("close")
                if not price or price <= 0:
                    continue
                # Allocate equal cash / slot
                alloc = (cash) / slots
                shares = int(alloc // (price * 100)) * 100  # round to 100 shares
                if shares <= 0:
                    continue
                cost = shares * price
                cash -= cost
                positions[c["code"]] = {
                    "entry_price": price, "entry_date": d, "shares": shares,
                    "dragon_score": c["dragon_score"],
                    "last_close": price,
                }
                slots -= 1

        # 3) Equity snapshot
        equity_now = cash + sum(p.get("last_close", p["entry_price"]) * p["shares"]
                                  for p in positions.values())
        equity_curve.append({"date": d, "equity": round(equity_now, 2)})

    # 4) Force-close remaining positions at end
    last_d = all_dates[-1]
    for code, pos in list(positions.items()):
        last = pos.get("last_close", pos["entry_price"])
        cash += last * pos["shares"]
        trades.append({
            "code": code, "entry_date": pos["entry_date"], "exit_date": last_d,
            "entry_price": pos["entry_price"], "exit_price": last,
            "pnl_pct": round((last - pos["entry_price"]) / pos["entry_price"] * 100, 2),
            "reason": "end_of_period",
            "dragon_score": pos["dragon_score"],
        })

    # Stats
    wins = [t for t in trades if t["pnl_pct"] > 0]
    losses = [t for t in trades if t["pnl_pct"] <= 0]
    total_return = (cash - init_cash) / init_cash * 100
    win_rate = len(wins) / len(trades) if trades else 0
    avg_win = float(np.mean([t["pnl_pct"] for t in wins])) if wins else 0
    avg_loss = float(np.mean([t["pnl_pct"] for t in losses])) if losses else 0
    # Max drawdown
    eq = np.array([e["equity"] for e in equity_curve])
    if len(eq) > 0:
        peak = np.maximum.accumulate(eq)
        dd = (eq - peak) / peak * 100
        max_dd = float(dd.min())
    else:
        max_dd = 0.0

    return {
        "start_date": start_date, "end_date": end_date,
        "init_cash": init_cash, "final_cash": round(cash, 2),
        "total_return_pct": round(total_return, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "trades": len(trades),
        "win_rate": round(win_rate, 4),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "params": {
            "score_threshold": score_threshold, "hold_days": hold_days,
            "stop_pct": stop_pct, "max_positions": max_positions,
            "filter_ice": filter_ice, "filter_cooldown": filter_cooldown,
        },
        "equity_curve": equity_curve,
        "trade_list": trades[-50:],  # last 50 for UI
    }


# ═══════════════════════════════════════════════════════════════
# Status / utilities
# ═══════════════════════════════════════════════════════════════

def dragon_model_status() -> dict:
    s1 = _MODEL_DIR / "dragon_stage1.pkl"
    s2 = _MODEL_DIR / "dragon_stage2.pt"
    return {
        "stage1": {
            "trained": s1.exists(),
            "modified_at": (datetime.fromtimestamp(s1.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                              if s1.exists() else None),
            "size_kb": round(s1.stat().st_size / 1024, 1) if s1.exists() else 0,
        },
        "stage2": {
            "trained": s2.exists(),
            "modified_at": (datetime.fromtimestamp(s2.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                              if s2.exists() else None),
            "size_kb": round(s2.stat().st_size / 1024, 1) if s2.exists() else 0,
        },
    }


def train_dragon_models(start_date: str, end_date: str,
                         epochs: int = 30,
                         train_stage2: bool = True,
                         progress_cb=None) -> dict:
    """Train Stage 1, then Stage 2."""
    out = {"stage1": None, "stage2": None}

    def _s1_cb(p, msg):
        if progress_cb:
            progress_cb(int(p * 0.5), f"[Stage 1] {msg}")

    out["stage1"] = train_dragon_identifier(start_date, end_date, progress_cb=_s1_cb)
    if "error" in out["stage1"]:
        return out
    if not train_stage2:
        return out

    def _s2_cb(p, msg):
        if progress_cb:
            progress_cb(50 + int(p * 0.5), f"[Stage 2] {msg}")

    out["stage2"] = train_dragon_timing(start_date, end_date, epochs=epochs, progress_cb=_s2_cb)
    return out
