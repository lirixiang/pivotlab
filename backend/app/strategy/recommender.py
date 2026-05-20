"""Top-level recommender: scans the universe, scores by style,
builds trade plans, and persists results to the DB.

Usage:
    from app.strategy.recommender import scan_universe
    n = scan_universe(styles=["short_term","swing"], top_n=50)

Performance: bulk-reads candles via one SQL query per scan instead of
5000 individual data_provider calls. A full A-share scan completes in
roughly 30–90 s depending on data volume.
"""
from __future__ import annotations

import logging
import time as _time
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

_CN_TZ = ZoneInfo("Asia/Shanghai")

def _today_cn() -> date:
    return datetime.now(_CN_TZ).date()

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from ..database import DATABASE_URL
from ..utils.markets import main_board_like_patterns
from ..models import (
    AnalystConsensus,
    ConceptBoard,
    DailyCandle,
    FinancialSnapshot,
    Stock,
    StockConcept,
)
from ..schemas import Candle
from ..services.levels_multifactor import detect_levels_multifactor
from . import store
from .features import FeatureSet, extract
from .styles import score as score_style, passes_style_filter
from .trade_plan import build_trade_plan

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
#  Cheap level detection (for the ENTIRE universe, not just top-N).
#  Uses 5-bar local extrema from the last 120 bars.
# ──────────────────────────────────────────────────────────────
def _quick_sr_distance(candles: list[Candle], price: float) -> tuple[float, float]:
    """Return (dist_to_resistance_pct, dist_to_support_pct).  100.0 = none nearby.

    A swing high counts as resistance only if it is the local max within
    ±5 bars AND its price is at least 1.5% above the current price (so we
    don't flag every minor noise wick as resistance).
    """
    if len(candles) < 12 or price <= 0:
        return 100.0, 100.0
    hs = [c.high for c in candles[-120:]]
    ls = [c.low for c in candles[-120:]]
    res_above: list[float] = []
    sup_below: list[float] = []
    n = len(hs)
    win = 5
    for i in range(win, n - win):
        h_i = hs[i]
        if h_i <= price * 1.015:
            pass
        elif all(h_i >= hs[j] for j in range(i - win, i + win + 1)):
            res_above.append(h_i)
        l_i = ls[i]
        if l_i >= price * 0.985:
            pass
        elif all(l_i <= ls[j] for j in range(i - win, i + win + 1)):
            sup_below.append(l_i)
    dist_r = ((min(res_above) - price) / price * 100) if res_above else 100.0
    dist_s = ((price - max(sup_below)) / price * 100) if sup_below else 100.0
    return dist_r, dist_s


def _market_environment(candles_by_code: dict[str, list[Candle]]) -> tuple[float, float]:
    """A-share market environment.

    Prefers REAL index k-lines (sh000001) when available; falls back to
    cross-sectional breadth if index data not synced yet.
    """
    # Try real index first
    try:
        from ..services.index_sync import market_environment_from_index
        trend, atr_pct = market_environment_from_index("sh000001")
        if atr_pct > 0:
            return trend, atr_pct
    except Exception as e:
        logger.debug("index env failed, fall back to breadth: %s", e)

    # Fallback: cross-sectional breadth proxy
    if not candles_by_code:
        return 0.0, 0.0
    pos5 = 0
    total = 0
    atrs: list[float] = []
    for cl in candles_by_code.values():
        if len(cl) < 6:
            continue
        c0 = cl[-6].close
        c1 = cl[-1].close
        if c0 > 0:
            total += 1
            if c1 > c0:
                pos5 += 1
        rng = [max(b.high - b.low, 0.0) for b in cl[-14:]]
        if rng and c1 > 0:
            atrs.append((sum(rng) / len(rng)) / c1)
    if total == 0:
        return 0.0, 0.0
    breadth = pos5 / total
    if breadth >= 0.6:
        trend = min(1.0, (breadth - 0.6) * 5)
    elif breadth <= 0.4:
        trend = max(-1.0, (breadth - 0.4) * 5)
    else:
        trend = 0.0
    atrs.sort()
    median_atr = atrs[len(atrs) // 2] if atrs else 0.0
    return trend, median_atr


def _seq_window_for(candles: list[Candle]) -> 'np.ndarray | None':
    """Build a (60,5) raw OHLCV window for the latest bar (sequence model input).
    Returns None if fewer than 60 bars available."""
    if len(candles) < 60:
        return None
    import numpy as np
    return np.array(
        [[c.open, c.high, c.low, c.close, c.volume] for c in candles[-60:]],
        dtype=np.float64,
    )


def _rl_mult(style: str, candles: list[Candle]) -> float:
    """For ai_ensemble only: ask the trained PPO agent for a position
    multiplier. Returns 1.0 when no model is loaded or for other styles."""
    if style != "ai_ensemble":
        return 1.0
    try:
        from .ml import rl_position
        if not rl_position.is_trained() or len(candles) < 30:
            return 1.0
        import numpy as np
        win = np.array(
            [[c.open, c.high, c.low, c.close, c.volume] for c in candles[-60:]],
            dtype=np.float64,
        )
        return rl_position.suggest_position_mult(win, score=0.0)
    except Exception as e:
        logger.debug("rl mult failed: %s", e)
        return 1.0


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


# ──────────────────────────────────────────────────────────────
#  Bulk data loading
# ──────────────────────────────────────────────────────────────

def _load_universe(session: Session, exclude_st: bool = True) -> list[Stock]:
    stmt = select(Stock)
    if exclude_st:
        stmt = stmt.where(Stock.is_st == False)  # noqa: E712
    # 业务层全局过滤:仅主板(沪 600/601/603/605 + 深 000/001/002/003)
    from sqlalchemy import or_
    stmt = stmt.where(or_(*[Stock.code.like(p) for p in main_board_like_patterns()]))
    return list(session.execute(stmt).scalars().all())


def _load_all_candles(session: Session, codes: list[str], days: int = 250) -> dict[str, list[Candle]]:
    """One bulk SQL pulling last `days` candles for all codes at once."""
    cutoff = (_today_cn() - timedelta(days=int(days * 1.6))).strftime("%Y-%m-%d")
    rows = session.execute(
        select(DailyCandle).where(
            DailyCandle.code.in_(codes),
            DailyCandle.trade_date >= cutoff,
        ).order_by(DailyCandle.code, DailyCandle.trade_date.asc())
    ).scalars().all()

    bucket: dict[str, list[Candle]] = defaultdict(list)
    for r in rows:
        bucket[r.code].append(Candle(
            date=r.trade_date,
            open=r.open or 0.0, high=r.high or 0.0,
            low=r.low or 0.0, close=r.close or 0.0,
            volume=r.volume or 0.0,
        ))
    return bucket


def _load_today_quotes(session: Session, codes: list[str]) -> dict[str, DailyCandle]:
    today = _today_cn().strftime("%Y-%m-%d")
    rows = session.execute(
        select(DailyCandle).where(
            DailyCandle.code.in_(codes),
            DailyCandle.trade_date == today,
        )
    ).scalars().all()
    return {r.code: r for r in rows}


def _load_fundamentals(session: Session, codes: list[str]) -> dict[str, FinancialSnapshot]:
    rows = session.execute(
        select(FinancialSnapshot).where(FinancialSnapshot.code.in_(codes))
    ).scalars().all()
    return {r.code: r for r in rows}


def _load_concept_heat(session: Session, codes: list[str]) -> dict[str, dict]:
    """Return code -> {heat: max change_pct_5d, inflow: max net_inflow, top_concept: name}."""
    sc_rows = session.execute(
        select(StockConcept).where(StockConcept.code.in_(codes))
    ).scalars().all()
    if not sc_rows:
        return {}

    boards = {b.concept: b for b in
              session.execute(select(ConceptBoard)).scalars().all()}

    out: dict[str, dict] = {}
    for sc in sc_rows:
        b = boards.get(sc.concept)
        if not b:
            continue
        cur = out.setdefault(sc.code, {"heat": 0.0, "inflow": 0.0, "top_concept": ""})
        h5 = b.change_pct_5d or 0.0
        if h5 > cur["heat"]:
            cur["heat"] = h5
            cur["top_concept"] = sc.concept
        flow = b.net_inflow or 0.0
        if flow > cur["inflow"]:
            cur["inflow"] = flow
    return out


# ──────────────────────────────────────────────────────────────
#  Build per-stock feature record (with enrichment)
# ──────────────────────────────────────────────────────────────

def _build_features(
    code: str,
    candles: list[Candle],
    today_quote: DailyCandle | None,
    fund: FinancialSnapshot | None,
    heat: dict | None,
    *,
    market_trend: float = 0.0,
    market_atr_pct: float = 0.0,
    today_date: date | None = None,
) -> FeatureSet | None:
    fs = extract(candles, code=code)
    if not fs:
        return None
    if today_quote:
        if today_quote.pe_ratio:
            fs.pe_ratio = float(today_quote.pe_ratio)
        if today_quote.market_cap:
            fs.market_cap_bil = float(today_quote.market_cap) / 1e8
    if fund:
        fs.roe = float(fund.roe or 0.0)
        fs.revenue_yoy = float(fund.revenue_yoy or 0.0)
        fs.net_profit_yoy = float(fund.net_profit_yoy or 0.0)
        if not fs.pe_ratio and fund.pe_ratio_ttm:
            fs.pe_ratio = float(fund.pe_ratio_ttm)
    if heat:
        fs.concept_heat = float(heat.get("heat") or 0.0)
        fs.concept_inflow_bil = float(heat.get("inflow") or 0.0) / 1e8

    # Cheap SR distances (full universe)
    dist_r, dist_s = _quick_sr_distance(candles, fs.price)
    fs.dist_to_resistance_pct = dist_r
    fs.dist_to_support_pct = dist_s

    # Market env
    fs.market_trend = market_trend
    fs.market_atr_pct = market_atr_pct

    # Calendar context
    today = today_date or _today_cn()
    fs.is_friday = int(today.weekday() == 4)
    # naive: 距下一个公历周末 = 5 - weekday (周一=0)
    fs.days_to_holiday = max(0, 5 - today.weekday())

    # Data freshness: how stale is the last bar?
    if candles:
        last_d = candles[-1].date
        try:
            last_dt = datetime.strptime(last_d, "%Y-%m-%d").date() if isinstance(last_d, str) else last_d
            fs.data_stale_days = max(0, (today - last_dt).days)
        except Exception:
            fs.data_stale_days = 0
    return fs


# ──────────────────────────────────────────────────────────────
#  Main scan entry point
# ──────────────────────────────────────────────────────────────

def scan_universe(
    styles: list[str] | None = None,
    *,
    top_n: int = 100,
    min_score: float = 50.0,
    exclude_st: bool = True,
    universe_limit: int | None = None,
    progress_cb=None,
) -> dict[str, int]:
    """Scan the full A-share universe and persist top recommendations per style.

    Returns: {style: count_persisted}
    """
    styles = styles or ["short_term", "swing", "value", "multi_factor"]
    t0 = _time.time()
    counts: dict[str, int] = {s: 0 for s in styles}

    eng = _get_engine()
    with Session(eng) as session:
        stocks = _load_universe(session, exclude_st=exclude_st)
        if universe_limit:
            stocks = stocks[:universe_limit]
        if not stocks:
            logger.warning("scan_universe: empty universe")
            return counts
        codes = [s.code for s in stocks]
        name_by_code = {s.code: s.name for s in stocks}
        industry_by_code = {s.code: s.industry or "" for s in stocks}
        logger.info("scan_universe: loaded %d stocks", len(codes))

        if progress_cb:
            progress_cb({"phase": "loading_data", "pct": 5})

        candles_by_code = _load_all_candles(session, codes, days=250)
        today_q = _load_today_quotes(session, codes)
        funds = _load_fundamentals(session, codes)
        heat = _load_concept_heat(session, codes)
        logger.info(
            "scan_universe: data loaded — candles=%d quotes=%d funds=%d heat=%d (%.1fs)",
            len(candles_by_code), len(today_q), len(funds), len(heat), _time.time() - t0,
        )

        if progress_cb:
            progress_cb({"phase": "scoring", "pct": 25})

        # Cross-sectional market environment from breadth.
        market_trend, market_atr_pct = _market_environment(candles_by_code)
        logger.info("scan_universe: market_trend=%.2f median_atr=%.3f", market_trend, market_atr_pct)
        today_d = _today_cn()

        # Per-style accumulators of (score, code, fs, reasons, factors)
        candidates: dict[str, list[tuple[float, str, FeatureSet, list[str], dict]]] = {
            s: [] for s in styles
        }

        n_processed = 0
        n_filtered = 0
        for code in codes:
            cl = candles_by_code.get(code)
            if not cl or len(cl) < 60:
                continue
            fs = _build_features(
                code,
                cl,
                today_q.get(code),
                funds.get(code),
                heat.get(code),
                market_trend=market_trend,
                market_atr_pct=market_atr_pct,
                today_date=today_d,
            )
            if not fs:
                continue
            seq_win = None
            for s in styles:
                ok, _why = passes_style_filter(s, fs)
                if not ok:
                    n_filtered += 1
                    continue
                if s == "ai_ensemble":
                    if seq_win is None:
                        seq_win = _seq_window_for(cl)
                    from .ml.ensemble import score_ai_ensemble
                    sc, reasons, factors = score_ai_ensemble(fs, seq_window=seq_win)
                    # AI ensemble path doesn't go through styles.score(), so apply
                    # quality gate manually:
                    from .styles import _apply_quality_gate
                    sc, reasons, factors = _apply_quality_gate(s, sc, reasons, factors, fs)
                else:
                    sc, reasons, factors = score_style(s, fs)
                # AI ensemble 评分分布极窄(几乎所有标的 50~57),用更严的门槛区分信号
                effective_min = 52.0 if s == "ai_ensemble" else min_score
                if sc >= effective_min:
                    candidates[s].append((sc, code, fs, reasons, factors))
            n_processed += 1
            if progress_cb and n_processed % 500 == 0:
                pct = 25 + int(50 * n_processed / len(codes))
                progress_cb({"phase": "scoring", "pct": min(pct, 75),
                             "processed": n_processed, "total": len(codes)})

        logger.info(
            "scan_universe: scoring done in %.1fs (processed=%d filtered=%d)",
            _time.time() - t0, n_processed, n_filtered,
        )
        for s in styles:
            logger.info("scan_universe: candidates style=%s n=%d", s, len(candidates[s]))

        if progress_cb:
            progress_cb({"phase": "building_plans", "pct": 80})

        # ── Build plans for top-N per style and persist ──
        scan_date = _today_cn().strftime("%Y-%m-%d")
        MAX_PER_INDUSTRY = 3       # only enforced within the core tier (rank ≤ CORE_SIZE)
        CORE_SIZE = 20             # industry diversity gate stops applying after this rank
        for s in styles:
            cands = sorted(candidates[s], key=lambda x: -x[0])
            # ── Score rescaling ──
            # AI ensemble 的原始分布极窄(子模型 sigmoid 输出回归均值);
            # 其它规则风格分布通常也偏窄。对所有风格统一做「截尾 + 百分位 + min-max」
            # 拉伸到 5~95,保留排序的同时让用户感知到差异。
            if len(cands) >= 5:
                raws = [c[0] for c in cands]
                n = len(raws)
                # 1) 截尾(去掉极端值,避免单个 outlier 把其余压扁)
                sorted_raw = sorted(raws)
                lo = sorted_raw[max(0, int(n * 0.02))]
                hi = sorted_raw[min(n - 1, int(n * 0.98))]
                if hi - lo < 1e-6:
                    hi = lo + 1.0
                # 2) 百分位 + min-max 混合:60% 百分位 + 40% 线性,
                #    既保证均匀分布,又保留原始分数的相对距离
                rank_by_raw = {r: i for i, r in enumerate(sorted_raw)}
                rescaled: list[tuple[float, str, FeatureSet, list[str], dict]] = []
                for raw, code, fs, reasons, factors in cands:
                    pct = rank_by_raw[raw] / max(1, n - 1)          # 0..1
                    lin = (max(min(raw, hi), lo) - lo) / (hi - lo)  # 0..1
                    mixed = 0.6 * pct + 0.4 * lin
                    new_score = round(5.0 + 90.0 * mixed, 2)        # 5..95
                    rescaled.append((new_score, code, fs, reasons, factors))
                # 按重缩后的分数重新排序(基本与原顺序一致)
                cands = sorted(rescaled, key=lambda x: -x[0])
                logger.info(
                    "scan_universe: rescale style=%s raw[min=%.2f,max=%.2f] → new[min=%.2f,max=%.2f]",
                    s, min(raws), max(raws), cands[-1][0], cands[0][0],
                )
            # AI ensemble 只保留前 50(核心 20 + 备选 30),不进入观察池
            effective_top_n = 50 if s == "ai_ensemble" else top_n
            items = []
            industry_cnt: dict[str, int] = defaultdict(int)
            kept = 0
            n_dropped_rr = 0
            n_dropped_industry = 0
            for sc, code, fs, reasons, factors in cands:
                if kept >= effective_top_n:
                    break
                ind = industry_by_code.get(code, "") or "unknown"
                # industry cap only for top CORE_SIZE picks (keeps core diversified, allows watch/observe to fill)
                if kept < CORE_SIZE and industry_cnt[ind] >= MAX_PER_INDUSTRY:
                    n_dropped_industry += 1
                    continue
                cl = candles_by_code[code]
                try:
                    levels = detect_levels_multifactor(cl)
                except Exception as e:
                    logger.debug("levels failed for %s: %s", code, e)
                    levels = []

                plan = build_trade_plan(
                    style=s, candles=cl, levels=levels, fs=fs,
                    score=sc, reasons=reasons,
                    position_mult=_rl_mult(s, cl),
                )
                # Drop rejects (rr too low, etc.) — they shouldn't go to the user
                if plan.state == "reject":
                    n_dropped_rr += 1
                    continue
                kept += 1
                industry_cnt[ind] += 1
                top_concept = (heat.get(code) or {}).get("top_concept", "")
                items.append({
                    "code": code,
                    "name": name_by_code.get(code, ""),
                    "score": round(sc, 2),
                    "rank": kept,
                    "price": fs.price,
                    "industry": ind,
                    "concept": top_concept,
                    "reasons": reasons,
                    "factors": factors,
                    "expires_date": (_today_cn() + timedelta(
                        days=plan.holding_days_max
                    )).strftime("%Y-%m-%d"),
                    "plan": plan,
                })

            n = store.save_batch(session, style=s, scan_date=scan_date, items=items)
            counts[s] = n
            logger.info(
                "scan_universe: style=%s persisted=%d (dropped rr=%d industry=%d)",
                s, n, n_dropped_rr, n_dropped_industry,
            )

        if progress_cb:
            progress_cb({"phase": "done", "pct": 100, "counts": counts})

    logger.info("scan_universe: total time %.1fs counts=%s", _time.time() - t0, counts)
    return counts


# ──────────────────────────────────────────────────────────────
#  Single-stock rebuild (for the detail page)
# ──────────────────────────────────────────────────────────────

def rebuild_for_code(code: str, styles: list[str] | None = None) -> list[dict]:
    """Build (but do not persist) recommendations + plans for a single code,
    for ALL requested styles, regardless of score. Used by the per-stock
    explainer endpoint."""
    from ..services.data_provider import get_candles  # local import to avoid cycles
    styles = styles or ["short_term", "swing", "value", "multi_factor"]
    candles = get_candles(code, days=250)
    if not candles or len(candles) < 60:
        return []

    eng = _get_engine()
    with Session(eng) as session:
        stk = session.get(Stock, code)
        name = stk.name if stk else ""
        industry = (stk.industry if stk else "") or ""
        funds = _load_fundamentals(session, [code]).get(code)
        today_q = _load_today_quotes(session, [code]).get(code)
        heat = _load_concept_heat(session, [code]).get(code) or {}

        fs = _build_features(code, candles, today_q, funds, heat)
        if not fs:
            return []

        try:
            levels = detect_levels_multifactor(candles)
        except Exception:
            levels = []

        out = []
        for s in styles:
            if s == "ai_ensemble":
                from .ml.ensemble import score_ai_ensemble
                seq_win = _seq_window_for(candles)
                sc, reasons, factors = score_ai_ensemble(fs, seq_window=seq_win)
            else:
                sc, reasons, factors = score_style(s, fs)
            plan = build_trade_plan(
                style=s, candles=candles, levels=levels, fs=fs,
                score=sc, reasons=reasons,
                position_mult=_rl_mult(s, candles),
            )
            out.append({
                "code": code, "name": name, "industry": industry,
                "style": s, "score": round(sc, 2),
                "price": fs.price,
                "concept": heat.get("top_concept", ""),
                "reasons": reasons, "factors": factors,
                "plan": plan.to_dict(),
            })
        return out
