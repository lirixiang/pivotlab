import asyncio
import logging

from fastapi import APIRouter

from ..services.backtester import run_backtest
from ..services.data_provider import get_candles

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


def _run(
    code: str, strategy: str, period: str,
    stop_loss: float, target: float,
    volume_filter: bool, shrink_filter: bool,
    close_above_support: bool, weekly_confluence: bool,
) -> dict:
    candles = get_candles(code, period="daily", days=500)
    if not candles:
        return {"error": "no candles"}
    result = run_backtest(
        candles,
        strategy=strategy,  # type: ignore
        period=period,
        stop_loss_pct=stop_loss,
        target_pct=target,
        volume_filter=volume_filter,
        shrink_filter=shrink_filter,
        close_above_support=close_above_support,
        weekly_confluence=weekly_confluence,
    )
    result.code = code
    return {
        "code": result.code,
        "strategy": result.strategy,
        "period": result.period,
        "trades": result.trades,
        "equity_curve": result.equity_curve,
        "stats": result.stats,
        "levels_used": result.levels_used,
    }


@router.post("")
async def backtest(body: dict):
    code = body.get("code", "000001")
    strategy = body.get("strategy", "breakout_pullback")
    period = body.get("period", "3m")
    stop_loss = float(body.get("stop_loss", 2.5))
    target = float(body.get("target", 6.0))
    volume_filter = body.get("volume_filter", True)
    shrink_filter = body.get("shrink_filter", True)
    close_above_support = body.get("close_above_support", True)
    weekly_confluence = body.get("weekly_confluence", True)

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None, _run,
        code, strategy, period,
        stop_loss, target,
        volume_filter, shrink_filter,
        close_above_support, weekly_confluence,
    )
    return result
