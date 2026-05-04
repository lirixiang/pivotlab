from datetime import datetime

from fastapi import APIRouter

from ..schemas import MarketOverview
from ..services.data_provider import get_indices

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/overview", response_model=MarketOverview)
async def overview() -> MarketOverview:
    indices = get_indices()
    return MarketOverview(
        indices=indices,
        total_amount=8914.0,
        server_time=datetime.now(),
    )
