"""Screenshot → stock-code OCR + batch watchlist import."""
from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import Stock, WatchlistItem
from ..services.ocr import extract_codes_from_image

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ocr", tags=["ocr"])

MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 8 MB


@router.post("/extract-codes")
async def extract_codes(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Extract A-share stock codes from an uploaded screenshot.

    Returns each candidate enriched with the matching stock name (if known)
    and whether it is already in the watchlist.
    """
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "image too large (max 8MB)")

    try:
        candidates = extract_codes_from_image(data)
    except ImportError as exc:
        logger.error("PaddleOCR not installed: %s", exc)
        raise HTTPException(500, "OCR engine unavailable: paddleocr not installed")
    except Exception as exc:
        logger.exception("OCR processing failed")
        raise HTTPException(500, f"OCR failed: {exc}")

    if not candidates:
        return {"candidates": []}

    codes = [c["code"] for c in candidates]
    stocks = {
        s.code: s
        for s in (
            await db.execute(select(Stock).where(Stock.code.in_(codes)))
        ).scalars().all()
    }
    in_watch = {
        w.code
        for w in (
            await db.execute(select(WatchlistItem).where(WatchlistItem.code.in_(codes)))
        ).scalars().all()
    }

    out = []
    for c in candidates:
        s = stocks.get(c["code"])
        out.append({
            "code": c["code"],
            "name": s.name if s else "",
            "industry": s.industry if s else "",
            "valid": s is not None,
            "in_watchlist": c["code"] in in_watch,
            "confidence": round(c["confidence"], 3),
            "text": c.get("text", ""),
        })
    return {"candidates": out}


class ImportBody(BaseModel):
    codes: List[str]
    note: str = ""


@router.post("/import-watchlist")
async def import_watchlist(body: ImportBody, db: AsyncSession = Depends(get_db)):
    """Batch-add codes to the watchlist. Skips duplicates and unknown codes."""
    codes = sorted({c.strip() for c in body.codes if c and c.strip()})
    if not codes:
        return {"added": 0, "skipped_existing": 0, "skipped_unknown": 0, "added_codes": []}

    stocks = {
        s.code: s
        for s in (
            await db.execute(select(Stock).where(Stock.code.in_(codes)))
        ).scalars().all()
    }
    existing = {
        w.code
        for w in (
            await db.execute(select(WatchlistItem).where(WatchlistItem.code.in_(codes)))
        ).scalars().all()
    }

    added_codes: list[str] = []
    skipped_existing = 0
    skipped_unknown = 0
    for code in codes:
        if code in existing:
            skipped_existing += 1
            continue
        if code not in stocks:
            skipped_unknown += 1
            continue
        db.add(WatchlistItem(code=code, name=stocks[code].name, note=body.note))
        added_codes.append(code)

    if added_codes:
        await db.commit()

    return {
        "added": len(added_codes),
        "skipped_existing": skipped_existing,
        "skipped_unknown": skipped_unknown,
        "added_codes": added_codes,
    }
