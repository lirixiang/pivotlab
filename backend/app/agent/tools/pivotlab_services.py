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
from zoneinfo import ZoneInfo as _ZoneInfo
_CN_TZ = _ZoneInfo("Asia/Shanghai")
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
        "'breakout_pullback' (突破回踩), 'macd_divergence' (MACD底背离), "
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
                "enum": ["breakout_pullback", "macd_divergence",
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
    today_str = datetime.now(_CN_TZ).strftime("%Y-%m-%d")
    if last_bar_date and last_bar_date[:10] < today_str:
        from datetime import date as _date
        days_old = (_date.fromisoformat(today_str) - _date.fromisoformat(last_bar_date[:10])).days
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
