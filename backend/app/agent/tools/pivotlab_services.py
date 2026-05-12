"""Tools that call pivotlab service functions directly.

Since the agent lives inside the pivotlab backend, we import services
directly — screener, signal generator, backtester, dragon strategy, etc.
No HTTP calls, no API dependency.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any

from app.agent.tools.registry import registry

logger = logging.getLogger(__name__)

_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    ".screener_cache",
)


# ── Helper: auto-sync candles if insufficient ────────────────────────────

async def _ensure_candles(code: str, min_bars: int = 60, days: int = 250):
    """Get candles; if insufficient, retry with longer lookback.

    get_candles already has built-in network fetch (Tencent + EastMoney).
    If still insufficient after extended lookback, there's genuinely no data.
    """
    from app.services.data_provider import get_candles

    candles = await asyncio.to_thread(get_candles, code, "daily", days)
    if len(candles) >= min_bars:
        return candles

    # Retry with extended lookback (maybe stock is new or data gap)
    if days < 500:
        logger.info("_ensure_candles: %s has %d bars (need %d), retrying with 500 days", code, len(candles), min_bars)
        candles = await asyncio.to_thread(get_candles, code, "daily", 500)

    return candles


# ── Pattern screener ────────────────────────────────────────────────────

@registry.register(
    name="pl_screener",
    description=(
        "Run pattern screener on the full A-share universe. Patterns: "
        "'breakout_pullback' (突破回踩), 'stabilize' (下跌企稳), "
        "'box_support' (箱体支撑), 'volume_breakout' (放量突破), "
        "'macd_divergence' (MACD底背离), "
        "'stage2_breakout' (Stage 2 趋势突破—赵败似老 Weinstein 机制), "
        "'vcp' (Mark Minervini 波动收缩高 RR 突破), "
        "'pivot_breakout' (William O'Neil base 点突破), "
        "'cup_handle' (杯柄形态), 'high_tight_flag' (高位紧旗—强爆发高 RR低胜率). "
        "Returns top matches with score, price, support/resistance, triggers. "
        "Uses cached results if available (< 24h old); otherwise triggers a fresh scan."
    ),
    parameters={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "enum": ["breakout_pullback", "stabilize", "box_support",
                         "volume_breakout", "macd_divergence",
                         "stage2_breakout", "vcp", "pivot_breakout",
                         "cup_handle", "high_tight_flag"],
                "description": "Pattern to scan for",
            },
            "min_score": {"type": "number", "default": 60, "description": "Minimum score threshold"},
            "limit": {"type": "integer", "default": 20},
            "force_rescan": {"type": "boolean", "default": False, "description": "Force fresh scan even if cache exists"},
        },
        "required": ["pattern"],
    },
    permission="safe",
)
async def pl_screener(args: dict[str, Any]) -> Any:
    from app.services.screener import PATTERN_DETECTORS

    pattern = args["pattern"]
    min_score = float(args.get("min_score", 60))
    limit = int(args.get("limit", 20))
    force_rescan = bool(args.get("force_rescan", False))

    if pattern not in PATTERN_DETECTORS:
        return {"error": f"unknown pattern '{pattern}', use one of: {list(PATTERN_DETECTORS)}"}

    # Try cached results first (cache valid for 48h — screener runs daily at 15:30)
    if not force_rescan:
        cached = await asyncio.to_thread(_read_cache, pattern, min_score, limit)
        if cached is not None:
            return cached

    # No cache or forced rescan — trigger scan via subprocess
    from app.services.sync_worker import spawn_sync
    started = await asyncio.to_thread(spawn_sync, "screener", pattern=pattern)
    if not started:
        logger.info("screener %s already running, waiting for completion", pattern)

    # Record cache file mtime before scan so we can detect new results
    cache_path = os.path.join(_CACHE_DIR, f"{pattern}.json")
    mtime_before = os.path.getmtime(cache_path) if os.path.exists(cache_path) else 0

    # Poll for completion (scan typically takes 3-8 minutes for ~3000 stocks)
    for i in range(120):  # up to 10 minutes
        await asyncio.sleep(5)
        # Check if cache file was updated
        if os.path.exists(cache_path):
            mtime_now = os.path.getmtime(cache_path)
            if mtime_now > mtime_before:
                cached = await asyncio.to_thread(_read_cache, pattern, min_score, limit, max_age_hours=1)
                if cached is not None:
                    return cached

    # Timeout — try to return stale cache with warning instead of empty error
    stale = await asyncio.to_thread(_read_cache, pattern, min_score, limit, max_age_hours=999)
    if stale is not None:
        stale["_warning"] = f"扫描超时，以下为旧缓存数据（{stale.get('scanned_at', '未知')}），仅供参考"
        return stale

    return {"status": "timeout", "message": f"{pattern} scan is still running after 10min and no cached data available. Use query_db on scan_results table to check later."}


def _read_cache(pattern: str, min_score: float, limit: int, max_age_hours: float = 48) -> dict | None:
    """Read screener cache JSON. Returns None if missing or too old."""
    path = os.path.join(_CACHE_DIR, f"{pattern}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            data = json.load(f)
        scanned_at = data.get("scanned_at")
        if scanned_at:
            ts = datetime.fromisoformat(scanned_at)
            if datetime.now() - ts > timedelta(hours=max_age_hours):
                return None
        items = [it for it in data.get("items", []) if it.get("score", 0) >= min_score]
        items.sort(key=lambda x: x.get("score", 0), reverse=True)
        return {
            "pattern": pattern,
            "scanned_at": scanned_at,
            "total_matches": data.get("total", len(items)),
            "scanned_stocks": data.get("scanned", 0),
            "results": [
                {
                    "code": it.get("code"),
                    "name": it.get("name"),
                    "score": round(it.get("score", 0), 1),
                    "price": round(it.get("price", 0), 2),
                    "change_pct": round(it.get("change_pct", 0), 2),
                    "volume_ratio": round(it.get("volume_ratio", 0), 2),
                    "rr_ratio": round(it.get("rr_ratio") or 0, 2) if it.get("rr_ratio") else None,
                    "support_score": round(it.get("support_score") or 0, 1) if it.get("support_score") else None,
                    "distance_to_support_pct": round(it["distance_to_support_pct"], 2)
                        if it.get("distance_to_support_pct") is not None else None,
                    "triggers": (it.get("triggers") or [])[:3],
                    "industry": it.get("industry", ""),
                    "concept": it.get("concept", ""),
                }
                for it in items[:limit]
            ],
        }
    except Exception as e:
        logger.warning("screener cache read error: %s", e)
        return None


# ── Signal generator ────────────────────────────────────────────────────

@registry.register(
    name="generate_signal",
    description=(
        "Generate a live trading signal for a stock: action (buy/wait/near_signal), "
        "entry price, stop-loss, target prices, confidence (0-100), position sizing. "
        "Uses the multi-factor S/R engine + backtester stats. "
        "Strategies: 'breakout_pullback', 'bottom_stabilize'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "6-digit stock code"},
            "strategy": {
                "type": "string", "default": "breakout_pullback",
                "description": "Strategy: breakout_pullback or bottom_stabilize",
            },
        },
        "required": ["code"],
    },
    permission="safe",
)
async def generate_signal_tool(args: dict[str, Any]) -> Any:
    from app.services.signal_generator import generate_signal

    code = str(args["code"]).zfill(6)
    strategy = args.get("strategy", "breakout_pullback")

    candles = await _ensure_candles(code, min_bars=60, days=250)
    if len(candles) < 60:
        return {"error": f"insufficient data for {code} ({len(candles)} bars), auto-sync failed. Try sync_daily_candles manually."}

    def _gen():
        return generate_signal(candles, strategy=strategy)

    return await asyncio.to_thread(_gen)


# ── Backtest ────────────────────────────────────────────────────────────

@registry.register(
    name="run_backtest",
    description=(
        "Run a historical backtest for a stock with a given strategy. "
        "Returns: trade_count, win_rate, avg_return, max_drawdown, profit_factor, "
        "sharpe_ratio, and individual trade list. "
        "Strategies: 'breakout_pullback', 'bottom_stabilize'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "6-digit stock code"},
            "strategy": {"type": "string", "default": "breakout_pullback"},
            "days": {"type": "integer", "default": 500, "description": "Lookback days for backtest"},
        },
        "required": ["code"],
    },
    permission="safe",
)
async def run_backtest_tool(args: dict[str, Any]) -> Any:
    from app.services.backtester import run_backtest

    code = str(args["code"]).zfill(6)
    strategy = args.get("strategy", "breakout_pullback")
    days = int(args.get("days", 500))

    candles = await _ensure_candles(code, min_bars=120, days=days)
    if len(candles) < 120:
        return {"error": f"insufficient data for {code} ({len(candles)} bars), auto-sync failed. Try sync_daily_candles manually."}

    def _bt():
        result = run_backtest(candles, strategy=strategy, period="daily")
        s = result.stats or {}
        return {
            "code": code,
            "strategy": strategy,
            "bars": len(candles),
            "trade_count": s.get("total_trades", 0),
            "win_rate_pct": round(float(s.get("win_rate", 0)) * 100, 1),
            "avg_win_pct": s.get("avg_win"),
            "avg_loss_pct": s.get("avg_loss"),
            "profit_factor": s.get("profit_factor"),
            "max_drawdown_pct": s.get("max_drawdown"),
            "sharpe": s.get("sharpe"),
            "total_return_pct": s.get("total_return"),
            "trades": [
                {
                    "entry_date": t.get("entry_date"),
                    "exit_date": t.get("exit_date"),
                    "entry_price": t.get("entry_price"),
                    "exit_price": t.get("exit_price"),
                    "pnl_pct": t.get("pnl_pct"),
                    "reason_exit": t.get("reason_exit"),
                    "holding_bars": t.get("holding_bars"),
                }
                for t in (result.trades or [])[-10:]  # last 10 trades
            ],
        }

    return await asyncio.to_thread(_bt)


# ── AI predict (LightGBM) ──────────────────────────────────────────────

@registry.register(
    name="predict_signal",
    description=(
        "Use the trained LightGBM model to predict buy/sell/hold probabilities "
        "for a stock's latest bar. Returns class probabilities and predicted action. "
        "Requires a trained model (train via strategy page first)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "6-digit stock code"},
            "model_key": {"type": "string", "default": "default", "description": "Model key"},
        },
        "required": ["code"],
    },
    permission="safe",
)
async def predict_signal_tool(args: dict[str, Any]) -> Any:
    from app.services.ai_strategy import predict_lightgbm

    code = str(args["code"]).zfill(6)
    model_key = args.get("model_key", "default")

    candles = await _ensure_candles(code, min_bars=60, days=250)
    if len(candles) < 60:
        return {"error": f"insufficient data for {code} ({len(candles)} bars), auto-sync failed."}

    def _predict():
        result = predict_lightgbm(candles, model_key=model_key)
        if result is None:
            return {"error": f"no trained model found for key '{model_key}'"}
        return {"code": code, **result}

    return await asyncio.to_thread(_predict)


# ── Recommendation lifecycle stats ──────────────────────────────────────

@registry.register(
    name="get_recommendation_stats",
    description=(
        "Get aggregate outcome statistics for AI recommendations: "
        "win rate, avg return, best/worst, by style. "
        "Useful for evaluating recommendation quality."
    ),
    parameters={
        "type": "object",
        "properties": {
            "style": {"type": "string", "description": "Filter by style (short_term/swing/value/multi_factor) or omit for all"},
            "days": {"type": "integer", "default": 90, "description": "Lookback period in days"},
        },
    },
    permission="safe",
)
async def get_recommendation_stats(args: dict[str, Any]) -> Any:
    from app.services.lifecycle import aggregate_outcomes

    style = args.get("style")
    days = int(args.get("days", 90))

    return await asyncio.to_thread(aggregate_outcomes, style=style, days=days)


# ── Composite signal verification ─────────────────────────────────────

@registry.register(
    name="verify_signal",
    description=(
        "**核心决策工具**。综合验证一只股票当前能否进场，输出可执行的交易建议。"
        "依次执行: (1) 拉取实时价格 vs screener/历史价格的偏离度 "
        "(2) 重算最新支撑/压力位 (3) 跑近 500 天回测看胜率/盈亏比 "
        "(4) 查负面信号: ST/连续下跌/异常放量出货/近期龙虎榜砸盘 "
        "(5) 综合给出 should_buy(true/false/wait), 进场区间, 止损, 目标, 仓位, "
        "confidence(0-100) 和 reasons/risks 列表。"
        "**任何'要不要买这只票'的问题都应该用此工具，不要直接基于 screener 结果给买入建议。**"
    ),
    parameters={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "6-digit stock code"},
            "ref_price": {
                "type": "number",
                "description": "可选: screener 给出的参考价，用于计算价格偏离度",
            },
            "strategy": {
                "type": "string",
                "default": "breakout_pullback",
                "description": "策略类型: breakout_pullback 或 bottom_stabilize",
            },
        },
        "required": ["code"],
    },
    permission="safe",
)
async def verify_signal(args: dict[str, Any]) -> dict[str, Any]:
    from app.services.tencent_provider import fetch_quotes
    from app.services.levels_multifactor import detect_levels_multifactor
    from app.services.signal_generator import generate_signal
    from app.services.backtester import run_backtest
    from sqlalchemy import text as _text

    code = str(args["code"]).zfill(6)
    ref_price = args.get("ref_price")
    strategy = args.get("strategy", "breakout_pullback")

    risks: list[str] = []
    reasons: list[str] = []
    warnings: list[str] = []

    # ── (1) Realtime quote ──
    quotes = await asyncio.to_thread(fetch_quotes, [code])
    if not quotes:
        # Fallback: use latest close from daily_candles
        from app.database import AsyncSessionLocal
        from sqlalchemy import text as _sql_text
        async with AsyncSessionLocal() as db_sess:
            row = (await db_sess.execute(
                _sql_text("SELECT close, trade_date FROM daily_candles WHERE code = :c ORDER BY trade_date DESC LIMIT 1"),
                {"c": code},
            )).first()
        if not row or not row[0]:
            return {"error": f"无法获取 {code} 实时报价且数据库无历史数据，可能停牌或代码错误"}
        last_price = float(row[0])
        name = ""
        change_pct = 0.0
        warnings.append(f"⚠️ 实时行情接口不可用，使用 {row[1]} 收盘价 {last_price} 代替")
    else:
        rt = quotes[0]
        name = rt.get("name", "")
        last_price = float(rt.get("price") or 0)
        change_pct = float(rt.get("change_pct") or 0)
        if not last_price:
            return {"error": f"{code} 实时价为 0，可能停牌"}

    # 价格偏离度检查
    deviation_pct = None
    if ref_price:
        ref_price = float(ref_price)
        deviation_pct = round((last_price - ref_price) / ref_price * 100, 2)
        if abs(deviation_pct) > 3:
            risks.append(f"⚠️ 实时价 {last_price} 偏离参考价 {ref_price} 已达 {deviation_pct}%，信号可能已失效")
        else:
            reasons.append(f"实时价 {last_price} 与参考价偏离仅 {deviation_pct}%")

    # ── (2) Pull candles + recompute S/R ──
    def _compute_sr_and_signal(candles_in):
        levels = detect_levels_multifactor(candles_in, lookback=120)
        sig = generate_signal(candles_in, strategy=strategy)
        return levels, sig, candles_in[-1].date

    candles = await _ensure_candles(code, min_bars=60, days=300)
    if not candles or len(candles) < 60:
        return {"error": f"{code} 历史数据不足，自动同步也失败了"}

    levels, signal, last_bar_date = await asyncio.to_thread(_compute_sr_and_signal, candles)

    # 数据新鲜度检查
    from datetime import date as _date
    today_str = _date.today().strftime("%Y-%m-%d")
    if last_bar_date and last_bar_date[:10] < today_str:
        days_old = (_date.today() - _date.fromisoformat(last_bar_date[:10])).days
        if days_old > 3:
            warnings.append(f"⚠️ 最新K线为 {last_bar_date[:10]} ({days_old} 天前)，建议先 sync_daily_candles")

    nearest_support = None
    nearest_resistance = None
    for lv in levels:
        if lv.kind == "support" and lv.price < last_price:
            if nearest_support is None or lv.price > nearest_support["price"]:
                nearest_support = {"price": round(lv.price, 2), "score": round(lv.score, 1), "touches": lv.touches}
        elif lv.kind == "resistance" and lv.price > last_price:
            if nearest_resistance is None or lv.price < nearest_resistance["price"]:
                nearest_resistance = {"price": round(lv.price, 2), "score": round(lv.score, 1), "touches": lv.touches}

    if nearest_support:
        dist_to_sup = (last_price - nearest_support["price"]) / last_price * 100
        if dist_to_sup < 1:
            reasons.append(f"距强支撑 {nearest_support['price']} 仅 {dist_to_sup:.2f}%，回撤空间小")
        elif dist_to_sup > 5:
            warnings.append(f"距最近支撑 {nearest_support['price']} 还有 {dist_to_sup:.1f}%，下方有空头空间")

    # ── (3) Backtest ──
    def _bt():
        try:
            r = run_backtest(candles, strategy=strategy, period="daily")
            s = r.stats or {}
            return {
                "trade_count": s.get("total_trades", 0),
                "win_rate_pct": round(float(s.get("win_rate", 0)) * 100, 1),
                "avg_win_pct": s.get("avg_win"),
                "avg_loss_pct": s.get("avg_loss"),
                "profit_factor": s.get("profit_factor", 0),
                "max_drawdown_pct": s.get("max_drawdown"),
                "total_return_pct": s.get("total_return"),
                "sharpe": s.get("sharpe"),
            }
        except Exception as e:
            return {"error": str(e)}

    bt_stats = await asyncio.to_thread(_bt)
    if "error" not in bt_stats:
        wr = bt_stats["win_rate_pct"]
        if bt_stats["trade_count"] < 5:
            warnings.append(f"该策略历史触发仅 {bt_stats['trade_count']} 次，样本不足")
        elif wr >= 55:
            reasons.append(f"近期回测胜率 {wr}%, 盈亏比 {bt_stats['profit_factor']}")
        elif wr < 40:
            risks.append(f"⚠️ 近期回测胜率仅 {wr}%, 历史表现差")

    # ── (4) Negative signal scan via DB ──
    def _check_negative():
        flags: list[str] = []
        positives: list[str] = []
        try:
            from sqlalchemy import create_engine
            from app.database import DATABASE_URL
            sync_url = str(DATABASE_URL).replace("postgresql+asyncpg", "postgresql+psycopg2")
            eng = create_engine(sync_url, pool_pre_ping=True)
            with eng.connect() as c:
                # ST check
                r = c.execute(_text("SELECT is_st, name FROM stocks WHERE code = :c"), {"c": code}).first()
                if r and r[0]:
                    flags.append("⚠️ ST 股票，T+1 涨跌幅 5%")

                # Recent down trend (last 10 bars)
                r = c.execute(_text(
                    "SELECT close, change_pct, volume, amount FROM daily_candles "
                    "WHERE code = :c ORDER BY trade_date DESC LIMIT 10"
                ), {"c": code}).fetchall()
                if r and len(r) >= 5:
                    recent_changes = [row[1] for row in r if row[1] is not None]
                    down_count = sum(1 for x in recent_changes if x < 0)
                    if down_count >= 7:
                        flags.append(f"⚠️ 近 10 日 {down_count} 天下跌，趋势偏空")
                    # 异常放量+下跌(出货)
                    avg_amt = sum(row[3] or 0 for row in r) / len(r)
                    latest = r[0]
                    if latest[3] and avg_amt and latest[3] > avg_amt * 2 and latest[1] < -3:
                        flags.append("⚠️ 最新 K 线放量大跌，疑似出货")

                # 最近 5 日是否上过龙虎榜 (砸盘)
                r = c.execute(_text(
                    "SELECT trade_date, net_amount, reason FROM lhb_records "
                    "WHERE code = :c AND trade_date >= to_char(CURRENT_DATE - INTERVAL '7 days', 'YYYYMMDD') "
                    "ORDER BY trade_date DESC LIMIT 3"
                ), {"c": code}).fetchall()
                for row in r or []:
                    net = float(row[1] or 0)
                    if net < -1e7:  # 净卖出 > 1000 万
                        flags.append(f"⚠️ {row[0]} 龙虎榜净卖出 {net/1e8:.2f} 亿: {row[2]}")
                    elif net > 1e7:
                        positives.append(f"✓ {row[0]} 龙虎榜净买入 {net/1e8:.2f} 亿")

                # 最近是否涨停
                r = c.execute(_text(
                    "SELECT trade_date, consecutive, pool_type FROM zt_pool_daily "
                    "WHERE code = :c AND trade_date >= to_char(CURRENT_DATE - INTERVAL '5 days', 'YYYYMMDD') "
                    "ORDER BY trade_date DESC LIMIT 3"
                ), {"c": code}).fetchall()
                for row in r or []:
                    if row[2] == "zt":
                        positives.append(f"✓ {row[0]} 涨停 ({row[1]} 连板)")
                    elif row[2] == "dt":
                        flags.append(f"⚠️ {row[0]} 跌停")

                # 财务负面
                r = c.execute(_text(
                    "SELECT roe, net_profit_yoy, revenue_yoy FROM financial_snapshots WHERE code = :c"
                ), {"c": code}).first()
                if r:
                    roe, np_yoy, rev_yoy = r
                    if np_yoy is not None and float(np_yoy) < -30:
                        flags.append(f"⚠️ 净利润同比 {float(np_yoy):.1f}%，业绩大幅下滑")
                    elif np_yoy is not None and float(np_yoy) > 30:
                        positives.append(f"✓ 净利润同比 +{float(np_yoy):.1f}%")
        except Exception as e:
            logger.warning("verify_signal negative check failed: %s", e)
        return flags, positives

    neg_flags, pos_flags = await asyncio.to_thread(_check_negative)
    risks.extend(neg_flags)
    reasons.extend(pos_flags)

    # ── (5) Final decision ──
    confidence = 50
    if signal and isinstance(signal, dict):
        action = signal.get("action") or "wait"
        confidence = int(signal.get("confidence") or 50)
    else:
        action = "wait"

    # 综合调整置信度
    confidence -= len(risks) * 12
    confidence += min(len(reasons) * 5, 20)
    confidence = max(0, min(100, confidence))

    # 决策
    if confidence < 35 or len([r for r in risks if "⚠️" in r]) >= 2:
        should_buy = "no"
        decision_note = "风险因素过多，不建议进场"
    elif confidence < 55 or action != "buy":
        should_buy = "wait"
        decision_note = "信号不够明确，等待更好的进场点"
    else:
        should_buy = "yes"
        decision_note = f"满足 {strategy} 进场条件，可考虑分批建仓"

    # 进场区间 / 止损 / 目标
    entry_low = entry_high = stop_loss = target_1 = target_2 = None
    position_pct = 0
    if signal and isinstance(signal, dict) and not signal.get("error"):
        entry_low = signal.get("entry_low") or signal.get("entry_price")
        entry_high = signal.get("entry_high") or signal.get("entry_price")
        stop_loss = signal.get("stop_loss")
        target_1 = signal.get("target_1") or signal.get("target_price")
        target_2 = signal.get("target_2")
        position_pct = signal.get("position_pct") or 0

    # 风险收益比
    rr = None
    if entry_high and stop_loss and target_1:
        try:
            risk_amt = float(entry_high) - float(stop_loss)
            reward_amt = float(target_1) - float(entry_high)
            if risk_amt > 0:
                rr = round(reward_amt / risk_amt, 2)
                if rr < 1.5:
                    warnings.append(f"⚠️ 盈亏比仅 {rr}:1，性价比偏低")
                else:
                    reasons.append(f"盈亏比 {rr}:1")
        except (TypeError, ValueError):
            pass

    return {
        "code": code,
        "name": name,
        "should_buy": should_buy,
        "confidence": confidence,
        "decision_note": decision_note,
        "current_price": last_price,
        "change_pct_today": round(change_pct, 2),
        "price_deviation_pct": deviation_pct,
        "entry_zone": {"low": entry_low, "high": entry_high} if entry_low else None,
        "stop_loss": stop_loss,
        "target_1": target_1,
        "target_2": target_2,
        "position_pct_suggested": position_pct,
        "risk_reward_ratio": rr,
        "nearest_support": nearest_support,
        "nearest_resistance": nearest_resistance,
        "backtest_summary": bt_stats,
        "reasons": reasons,
        "risks": risks,
        "warnings": warnings,
        "_meta": {
            "data_freshness": last_bar_date,
            "rt_quote_source": "tencent",
            "verified_at": datetime.now().isoformat(timespec="seconds"),
        },
    }


# ── Batch verify — verify multiple stocks in ONE tool call ────────────────

@registry.register(
    name="verify_signal_batch",
    description=(
        "批量验证多只股票能否进场（并发执行 verify_signal）。"
        "传入最多 10 个 code，返回每只股票的 should_buy/confidence/entry/stop/target 摘要。"
        "用于 screener 结果出来后一次性验证所有候选，避免逐个调用浪费步骤。"
        "⚠️ 优先使用此工具代替循环调用 verify_signal。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "codes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "股票代码列表（6位），最多10个",
            },
            "strategy": {
                "type": "string",
                "default": "breakout_pullback",
                "description": "策略类型",
            },
        },
        "required": ["codes"],
    },
    permission="safe",
)
async def verify_signal_batch(args: dict[str, Any]) -> Any:
    codes = args.get("codes", [])[:10]  # cap at 10
    strategy = args.get("strategy", "breakout_pullback")

    async def _verify_one(code: str) -> dict:
        try:
            result = await verify_signal({"code": code, "strategy": strategy})
            if isinstance(result, dict) and "error" not in result:
                # Return compact summary
                return {
                    "code": result.get("code"),
                    "name": result.get("name"),
                    "should_buy": result.get("should_buy"),
                    "confidence": result.get("confidence"),
                    "current_price": result.get("current_price"),
                    "entry_zone": result.get("entry_zone"),
                    "stop_loss": result.get("stop_loss"),
                    "target_1": result.get("target_1"),
                    "risk_reward_ratio": result.get("risk_reward_ratio"),
                    "decision_note": result.get("decision_note"),
                    "risks_count": len(result.get("risks", [])),
                    "top_risk": (result.get("risks") or [""])[0],
                }
            return {"code": code, "error": result.get("error", "unknown")}
        except Exception as e:
            return {"code": code, "error": str(e)}

    results = await asyncio.gather(*[_verify_one(c) for c in codes])

    # Sort: yes > wait > no, then by confidence desc
    order = {"yes": 0, "wait": 1, "no": 2}
    results_sorted = sorted(results, key=lambda r: (order.get(r.get("should_buy", "no"), 3), -(r.get("confidence") or 0)))

    return {
        "verified_count": len(results),
        "buy_count": sum(1 for r in results if r.get("should_buy") == "yes"),
        "wait_count": sum(1 for r in results if r.get("should_buy") == "wait"),
        "results": results_sorted,
    }


# ── End-to-end setup finder — one tool to rule the breakout-pullback workflow ──

@registry.register(
    name="find_setups",
    description=(
        "🎯 **一键找形态+预期共振票**。完整流水线：扫盘 → 形态过滤 → 预期过滤(财务+分析师+板块热度) → 多因子验证 → 招录补充催化剂 → 出成交计划。"
        "适用场景：用户说\"筛 X 只 XX 形态的股\"、\"找几只能买的票\"、\"今天有什么机会\"、\"预期好的股\"。"
        "**不要再手动 pl_screener + verify_signal_batch 串起来**，直接用这个。"
        "默认 expectation_filter='soft'（排除业绩雷+冷门股），默认 with_catalyst=true（联网拼接近期公告/新闻）。"
        "返回带完整 entry/stop/target/RR + expectation_reasons + catalyst 的最终推荐列表。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "enum": ["breakout_pullback", "stabilize", "box_support",
                         "volume_breakout", "macd_divergence",
                         "stage2_breakout", "vcp", "pivot_breakout",
                         "cup_handle", "high_tight_flag"],
                "default": "breakout_pullback",
                "description": "形态: breakout_pullback(突破回踩,默认) | stabilize(下跌企稳) | box_support(箱体支撑) | volume_breakout(放量突破) | macd_divergence(MACD底背离) | stage2_breakout(Stage 2 趋势突破—高败率主仓) | vcp(Minervini 波动收缩—高 RR) | pivot_breakout(O'Neil base 点突破) | cup_handle(杯柄经典) | high_tight_flag(高位紧旗—龙头独享)",
            },
            "n": {
                "type": "integer",
                "default": 5,
                "minimum": 1,
                "maximum": 10,
                "description": "目标推荐数（最终输出的 should_buy=yes 的票数）",
            },
            "min_score": {
                "type": "number",
                "default": 60,
                "description": "screener 最低分阈值，低于此分不进入验证",
            },
            "candidate_pool": {
                "type": "integer",
                "default": 12,
                "minimum": 3,
                "maximum": 20,
                "description": "送进 verify_signal 的候选数（>n 才有挑选余地，建议 2-3 倍 n）",
            },
            "near_ma20": {
                "type": "boolean",
                "default": True,
                "description": "额外过滤：当前价距 MA20 ≤ 4% 才入选（'回踩 20 日线'语义）",
            },
            "min_rr": {
                "type": "number",
                "default": 1.5,
                "description": "盈亏比下限，<此值的票剔除（避免推奖盈亏比差的票）",
            },
            "include_wait": {
                "type": "boolean",
                "default": False,
                "description": "如果 yes 不够 n 只，是否补 wait 状态的（默认 false 宁缺毋滥）",
            },
            "expectation_filter": {
                "type": "string",
                "enum": ["off", "soft", "medium", "strict"],
                "default": "soft",
                "description": (
                    "基本面/预期过滤档位（在技术形态之上叠加）："
                    "off=纯技术 | "
                    "soft=排除业绩雷+冷门股（默认） | "
                    "medium=要求业绩正增长 OR 概念近期资金流入 OR 分析师覆盖足 | "
                    "strict=高增长(np_yoy>30%)+目标价空间>15%+概念热度上升"
                ),
            },
            "with_catalyst": {
                "type": "boolean",
                "default": True,
                "description": "对最终入选股票联网查近期催化剂(公告/业绩/利好新闻)，注入 catalyst 字段。慢但更全面。",
            },
        },
    },
    permission="safe",
)
async def find_setups(args: dict[str, Any]) -> dict[str, Any]:
    pattern = args.get("pattern", "breakout_pullback")
    n_target = int(args.get("n", 5))
    min_score = float(args.get("min_score", 60))
    pool_size = int(args.get("candidate_pool", 12))
    near_ma20 = bool(args.get("near_ma20", True))
    min_rr = float(args.get("min_rr", 1.5))
    include_wait = bool(args.get("include_wait", False))
    exp_filter = str(args.get("expectation_filter", "soft")).lower()
    with_catalyst = bool(args.get("with_catalyst", True))

    # ── (1) Screener ──
    scr = await pl_screener({"pattern": pattern, "min_score": min_score, "limit": pool_size * 2})
    if isinstance(scr, dict) and scr.get("error"):
        return {"error": f"screener failed: {scr['error']}", "stage": "screener"}
    cands = scr.get("results") or scr.get("items") or []
    if not cands:
        return {
            "error": "screener returned no candidates above min_score",
            "stage": "screener",
            "hint": "降低 min_score 或换一个 pattern 试试",
            "scanned_at": scr.get("scanned_at"),
        }

    # ── (2) Optional MA20 distance filter (cheap, runs locally on screener output) ──
    filtered = []
    for c in cands:
        cur = c.get("price") or c.get("current_price")
        ma20 = c.get("ma20")
        # Some screener output doesn't expose MA20; in that case skip filter
        if near_ma20 and cur and ma20:
            dist = abs(cur - ma20) / ma20
            if dist > 0.04:  # > 4% from MA20 → not really "回踩 MA20"
                continue
        filtered.append(c)

    if not filtered:
        filtered = cands  # fall back to original list if MA20 filter killed everything

    # Take top pool_size by score
    filtered = filtered[:pool_size]
    pool_codes = [c.get("code") for c in filtered if c.get("code")]

    # ── (2.5) Expectation filter (fundamentals + sector + analyst) ──
    # Compute expectation score for every candidate (one batch DB query)
    exp_scores: dict[str, dict] = {}
    if exp_filter != "off" and pool_codes:
        exp_scores = await asyncio.to_thread(_compute_expectation_scores, pool_codes)
        # Apply hard gate per filter level
        before = len(filtered)
        filtered = [c for c in filtered if _expectation_gate(exp_scores.get(c.get("code"), {}), exp_filter)]
        pool_codes = [c.get("code") for c in filtered if c.get("code")]
        if not pool_codes:
            return {
                "stage": "expectation_filter",
                "error": f"expectation_filter='{exp_filter}' 把全部候选过滤掉了",
                "candidates_before_expectation": before,
                "hint": "把 expectation_filter 调成 'soft' 或 'off' 重试",
                "scanned_at": scr.get("scanned_at"),
            }

    # ── (3) Batch verify ──
    verify = await verify_signal_batch({"codes": pool_codes, "strategy": pattern})
    if isinstance(verify, dict) and verify.get("error"):
        return {"error": f"verify failed: {verify['error']}", "stage": "verify"}

    verified = verify.get("results", [])

    # ── (4) Filter by should_buy + RR ──
    def _rr(r):
        v = r.get("risk_reward_ratio")
        try:
            return float(v) if v is not None else 0.0
        except Exception:
            return 0.0

    yes_list = [r for r in verified if r.get("should_buy") == "yes" and _rr(r) >= min_rr]
    wait_list = [r for r in verified if r.get("should_buy") == "wait" and _rr(r) >= min_rr]

    final = yes_list[:n_target]
    if include_wait and len(final) < n_target:
        final = final + wait_list[: n_target - len(final)]

    # ── (5) Build final recommendations with full play-book ──
    # Merge screener triggers into verified output for context
    by_code = {c.get("code"): c for c in filtered}
    recommendations = []
    for r in final:
        code = r.get("code")
        sc = by_code.get(code, {})
        exp = exp_scores.get(code, {})
        recommendations.append({
            "code": code,
            "name": r.get("name") or sc.get("name"),
            "should_buy": r.get("should_buy"),
            "confidence": r.get("confidence"),
            "screener_score": sc.get("score"),
            "screener_triggers": sc.get("triggers", []),
            "expectation_score": exp.get("score") if exp else None,
            "expectation_reasons": exp.get("reasons", []) if exp else [],
            "expectation_warnings": exp.get("warnings", []) if exp else [],
            "current_price": r.get("current_price"),
            "entry_zone": r.get("entry_zone"),
            "stop_loss": r.get("stop_loss"),
            "target_1": r.get("target_1"),
            "risk_reward_ratio": r.get("risk_reward_ratio"),
            "decision_note": r.get("decision_note"),
            "top_risk": r.get("top_risk"),
            "risks_count": r.get("risks_count"),
        })

    # ── (6) Optional: pull catalyst news for each final pick (concurrent web_search) ──
    if with_catalyst and recommendations:
        await _attach_catalysts(recommendations)

    return {
        "pattern": pattern,
        "pattern_name": {
            "breakout_pullback": "突破回踩",
            "stabilize": "下跌企稳",
            "box_support": "箱体支撑",
            "volume_breakout": "放量突破",
            "macd_divergence": "MACD底背离",
            "stage2_breakout": "Stage 2 突破",
            "vcp": "VCP 波动收缩",
            "pivot_breakout": "Pivot 点突破",
            "cup_handle": "杯柄形态",
            "high_tight_flag": "高位紧旗",
        }.get(pattern, pattern),
        "expectation_filter": exp_filter,
        "scanned_at": scr.get("scanned_at"),
        "candidates_scanned": len(cands),
        "candidates_after_ma20_filter": len(filtered),
        "verified_count": len(verified),
        "yes_count": len(yes_list),
        "wait_count": len(wait_list),
        "returned_count": len(recommendations),
        "recommendations": recommendations,
        "summary": (
            f"扫描 {len(cands)} 候选 → 形态过滤 → 预期过滤({exp_filter}) {len(filtered)} 只 → "
            f"验证 {len(verified)} 只 → yes={len(yes_list)}, wait={len(wait_list)} → 最终推荐 {len(recommendations)} 只"
        ),
        "next_step_hint": (
            "对 recommendations 中的每只调用 render_stock_chart 出图，然后给出最终建议表格（含 expectation_reasons 和 catalyst）。"
            if recommendations else
            "今日无符合条件的标的。可建议用户：(a) 降低 expectation_filter 到 'soft' 或 'off' (b) 降低 min_rr/min_score (c) 换一个 pattern (d) 等待"
        ),
    }


# ── Expectation scoring helpers (DB-only, fast) ──────────────────────────

def _compute_expectation_scores(codes: list[str]) -> dict[str, dict]:
    """For each code return:
        {score: 0..100, reasons: [...], warnings: [...], data: {...}}
    Single batch query per table; all O(1) extra DB hits.
    """
    if not codes:
        return {}

    from sqlalchemy import create_engine, text as _text

    db_url = os.environ.get("SQLALCHEMY_DATABASE_URI") or os.environ.get(
        "DATABASE_URL", "postgresql+psycopg2://pivotlab:pivotlab@127.0.0.1:5433/pivotlab"
    )
    if "+asyncpg" in db_url:
        db_url = db_url.replace("+asyncpg", "+psycopg2")

    fin: dict[str, dict] = {}
    ana: dict[str, dict] = {}
    concepts_by_code: dict[str, list[str]] = {}
    concept_heat: dict[str, dict] = {}
    last_close: dict[str, float] = {}

    try:
        eng = create_engine(db_url, pool_pre_ping=True, pool_recycle=300)
        with eng.connect() as c:
            # 1) Financials
            r = c.execute(_text(
                "SELECT code, eps_ttm, roe, revenue_yoy, net_profit_yoy, pe_ratio_ttm, fundamental_status "
                "FROM financial_snapshots WHERE code = ANY(:codes)"
            ), {"codes": codes}).fetchall()
            for row in r:
                fin[row[0]] = {
                    "eps_ttm": row[1], "roe": row[2], "rev_yoy": row[3],
                    "np_yoy": row[4], "pe": row[5], "status": row[6],
                }

            # 2) Analyst consensus
            r = c.execute(_text(
                "SELECT code, target_price_high, target_price_low, analyst_count, buy_count, overweight_count "
                "FROM analyst_consensus WHERE code = ANY(:codes)"
            ), {"codes": codes}).fetchall()
            for row in r:
                ana[row[0]] = {
                    "tp_high": row[1], "tp_low": row[2], "n_analyst": row[3],
                    "n_buy": row[4], "n_ow": row[5],
                }

            # 3) Concepts per code
            r = c.execute(_text(
                "SELECT code, concept FROM stock_concepts WHERE code = ANY(:codes)"
            ), {"codes": codes}).fetchall()
            for row in r:
                concepts_by_code.setdefault(row[0], []).append(row[1])

            # 4) Latest concept heat (within last 7 trade days)
            all_concepts = {ct for cs in concepts_by_code.values() for ct in cs}
            if all_concepts:
                r = c.execute(_text(
                    "SELECT DISTINCT ON (concept) concept, change_pct, net_inflow, heat_score, heat_level, trade_date "
                    "FROM concept_heat_history WHERE concept = ANY(:cs) "
                    "ORDER BY concept, trade_date DESC"
                ), {"cs": list(all_concepts)}).fetchall()
                for row in r:
                    concept_heat[row[0]] = {
                        "change_pct": row[1], "net_inflow": row[2],
                        "heat_score": row[3], "heat_level": row[4], "date": row[5],
                    }

            # 5) Latest close (for target-price upside calc)
            r = c.execute(_text(
                "SELECT DISTINCT ON (code) code, close FROM daily_candles "
                "WHERE code = ANY(:codes) ORDER BY code, trade_date DESC"
            ), {"codes": codes}).fetchall()
            for row in r:
                last_close[row[0]] = float(row[1] or 0)
    except Exception as e:
        logger.warning("expectation scoring db failed: %s", e)
        return {c: {"score": 0, "reasons": [], "warnings": ["expectation 数据查询失败"]} for c in codes}

    # Build per-code score
    out: dict[str, dict] = {}
    for code in codes:
        score = 0
        reasons: list[str] = []
        warnings: list[str] = []

        # ── Financials (max 35) ──
        f = fin.get(code)
        if f:
            np_yoy = f.get("np_yoy")
            rev_yoy = f.get("rev_yoy")
            roe = f.get("roe")
            if np_yoy is not None:
                if np_yoy >= 50:
                    score += 20
                    reasons.append(f"净利润增速 +{np_yoy:.0f}%")
                elif np_yoy >= 20:
                    score += 12
                    reasons.append(f"净利润增速 +{np_yoy:.0f}%")
                elif np_yoy > 0:
                    score += 5
                elif np_yoy < -30:
                    warnings.append(f"⚠️ 净利润大幅下滑 {np_yoy:.0f}%")
                elif np_yoy < 0:
                    warnings.append(f"净利润下滑 {np_yoy:.0f}%")
            if rev_yoy is not None and rev_yoy >= 20:
                score += 8
                reasons.append(f"营收 +{rev_yoy:.0f}%")
            if roe is not None and roe >= 15:
                score += 7
                reasons.append(f"ROE {roe:.1f}%")
        else:
            warnings.append("无财务数据")

        # ── Analyst (max 25) ──
        a = ana.get(code)
        cur_close = last_close.get(code) or 0
        if a:
            n = int(a.get("n_analyst") or 0)
            tp_low = a.get("tp_low")
            tp_high = a.get("tp_high")
            if n >= 5:
                score += 8
                reasons.append(f"{n} 家机构覆盖")
            elif n >= 3:
                score += 4
            if tp_low and cur_close > 0:
                upside_low = (tp_low - cur_close) / cur_close * 100
                if upside_low >= 15:
                    score += 12
                    reasons.append(f"目标价下限+{upside_low:.0f}%")
                elif upside_low >= 5:
                    score += 6
                elif upside_low < -10:
                    warnings.append(f"⚠️ 已超目标价上限 {(tp_high or 0 - cur_close)/cur_close*100:.0f}%")
            n_buy = int(a.get("n_buy") or 0) + int(a.get("n_ow") or 0)
            if n >= 3 and n_buy / max(n, 1) >= 0.7:
                score += 5
                reasons.append("70%+ 机构买入评级")

        # ── Sector heat (max 20) ──
        my_concepts = concepts_by_code.get(code, [])
        best_heat = None
        for ct in my_concepts:
            h = concept_heat.get(ct)
            if not h:
                continue
            if best_heat is None or (h.get("heat_score") or 0) > (best_heat[1].get("heat_score") or 0):
                best_heat = (ct, h)
        if best_heat:
            ct, h = best_heat
            inflow = h.get("net_inflow") or 0
            heat_score = h.get("heat_score") or 0
            if inflow > 5e8:
                score += 10
                reasons.append(f"概念「{ct}」资金净流入 {inflow/1e8:.1f}亿")
            elif inflow > 0:
                score += 4
            elif inflow < -3e8:
                warnings.append(f"概念「{ct}」资金净流出 {inflow/1e8:.1f}亿")
            if heat_score >= 70:
                score += 10
                reasons.append(f"概念热度 {heat_score:.0f}")
            elif heat_score >= 50:
                score += 5

        # ── Cap and write ──
        score = max(0, min(100, score))
        out[code] = {
            "score": score,
            "reasons": reasons,
            "warnings": warnings,
            "data": {
                "fin": f, "analyst": a,
                "top_concept": best_heat[0] if best_heat else None,
                "concepts_count": len(my_concepts),
            },
        }

    return out


def _expectation_gate(exp: dict, level: str) -> bool:
    """Return True if candidate passes the gate at given level."""
    if not exp:
        return level in ("off", "soft")  # no data → only loose levels admit

    score = exp.get("score", 0)
    warnings = exp.get("warnings", [])
    has_serious_warning = any("⚠️" in w for w in warnings)

    # Block 业绩雷 in all levels except off
    if level != "off" and has_serious_warning:
        return False

    # No data check at soft+
    if level in ("medium", "strict"):
        if not exp.get("data", {}).get("fin") and not exp.get("data", {}).get("analyst"):
            return False  # 冷门股无任何数据

    if level == "off":
        return True
    if level == "soft":
        return True  # only block 业绩雷
    if level == "medium":
        return score >= 15
    if level == "strict":
        return score >= 35
    return True


# ── Catalyst lookup (web_search top news) ────────────────────────────────

async def _attach_catalysts(recommendations: list[dict]) -> None:
    """For each rec, run a web_search and attach `catalyst` field with 1-3 recent
    news headlines + urls. Best-effort; failures are silently tolerated.
    """
    from app.agent.tools.web_search import web_search

    async def _one(rec):
        code = rec.get("code")
        name = rec.get("name") or ""
        if not code:
            rec["catalyst"] = []
            return
        try:
            q = f"{name} {code} 公告 OR 业绩 OR 利好 OR 重组 最新"
            res = await asyncio.wait_for(
                web_search({"query": q, "max_results": 3}), timeout=12,
            )
            items = (res or {}).get("results", []) if isinstance(res, dict) else []
            rec["catalyst"] = [
                {"title": it.get("title"), "url": it.get("url"), "snippet": (it.get("snippet") or "")[:140]}
                for it in items[:3]
            ]
        except Exception as e:
            rec["catalyst"] = []
            rec["catalyst_error"] = str(e)[:80]

    await asyncio.gather(*[_one(r) for r in recommendations])


