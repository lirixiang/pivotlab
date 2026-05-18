"""Sector Pool endpoints — 人工维护的赛道池 CRUD。

设计原则：
  - 完全独立于自动抓取的 ConceptBoard/StockConcept
  - 软删除（archived_at / removed_at）保留历史，便于回测复现
  - 个股可归属多个赛道
  - 提供工具端点 GET /stocks 给 quant pipeline 在 Universe 层消费
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import SectorPool, SectorPoolStock, Stock

router = APIRouter(prefix="/api/sector-pool", tags=["sector-pool"])


# ── Schemas ───────────────────────────────────────────────────

class SectorPoolCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    category: str = ""
    description: str = ""
    rank: int = 0


class SectorPoolUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=60)
    category: Optional[str] = None
    description: Optional[str] = None
    rank: Optional[int] = None
    status: Optional[str] = None  # active / archived


class SectorStockCreate(BaseModel):
    code: str = Field(..., min_length=6, max_length=10)
    tier: int = Field(2, ge=1, le=3)
    note: str = ""


class SectorStockUpdate(BaseModel):
    tier: Optional[int] = Field(None, ge=1, le=3)
    note: Optional[str] = None


class SectorStockBulkCreate(BaseModel):
    codes: list[str]
    tier: int = Field(2, ge=1, le=3)


# ── Helpers ───────────────────────────────────────────────────

async def _get_sector_or_404(session: AsyncSession, sector_id: int) -> SectorPool:
    s = await session.get(SectorPool, sector_id)
    if s is None or s.status != "active":
        raise HTTPException(status_code=404, detail="赛道不存在或已归档")
    return s


async def _stock_name_map(session: AsyncSession, codes: list[str]) -> dict[str, dict]:
    if not codes:
        return {}
    rows = (await session.execute(select(Stock).where(Stock.code.in_(codes)))).scalars().all()
    return {s.code: {"name": s.name, "industry": s.industry} for s in rows}


def _serialize_pool(p: SectorPool, stock_count: int = 0) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "category": p.category or "",
        "description": p.description or "",
        "rank": p.rank or 0,
        "status": p.status,
        "stock_count": stock_count,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


def _serialize_stock(s: SectorPoolStock, info: dict | None) -> dict:
    return {
        "id": s.id,
        "sector_id": s.sector_id,
        "code": s.code,
        "name": (info or {}).get("name", ""),
        "industry": (info or {}).get("industry", ""),
        "tier": s.tier,
        "note": s.note or "",
        "added_at": s.added_at.isoformat() if s.added_at else None,
    }


# ── Sector CRUD ───────────────────────────────────────────────

@router.get("")
async def list_pools(
    include_archived: bool = False,
    session: AsyncSession = Depends(get_db),
):
    """列出所有赛道（按 category, rank 排序）。"""
    q = select(SectorPool)
    if not include_archived:
        q = q.where(SectorPool.status == "active")
    q = q.order_by(SectorPool.category.asc(), SectorPool.rank.asc(), SectorPool.id.asc())
    pools = (await session.execute(q)).scalars().all()

    # bulk fetch stock counts (active only)
    if pools:
        ids = [p.id for p in pools]
        count_rows = (await session.execute(
            select(SectorPoolStock.sector_id, func.count(SectorPoolStock.id))
            .where(SectorPoolStock.sector_id.in_(ids), SectorPoolStock.removed_at == "")
            .group_by(SectorPoolStock.sector_id)
        )).all()
        count_map = {sid: n for sid, n in count_rows}
    else:
        count_map = {}

    return {"items": [_serialize_pool(p, count_map.get(p.id, 0)) for p in pools]}


@router.post("")
async def create_pool(
    body: SectorPoolCreate,
    session: AsyncSession = Depends(get_db),
):
    # 检查同名 active 是否已存在
    existing = (await session.execute(
        select(SectorPool).where(
            SectorPool.name == body.name,
            SectorPool.status == "active",
        )
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"赛道「{body.name}」已存在")

    p = SectorPool(
        name=body.name.strip(),
        category=body.category.strip(),
        description=body.description,
        rank=body.rank,
        status="active",
        archived_at="",
    )
    session.add(p)
    await session.commit()
    await session.refresh(p)
    return _serialize_pool(p, 0)


@router.patch("/{sector_id}")
async def update_pool(
    sector_id: int,
    body: SectorPoolUpdate,
    session: AsyncSession = Depends(get_db),
):
    p = await session.get(SectorPool, sector_id)
    if p is None:
        raise HTTPException(status_code=404, detail="赛道不存在")

    if body.name is not None and body.name.strip() != p.name:
        # 同名 active 校验
        dup = (await session.execute(
            select(SectorPool).where(
                SectorPool.name == body.name.strip(),
                SectorPool.status == "active",
                SectorPool.id != sector_id,
            )
        )).scalar_one_or_none()
        if dup:
            raise HTTPException(status_code=409, detail=f"赛道「{body.name}」已存在")
        p.name = body.name.strip()
    if body.category is not None:
        p.category = body.category.strip()
    if body.description is not None:
        p.description = body.description
    if body.rank is not None:
        p.rank = body.rank
    if body.status is not None:
        if body.status not in ("active", "archived"):
            raise HTTPException(status_code=400, detail="status 仅支持 active/archived")
        p.status = body.status
        p.archived_at = datetime.utcnow().isoformat() if body.status == "archived" else ""
    p.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(p)
    return _serialize_pool(p)


@router.delete("/{sector_id}")
async def delete_pool(
    sector_id: int,
    session: AsyncSession = Depends(get_db),
):
    """软删除（归档）。如已归档则物理删除。"""
    p = await session.get(SectorPool, sector_id)
    if p is None:
        raise HTTPException(status_code=404, detail="赛道不存在")
    if p.status == "archived":
        await session.delete(p)
        await session.commit()
        return {"ok": True, "hard_deleted": True}
    p.status = "archived"
    p.archived_at = datetime.utcnow().isoformat()
    p.updated_at = datetime.utcnow()
    await session.commit()
    return {"ok": True, "hard_deleted": False}


# ── Stock membership ─────────────────────────────────────────

@router.get("/{sector_id}/stocks")
async def list_pool_stocks(
    sector_id: int,
    session: AsyncSession = Depends(get_db),
):
    await _get_sector_or_404(session, sector_id)
    rows = (await session.execute(
        select(SectorPoolStock)
        .where(SectorPoolStock.sector_id == sector_id, SectorPoolStock.removed_at == "")
        .order_by(SectorPoolStock.tier.asc(), SectorPoolStock.id.asc())
    )).scalars().all()
    info_map = await _stock_name_map(session, [r.code for r in rows])
    return {"items": [_serialize_stock(r, info_map.get(r.code)) for r in rows]}


@router.post("/{sector_id}/stocks")
async def add_pool_stock(
    sector_id: int,
    body: SectorStockCreate,
    session: AsyncSession = Depends(get_db),
):
    await _get_sector_or_404(session, sector_id)
    code = body.code.strip()

    # 校验股票存在
    stock = await session.get(Stock, code)
    if stock is None:
        raise HTTPException(status_code=400, detail=f"股票代码 {code} 不在 stocks 表中")

    # 已存在 active 记录？
    existing = (await session.execute(
        select(SectorPoolStock).where(
            SectorPoolStock.sector_id == sector_id,
            SectorPoolStock.code == code,
            SectorPoolStock.removed_at == "",
        )
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"{code} 已在该赛道")

    row = SectorPoolStock(
        sector_id=sector_id,
        code=code,
        tier=body.tier,
        note=body.note,
        removed_at="",
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _serialize_stock(row, {"name": stock.name, "industry": stock.industry})


@router.post("/{sector_id}/stocks/bulk")
async def bulk_add_pool_stocks(
    sector_id: int,
    body: SectorStockBulkCreate,
    session: AsyncSession = Depends(get_db),
):
    """批量添加（用于粘贴一组代码）。返回 added / skipped_existing / skipped_unknown。"""
    await _get_sector_or_404(session, sector_id)
    codes = [c.strip() for c in body.codes if c and c.strip()]
    if not codes:
        return {"added": 0, "skipped_existing": 0, "skipped_unknown": 0, "added_codes": []}

    info_map = await _stock_name_map(session, codes)

    existing_codes = set((await session.execute(
        select(SectorPoolStock.code).where(
            SectorPoolStock.sector_id == sector_id,
            SectorPoolStock.code.in_(codes),
            SectorPoolStock.removed_at == "",
        )
    )).scalars().all())

    added: list[str] = []
    skipped_existing = 0
    skipped_unknown = 0
    for c in codes:
        if c not in info_map:
            skipped_unknown += 1
            continue
        if c in existing_codes:
            skipped_existing += 1
            continue
        session.add(SectorPoolStock(
            sector_id=sector_id, code=c, tier=body.tier, removed_at="",
        ))
        added.append(c)
        existing_codes.add(c)  # 同次提交内去重
    if added:
        await session.commit()
    return {
        "added": len(added),
        "skipped_existing": skipped_existing,
        "skipped_unknown": skipped_unknown,
        "added_codes": added,
    }


@router.patch("/{sector_id}/stocks/{code}")
async def update_pool_stock(
    sector_id: int,
    code: str,
    body: SectorStockUpdate,
    session: AsyncSession = Depends(get_db),
):
    row = (await session.execute(
        select(SectorPoolStock).where(
            SectorPoolStock.sector_id == sector_id,
            SectorPoolStock.code == code,
            SectorPoolStock.removed_at == "",
        )
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="个股不在该赛道")
    if body.tier is not None:
        row.tier = body.tier
    if body.note is not None:
        row.note = body.note
    await session.commit()
    await session.refresh(row)
    stock = await session.get(Stock, code)
    return _serialize_stock(row, {"name": stock.name if stock else "", "industry": stock.industry if stock else ""})


@router.delete("/{sector_id}/stocks/{code}")
async def remove_pool_stock(
    sector_id: int,
    code: str,
    session: AsyncSession = Depends(get_db),
):
    row = (await session.execute(
        select(SectorPoolStock).where(
            SectorPoolStock.sector_id == sector_id,
            SectorPoolStock.code == code,
            SectorPoolStock.removed_at == "",
        )
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="个股不在该赛道")
    row.removed_at = datetime.utcnow().isoformat()
    await session.commit()
    return {"ok": True}


# ── Tool endpoint: 给 quant pipeline / 外部消费者 ────────────

@router.get("/codes/union")
async def get_codes_union(
    pool_ids: str = Query(..., description="逗号分隔的赛道 id 列表"),
    tier_max: int = Query(3, ge=1, le=3, description="最低 tier (3=全收, 2=只龙一龙二, 1=只龙一)"),
    session: AsyncSession = Depends(get_db),
):
    """返回若干赛道池里所有 active 个股代码的并集。给 Universe 层做交集过滤用。"""
    try:
        ids = [int(x) for x in pool_ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="pool_ids 格式错误")
    if not ids:
        return {"codes": [], "total": 0}
    rows = (await session.execute(
        select(SectorPoolStock.code, SectorPoolStock.tier, SectorPoolStock.sector_id)
        .where(
            SectorPoolStock.sector_id.in_(ids),
            SectorPoolStock.removed_at == "",
            SectorPoolStock.tier <= tier_max,
        )
    )).all()
    codes = sorted({r[0] for r in rows})
    return {
        "codes": codes,
        "total": len(codes),
        "by_pool": {
            sid: sorted({r[0] for r in rows if r[2] == sid})
            for sid in ids
        },
    }
