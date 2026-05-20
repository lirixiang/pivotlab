"""JoinQuant 风格策略 API。

回测：
  POST /api/jq/backtest            — 提交回测（同步，等待返回）

筛选：
  POST /api/jq/screener/run        — 执行筛选策略（同步）
  GET  /api/jq/screener/template   — 默认示例代码
  GET  /api/jq/screener/templates  — 列出所有内置模板

策略管理（回测/筛选共用，按 type 区分）：
  GET  /api/jq/strategies          — 列出已保存策略（可选 ?type=backtest|screener）
  POST /api/jq/strategies          — 保存策略代码
  GET  /api/jq/strategies/{id}     — 获取单条策略
  PUT  /api/jq/strategies/{id}     — 更新策略代码
  DELETE /api/jq/strategies/{id}   — 删除策略
  GET  /api/jq/template            — 返回示例回测代码
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..jq.backtest import run_jq_backtest
from ..jq.executor import CodeValidationError, validate_strategy_code
from ..jq.screener_engine import run_screener_strategy
from ..jq.screener_templates import SCREENER_TEMPLATES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jq", tags=["jq-strategy"])


# ─────────────────────── 内存策略存储（轻量替代 DB） ───────────────────────

_strategies: dict[int, dict] = {}
_next_id = 1


def _new_strategy(name: str, code: str, description: str = "", stype: str = "backtest") -> dict:
    global _next_id
    now = datetime.utcnow().isoformat()
    s = {
        "id": _next_id,
        "name": name,
        "code": code,
        "description": description,
        "type": stype,  # "backtest" | "screener"
        "created_at": now,
        "updated_at": now,
    }
    _strategies[_next_id] = s
    _next_id += 1
    return s


# ─────────────────────── Schema ───────────────────────

class BacktestRequest(BaseModel):
    code: str
    start_date: str = "2022-01-01"
    end_date: str = "2024-12-31"
    initial_cash: float = 1_000_000.0


class ScreenerRunRequest(BaseModel):
    code: str
    universe: str = "all"
    max_workers: int = 8
    timeout_sec: int = 600
    limit: int = 0  # 0=不限；调试时可设小一点


class StrategyCreate(BaseModel):
    name: str
    code: str
    description: str = ""
    type: str = "backtest"


class StrategyUpdate(BaseModel):
    name: str | None = None
    code: str | None = None
    description: str | None = None
    type: str | None = None


# ─────────────────────── 示例回测策略 ───────────────────────

_TEMPLATE_CODE = '''\
# ── 双均线策略示例 ──────────────────────────────────────────
# 逻辑：MA5 上穿 MA20 买入；MA5 下穿 MA20 卖出
# 仓位：每只股票不超过总资产的 10%，最多同时持有 5 只

def initialize(context):
    # 自选股池（可换成 get_index_stocks 动态获取）
    context.stock_pool = [
        \'000001.XSHE\',  # 平安银行
        \'000858.XSHE\',  # 五粮液
        \'600519.XSHG\',  # 贵州茅台
        \'300750.XSHE\',  # 宁德时代
        \'601318.XSHG\',  # 中国平安
    ]
    set_benchmark(\'000300.XSHG\')
    set_commission(0.00025)   # 万2.5手续费


def handle_data(context, data):
    for security in context.stock_pool:
        # 取最近 30 日收盘价
        hist = attribute_history(security, 30, \'1d\', [\'close\'])
        if len(hist) < 20:
            continue

        close = hist[\'close\']
        ma5  = close.iloc[-5:].mean()
        ma20 = close.mean()
        ma5_prev  = close.iloc[-6:-1].mean()
        ma20_prev = close.iloc[-20:-1].mean() if len(close) >= 20 else ma20

        in_position = security in context.portfolio.positions

        # 金叉买入
        if ma5 > ma20 and ma5_prev <= ma20_prev and not in_position:
            # 控制仓位：不超过总资产 10%，且总持仓数 ≤ 5
            if len(context.portfolio.positions) < 5:
                order_target_percent(security, 0.1)
                log.info(f"买入 {security}，MA5={ma5:.2f} MA20={ma20:.2f}")

        # 死叉卖出
        elif ma5 < ma20 and ma5_prev >= ma20_prev and in_position:
            order_target_percent(security, 0)
            log.info(f"卖出 {security}，MA5={ma5:.2f} MA20={ma20:.2f}")
'''


# ─────────────────────── 回测路由 ───────────────────────

@router.get("/template")
async def get_template():
    """返回示例回测策略代码。"""
    return {"code": _TEMPLATE_CODE}


@router.post("/backtest")
async def backtest(req: BacktestRequest):
    """执行 JQ 风格回测（同步阻塞，通常 5~30 秒）。"""
    try:
        validate_strategy_code(req.code)
    except CodeValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None,
            run_jq_backtest,
            req.code,
            req.start_date,
            req.end_date,
            req.initial_cash,
        )
    except Exception as e:
        logger.exception("jq backtest error")
        raise HTTPException(status_code=500, detail=str(e))

    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])

    return result


# ─────────────────────── 筛选路由 ───────────────────────

@router.get("/screener/template")
async def get_screener_template():
    """返回默认筛选策略代码。"""
    return {"code": SCREENER_TEMPLATES["default"]["code"]}


@router.get("/screener/templates")
async def list_screener_templates():
    """列出所有内置筛选模板。"""
    return [
        {"key": k, "label": v["label"], "code": v["code"]}
        for k, v in SCREENER_TEMPLATES.items()
    ]


@router.post("/screener/run")
async def run_screener(req: ScreenerRunRequest):
    """执行用户筛选策略（同步阻塞，30s~5min）。"""
    try:
        validate_strategy_code(req.code)
    except CodeValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None,
            run_screener_strategy,
            req.code,
            req.universe,
            req.max_workers,
            req.timeout_sec,
            req.limit,
        )
    except Exception as e:
        logger.exception("screener strategy error")
        raise HTTPException(status_code=500, detail=str(e))

    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# ─────────────────────── 策略 CRUD ───────────────────────

@router.get("/strategies")
async def list_strategies(type: str | None = Query(None, description="backtest | screener，留空=全部")):
    items = list(_strategies.values())
    if type:
        items = [s for s in items if s.get("type", "backtest") == type]
    return sorted(items, key=lambda s: s["id"], reverse=True)


@router.post("/strategies")
async def create_strategy(body: StrategyCreate):
    try:
        validate_strategy_code(body.code)
    except CodeValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if body.type not in ("backtest", "screener"):
        raise HTTPException(status_code=400, detail="type 必须为 backtest 或 screener")
    s = _new_strategy(body.name, body.code, body.description, body.type)
    return s


@router.get("/strategies/{sid}")
async def get_strategy(sid: int):
    s = _strategies.get(sid)
    if not s:
        raise HTTPException(status_code=404, detail="策略不存在")
    return s


@router.put("/strategies/{sid}")
async def update_strategy(sid: int, body: StrategyUpdate):
    s = _strategies.get(sid)
    if not s:
        raise HTTPException(status_code=404, detail="策略不存在")
    if body.code is not None:
        try:
            validate_strategy_code(body.code)
        except CodeValidationError as e:
            raise HTTPException(status_code=400, detail=str(e))
        s["code"] = body.code
    if body.name is not None:
        s["name"] = body.name
    if body.description is not None:
        s["description"] = body.description
    if body.type is not None and body.type in ("backtest", "screener"):
        s["type"] = body.type
    s["updated_at"] = datetime.utcnow().isoformat()
    return s


@router.delete("/strategies/{sid}")
async def delete_strategy(sid: int):
    if sid not in _strategies:
        raise HTTPException(status_code=404, detail="策略不存在")
    del _strategies[sid]
    return {"ok": True}
