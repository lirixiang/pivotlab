from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import UserSettings

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingPayload(BaseModel):
    value: dict


@router.get("/{key}")
async def get_setting(key: str, db: AsyncSession = Depends(get_db)):
    row = (await db.execute(select(UserSettings).where(UserSettings.key == key))).scalar_one_or_none()
    if not row:
        return {"key": key, "value": {}}
    return {"key": row.key, "value": row.value}


@router.put("/{key}")
async def put_setting(key: str, payload: SettingPayload, db: AsyncSession = Depends(get_db)):
    row = (await db.execute(select(UserSettings).where(UserSettings.key == key))).scalar_one_or_none()
    if row:
        row.value = payload.value
        row.updated_at = datetime.utcnow()
    else:
        row = UserSettings(key=key, value=payload.value)
        db.add(row)
    await db.commit()
    return {"key": key, "value": payload.value, "ok": True}


@router.get("")
async def list_settings(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(UserSettings))).scalars().all()
    return {r.key: r.value for r in rows}
