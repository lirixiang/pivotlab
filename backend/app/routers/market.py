import asyncio
from datetime import datetime

from fastapi import APIRouter

from ..schemas import MarketOverview
from ..services.data_provider import get_indices

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/overview", response_model=MarketOverview)
async def overview() -> MarketOverview:
    try:
        indices = await asyncio.wait_for(
            asyncio.to_thread(get_indices), timeout=10.0,
        )
    except asyncio.TimeoutError:
        indices = []
    return MarketOverview(
        indices=indices,
        total_amount=8914.0,
        server_time=datetime.now(),
    )
