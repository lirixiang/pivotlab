import asyncio
import logging

from fastapi import APIRouter

from ..services.backtester import BacktestConfig, run_backtest
from ..services.data_provider import get_candles
from ..services.signal_generator import generate_signal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


def _run(code: str, strategy: str, period: str, cfg: BacktestConfig) -> dict:
    candles = get_candles(code, period="daily", days=500)
    if not candles:
        return {"error": "no candles"}
    result = run_backtest(candles, strategy=strategy, period=period, config=cfg)  # type: ignore
    result.code = code
    return {
        "code": result.code,
        "strategy": result.strategy,
        "period": result.period,
        "trades": result.trades,
        "equity_curve": result.equity_curve,
        "stats": result.stats,
        "levels_used": result.levels_used,
        "config": result.config,
        "candles": [
            {"date": c.date, "open": c.open, "high": c.high,
             "low": c.low, "close": c.close, "volume": c.volume}
            for c in candles
        ],
    }


@router.post("")
async def backtest(body: dict):
    code = body.get("code", "000001")
    strategy = body.get("strategy", "breakout_pullback")
    period = body.get("period", "3m")

    cfg = BacktestConfig(
        stop_loss_pct=float(body.get("stop_loss", 2.5)),
        target_pct=float(body.get("target", 6.0)),
        max_hold_bars=int(body.get("max_hold_bars", 20)),
        use_atr_stop=body.get("use_atr_stop", False),
        atr_stop_mult=float(body.get("atr_stop_mult", 2.0)),
        volume_filter=body.get("volume_filter", True),
        shrink_filter=body.get("shrink_filter", True),
        close_above_support=body.get("close_above_support", True),
        weekly_confluence=body.get("weekly_confluence", True),
        ma_trend_filter=body.get("ma_trend_filter", False),
        ma_trend_period=int(body.get("ma_trend_period", 20)),
        pullback_min_pct=float(body.get("pullback_min_pct", 0.1)),
        pullback_max_pct=float(body.get("pullback_max_pct", 3.0)),
        min_level_score=int(body.get("min_level_score", 30)),
        stabilize_bars=int(body.get("stabilize_bars", 3)),
        stabilize_max_dist_pct=float(body.get("stabilize_max_dist_pct", 3.0)),
        commission_pct=float(body.get("commission_pct", 0.1)),
        slippage_pct=float(body.get("slippage_pct", 0.05)),
        cooldown_bars=int(body.get("cooldown_bars", 2)),
    )

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _run, code, strategy, period, cfg)
    return result


def _build_cfg(body: dict) -> BacktestConfig:
    return BacktestConfig(
        stop_loss_pct=float(body.get("stop_loss", 2.5)),
        target_pct=float(body.get("target", 6.0)),
        max_hold_bars=int(body.get("max_hold_bars", 20)),
        use_atr_stop=body.get("use_atr_stop", False),
        atr_stop_mult=float(body.get("atr_stop_mult", 2.0)),
        volume_filter=body.get("volume_filter", True),
        shrink_filter=body.get("shrink_filter", True),
        close_above_support=body.get("close_above_support", True),
        weekly_confluence=body.get("weekly_confluence", True),
        ma_trend_filter=body.get("ma_trend_filter", False),
        ma_trend_period=int(body.get("ma_trend_period", 20)),
        pullback_min_pct=float(body.get("pullback_min_pct", 0.1)),
        pullback_max_pct=float(body.get("pullback_max_pct", 3.0)),
        min_level_score=int(body.get("min_level_score", 30)),
        stabilize_bars=int(body.get("stabilize_bars", 3)),
        stabilize_max_dist_pct=float(body.get("stabilize_max_dist_pct", 3.0)),
        commission_pct=float(body.get("commission_pct", 0.1)),
        slippage_pct=float(body.get("slippage_pct", 0.05)),
        cooldown_bars=int(body.get("cooldown_bars", 2)),
    )


@router.post("/signal")
async def signal(body: dict):
    """Generate live trading signal for a stock using current config."""
    code = body.get("code", "000001")
    strategy = body.get("strategy", "breakout_pullback")
    cfg = _build_cfg(body)
    backtest_stats = body.get("backtest_stats")

    def _gen():
        candles = get_candles(code, period="daily", days=500)
        if not candles:
            return {"error": "no candles"}
        return generate_signal(candles, strategy=strategy, config=cfg, backtest_stats=backtest_stats)

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _gen)
