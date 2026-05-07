"""Data-source registry — defines available providers per sync task type.

Each task type has one or more data sources with metadata (id, name, url, status).
The user can pick which source to use for each task type via the API.
Default selections are stored in user_settings DB table (key = 'sync_sources').
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── Source definitions ──────────────────────────────────────────

SOURCE_CATALOG: dict[str, list[dict]] = {
    "stocks": [
        {
            "id": "em_api",
            "name": "东方财富 API",
            "desc": "datacenter-web RPT_LICO_FN_CPD 全量股票列表",
            "url": "datacenter-web.eastmoney.com",
            "status": "ok",
            "default": True,
        },
        {
            "id": "akshare",
            "name": "AKShare",
            "desc": "akshare stock_info_a_code_name() 接口",
            "url": "akshare (东财内部)",
            "status": "ok",
        },
    ],
    "quotes": [
        {
            "id": "tencent",
            "name": "腾讯财经",
            "desc": "qt.gtimg.cn 实时行情批量接口",
            "url": "qt.gtimg.cn",
            "status": "ok",
            "default": True,
        },
        {
            "id": "em_push",
            "name": "东方财富推送",
            "desc": "push2.eastmoney.com 实时行情",
            "url": "push2.eastmoney.com",
            "status": "blocked",
        },
    ],
    "financials": [
        {
            "id": "em_datacenter",
            "name": "东方财富 DataCenter",
            "desc": "RPT_LICO_FN_CPD 财报快照（EPS/ROE/增长率）",
            "url": "datacenter-web.eastmoney.com",
            "status": "ok",
            "default": True,
        },
        {
            "id": "akshare",
            "name": "AKShare",
            "desc": "akshare 逐只抓取（慢，已弃用）",
            "url": "akshare",
            "status": "deprecated",
        },
    ],
    "concepts": [
        {
            "id": "em_datacenter",
            "name": "东方财富 DataCenter",
            "desc": "RPT_WEB_RESPREDICT CONCEPTINDEX_BOARD 字段",
            "url": "datacenter-web.eastmoney.com",
            "status": "ok",
            "default": True,
        },
        {
            "id": "akshare",
            "name": "AKShare",
            "desc": "akshare 板块接口（push2 已封，不可用）",
            "url": "push2.eastmoney.com",
            "status": "blocked",
        },
    ],
    "industry": [
        {
            "id": "em_datacenter",
            "name": "东方财富 DataCenter",
            "desc": "RPT_WEB_RESPREDICT INDUSTRY_BOARD 字段",
            "url": "datacenter-web.eastmoney.com",
            "status": "ok",
            "default": True,
        },
        {
            "id": "akshare",
            "name": "AKShare",
            "desc": "akshare 板块接口（push2 已封，不可用）",
            "url": "push2.eastmoney.com",
            "status": "blocked",
        },
    ],
    "analyst_consensus": [
        {
            "id": "em_datacenter",
            "name": "东方财富 DataCenter",
            "desc": "RPT_WEB_RESPREDICT 机构一致预期（目标价/评级/EPS）",
            "url": "datacenter-web.eastmoney.com",
            "status": "ok",
            "default": True,
        },
    ],
    "daily_candles": [
        {
            "id": "dual",
            "name": "双源自动切换",
            "desc": "腾讯优先，失败自动回退东财（默认策略）",
            "url": "tencent + eastmoney",
            "status": "ok",
            "default": True,
        },
        {
            "id": "tencent",
            "name": "腾讯财经",
            "desc": "web.ifzq.gtimg.cn 前复权日K（快速稳定）",
            "url": "web.ifzq.gtimg.cn",
            "status": "ok",
        },
        {
            "id": "em_push",
            "name": "东方财富 K线",
            "desc": "push2his.eastmoney.com 前复权日K（备用源）",
            "url": "push2his.eastmoney.com",
            "status": "ok",
        },
    ],
}


def get_defaults() -> dict[str, str]:
    """Return default source_id per task type."""
    defaults = {}
    for task_type, sources in SOURCE_CATALOG.items():
        for src in sources:
            if src.get("default"):
                defaults[task_type] = src["id"]
                break
        if task_type not in defaults and sources:
            defaults[task_type] = sources[0]["id"]
    return defaults


# ── Persistence via user_settings ───────────────────────────────

def _get_sync_engine():
    from .sync_service import _get_sync_engine as _eng
    return _eng()


def load_selected_sources() -> dict[str, str]:
    """Load user-selected sources from DB, merged with defaults."""
    from ..models import UserSettings
    defaults = get_defaults()
    try:
        engine = _get_sync_engine()
        with Session(engine) as s:
            row = s.execute(
                select(UserSettings).where(UserSettings.key == "sync_sources")
            ).scalar_one_or_none()
            if row and isinstance(row.value, dict):
                # Merge: user overrides win, but only if source_id is valid
                for task_type, source_id in row.value.items():
                    if task_type in SOURCE_CATALOG:
                        valid_ids = {src["id"] for src in SOURCE_CATALOG[task_type]}
                        if source_id in valid_ids:
                            defaults[task_type] = source_id
    except Exception as e:
        logger.warning("Failed to load sync sources config: %s", e)
    return defaults


def save_selected_sources(selections: dict[str, str]) -> bool:
    """Save user source selections to DB. Returns True on success."""
    from ..models import UserSettings
    from datetime import datetime

    # Validate
    for task_type, source_id in selections.items():
        if task_type not in SOURCE_CATALOG:
            raise ValueError(f"未知任务类型: {task_type}")
        valid_ids = {src["id"] for src in SOURCE_CATALOG[task_type]}
        if source_id not in valid_ids:
            raise ValueError(f"无效数据源 {source_id}（任务 {task_type}）")
        # Check status
        src = next(s for s in SOURCE_CATALOG[task_type] if s["id"] == source_id)
        if src["status"] == "blocked":
            raise ValueError(f"数据源 {src['name']} 当前不可用（已封锁）")

    try:
        engine = _get_sync_engine()
        with Session(engine) as s:
            row = s.execute(
                select(UserSettings).where(UserSettings.key == "sync_sources")
            ).scalar_one_or_none()
            if row:
                # Merge with existing
                current = row.value if isinstance(row.value, dict) else {}
                current.update(selections)
                row.value = current
                row.updated_at = datetime.utcnow()
            else:
                s.add(UserSettings(key="sync_sources", value=selections))
            s.commit()
        return True
    except ValueError:
        raise
    except Exception as e:
        logger.error("Failed to save sync sources: %s", e)
        return False


def get_source_for(task_type: str) -> str:
    """Get currently selected source_id for a task type."""
    selected = load_selected_sources()
    return selected.get(task_type, get_defaults().get(task_type, ""))


def get_catalog_with_selection() -> dict:
    """Return full catalog annotated with current selections — for API response."""
    selected = load_selected_sources()
    result = {}
    for task_type, sources in SOURCE_CATALOG.items():
        active_id = selected.get(task_type, "")
        result[task_type] = {
            "selected": active_id,
            "sources": [
                {**src, "selected": src["id"] == active_id}
                for src in sources
            ],
        }
    return result
