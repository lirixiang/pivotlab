"""量化系统 API (M1 + M2 + M3 + M4)

M1: CRUD
  GET    /api/quant/systems
  POST   /api/quant/systems
  GET    /api/quant/systems/{id}
  PUT    /api/quant/systems/{id}
  DELETE /api/quant/systems/{id}
  GET    /api/quant/defaults

M2: 单股信号试运行
  POST   /api/quant/systems/{id}/test

M3: 完整 Pipeline daily-run + 历史留痕
  POST   /api/quant/systems/{id}/run         触发一次 daily_run
  GET    /api/quant/systems/{id}/runs        历史 run 列表（summary）
  GET    /api/quant/runs/{run_id}            run 详情（含完整 orders/signals）
  DELETE /api/quant/runs/{run_id}            删除 run

M4: 历史回测
  POST   /api/quant/systems/{id}/backtest    跑一次回测（同步等返回）
  GET    /api/quant/systems/{id}/backtests   回测列表（summary）
  GET    /api/quant/backtests/{bid}          回测详情
  DELETE /api/quant/backtests/{bid}          删除回测

M5: 实盘交易日志
  GET    /api/quant/systems/{id}/positions   持仓列表（status=open|closed|all）
  POST   /api/quant/positions/from-order     从 run 的某个 order 创建持仓（标记已成交）
  POST   /api/quant/positions/manual         手动新建持仓
  POST   /api/quant/positions/{pid}/close    手动平仓
  PUT    /api/quant/positions/{pid}          编辑（如调整 stop_price）
  DELETE /api/quant/positions/{pid}          删除（误操作回滚）
  GET    /api/quant/systems/{id}/trades      已平仓交易（按时间 desc）
  POST   /api/quant/systems/{id}/nav/snapshot  按日抓快照
  GET    /api/quant/systems/{id}/nav         净值序列（区间）
  GET    /api/quant/systems/{id}/journal/summary  汇总指标
"""
import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import AsyncSessionLocal
from . import defaults
from .models import QuantBacktest, QuantNavDaily, QuantPosition, QuantSystem, QuantSystemRun, QuantTemplate
from .pipeline.backtest import run_backtest, run_single_stock_backtest
from .pipeline.context import load_stock_context
from .pipeline.runner import daily_run
from .pipeline.signal import evaluate_signal
from .schemas import SystemCreate, SystemOut, SystemSummary, SystemUpdate

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/quant", tags=["quant"])


@router.get("/defaults")
async def get_defaults():
    """返回默认模板（Stage 2 趋势跟随），供前端"新建"时预填。"""
    return defaults.STAGE2_TREND_FOLLOWING


@router.get("/templates")
async def get_templates():
    """返回所有策略模板列表：内置 + 用户自定义。"""
    builtin = [
        {**v, "builtin": True} for v in defaults.TEMPLATES.values()
    ]
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(QuantTemplate).order_by(QuantTemplate.created_at.desc())
        )
        user_tpls = [
            {
                "key": t.key,
                "name": t.name,
                "emoji": t.emoji,
                "desc": t.desc,
                "tags": t.tags or [],
                "config": t.config or {},
                "builtin": False,
                "id": t.id,
            }
            for t in result.scalars().all()
        ]
    return builtin + user_tpls


@router.post("/templates")
async def create_template(body: dict):
    """保存一个用户自定义模板（从现有系统的配置保存）。"""
    import re
    import time

    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(400, "name is required")

    key = body.get("key") or re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or f"tpl_{int(time.time())}"

    # 不允许覆盖内置 key
    if key in defaults.TEMPLATES:
        key = f"user_{key}"

    async with AsyncSessionLocal() as session:
        existing = await session.execute(select(QuantTemplate).where(QuantTemplate.key == key))
        if existing.scalars().first():
            raise HTTPException(409, f"模板 key「{key}」已存在")

        tpl = QuantTemplate(
            key=key,
            name=name,
            emoji=body.get("emoji", "📋"),
            desc=body.get("desc", ""),
            tags=body.get("tags", []),
            config=body.get("config", {}),
        )
        session.add(tpl)
        await session.commit()
        await session.refresh(tpl)
        return {
            "id": tpl.id, "key": tpl.key, "name": tpl.name,
            "emoji": tpl.emoji, "desc": tpl.desc, "tags": tpl.tags,
            "config": tpl.config, "builtin": False,
        }


@router.put("/templates/{tpl_id}")
async def update_template(tpl_id: int, body: dict):
    """更新用户自定义模板。"""
    async with AsyncSessionLocal() as session:
        tpl = await session.get(QuantTemplate, tpl_id)
        if not tpl:
            raise HTTPException(404, "template not found")
        for field in ("name", "emoji", "desc", "tags", "config"):
            if field in body:
                setattr(tpl, field, body[field])
        await session.commit()
        await session.refresh(tpl)
        return {
            "id": tpl.id, "key": tpl.key, "name": tpl.name,
            "emoji": tpl.emoji, "desc": tpl.desc, "tags": tpl.tags,
            "config": tpl.config, "builtin": False,
        }


@router.delete("/templates/{tpl_id}")
async def delete_template(tpl_id: int):
    """删除用户自定义模板。"""
    async with AsyncSessionLocal() as session:
        tpl = await session.get(QuantTemplate, tpl_id)
        if not tpl:
            raise HTTPException(404, "template not found")
        await session.delete(tpl)
        await session.commit()
        return {"ok": True}


@router.get("/systems", response_model=list[SystemSummary])
async def list_systems():
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(QuantSystem).order_by(QuantSystem.updated_at.desc())
        )
        return result.scalars().all()


@router.post("/systems", response_model=SystemOut)
async def create_system(body: SystemCreate):
    """新建。任意未提供字段使用 Stage 2 默认值。"""
    tpl = defaults.make_default(name=body.name or "我的交易系统")

    def pick(field: str, fallback):
        v = getattr(body, field, None)
        return v if v is not None else fallback

    sys = QuantSystem(
        name=tpl["name"] if body.name is None else body.name,
        description=pick("description", tpl["description"]),
        status=pick("status", tpl["status"]),
        universe_cfg=pick("universe_cfg", tpl["universe_cfg"]),
        signal_cfg=pick("signal_cfg", tpl["signal_cfg"]),
        risk_cfg=pick("risk_cfg", tpl["risk_cfg"]),
        exec_cfg=pick("exec_cfg", tpl["exec_cfg"]),
        initial_capital=pick("initial_capital", tpl["initial_capital"]),
    )
    async with AsyncSessionLocal() as session:
        session.add(sys)
        await session.commit()
        await session.refresh(sys)
        return sys


@router.get("/systems/{system_id}", response_model=SystemOut)
async def get_system(system_id: int):
    async with AsyncSessionLocal() as session:
        sys = await session.get(QuantSystem, system_id)
        if not sys:
            raise HTTPException(404, "system not found")
        return sys


@router.put("/systems/{system_id}", response_model=SystemOut)
async def update_system(system_id: int, body: SystemUpdate):
    async with AsyncSessionLocal() as session:
        sys = await session.get(QuantSystem, system_id)
        if not sys:
            raise HTTPException(404, "system not found")
        for field, value in body.model_dump(exclude_unset=True).items():
            setattr(sys, field, value)
        await session.commit()
        await session.refresh(sys)
        return sys


@router.delete("/systems/{system_id}")
async def delete_system(system_id: int):
    async with AsyncSessionLocal() as session:
        sys = await session.get(QuantSystem, system_id)
        if not sys:
            raise HTTPException(404, "system not found")
        await session.delete(sys)
        await session.commit()
        return {"ok": True, "id": system_id}


# ── M2: 单股信号试运行 ─────────────────────────────────────────────

class TestRunBody(BaseModel):
    code: str
    date: str | None = None        # YYYY-MM-DD，None=最新
    lookback: int = 300


@router.post("/systems/{system_id}/test")
async def test_signal(system_id: int, body: TestRunBody):
    """对某只股票（可选某日）跑一次该系统的信号层。

    返回每条买/卖规则的命中情况、在该 bar 的数值、以及最终是否触发。
    """
    async with AsyncSessionLocal() as session:
        sys = await session.get(QuantSystem, system_id)
        if not sys:
            raise HTTPException(404, "system not found")
        signal_cfg = sys.signal_cfg or {}

    def _run():
        ctx = load_stock_context(body.code, end_date=body.date, lookback=body.lookback)
        if ctx is None:
            return None
        report = evaluate_signal(signal_cfg, ctx.as_dict(), body.code, ctx.last_date)
        # 顺便附上最后一根 bar 的快照，便于前端展示"测试日 OHLC"
        snapshot = {
            "open": float(ctx.open[-1]),
            "high": float(ctx.high[-1]),
            "low": float(ctx.low[-1]),
            "close": float(ctx.close[-1]),
            "vol": float(ctx.vol[-1]),
            "bars": len(ctx.dates),
        }
        return {"snapshot": snapshot, **report.to_jsonable()}

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _run)
    if result is None:
        raise HTTPException(404, f"no candle data for {body.code}")
    return result


# ── M3: 完整 Pipeline + 历史留痕 ───────────────────────────────────

class DailyRunBody(BaseModel):
    end_date: str | None = None   # YYYY-MM-DD；None=今天


@router.post("/systems/{system_id}/run")
async def run_daily(system_id: int, body: DailyRunBody | None = None):
    """触发一次完整 Pipeline 跑一遍（universe → signal → risk → orders），
    结果写入 quant_system_runs。会读取该 system 当前 open 持仓，
    持仓中的 code 不再进入买入候选，且会单独检查是否触发卖出 / 止损。"""
    body = body or DailyRunBody()
    async with AsyncSessionLocal() as session:
        sys = await session.get(QuantSystem, system_id)
        if not sys:
            raise HTTPException(404, "system not found")
        sys_snapshot = sys
        # 拉当前 open 持仓
        pos_rs = await session.execute(
            select(QuantPosition).where(
                QuantPosition.system_id == system_id,
                QuantPosition.status == "open",
            )
        )
        open_positions = [
            {
                "id": p.id,
                "code": p.code,
                "name": p.name,
                "qty": p.qty,
                "entry_price": p.entry_price,
                "entry_date": p.entry_date,
                "stop_price": p.stop_price,
                "cost_basis": p.cost_basis,
            }
            for p in pos_rs.scalars().all()
        ]

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: daily_run(sys_snapshot, end_date=body.end_date, open_positions=open_positions),
        )
    except Exception as e:
        logger.exception("daily_run failed")
        raise HTTPException(500, f"pipeline failed: {e}")

    # 写入 run 记录
    async with AsyncSessionLocal() as session:
        # 再次确认 system 仍存在（防止并发删除导致 FK 错误）
        still = await session.get(QuantSystem, system_id)
        if not still:
            raise HTTPException(404, "system not found (deleted during run?)")
        run = QuantSystemRun(
            system_id=system_id,
            run_type="live_daily",
            trade_date=result["trade_date"],
            universe_count=result["universe_count"],
            signal_count=result["signal_count"],
            order_count=result["order_count"],
            candidates=result["candidates"],
            signals=result["signals"],
            orders=result["orders"],
            metrics=result["metrics"],
            duration_ms=result["duration_ms"],
            error=result.get("error", ""),
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)

    return {"run_id": run.id, **result}


@router.get("/systems/{system_id}/runs")
async def list_runs(system_id: int, limit: int = 20):
    async with AsyncSessionLocal() as session:
        sys = await session.get(QuantSystem, system_id)
        if not sys:
            raise HTTPException(404, "system not found")
        result = await session.execute(
            select(QuantSystemRun)
            .where(QuantSystemRun.system_id == system_id)
            .order_by(QuantSystemRun.created_at.desc())
            .limit(limit)
        )
        runs = result.scalars().all()
        return [
            {
                "id": r.id,
                "run_type": r.run_type,
                "trade_date": r.trade_date,
                "universe_count": r.universe_count,
                "signal_count": r.signal_count,
                "order_count": r.order_count,
                "duration_ms": r.duration_ms,
                "metrics": r.metrics,
                "created_at": r.created_at.isoformat(),
                "error": r.error,
            }
            for r in runs
        ]


@router.get("/runs/{run_id}")
async def get_run(run_id: int):
    async with AsyncSessionLocal() as session:
        run = await session.get(QuantSystemRun, run_id)
        if not run:
            raise HTTPException(404, "run not found")
        return {
            "id": run.id,
            "system_id": run.system_id,
            "run_type": run.run_type,
            "trade_date": run.trade_date,
            "universe_count": run.universe_count,
            "signal_count": run.signal_count,
            "order_count": run.order_count,
            "candidates": run.candidates,
            "signals": run.signals,
            "orders": run.orders,
            "metrics": run.metrics,
            "duration_ms": run.duration_ms,
            "error": run.error,
            "created_at": run.created_at.isoformat(),
        }


@router.delete("/runs/{run_id}")
async def delete_run(run_id: int):
    async with AsyncSessionLocal() as session:
        run = await session.get(QuantSystemRun, run_id)
        if not run:
            raise HTTPException(404, "run not found")
        await session.delete(run)
        await session.commit()
        return {"ok": True, "id": run_id}


# ── M4: 历史回测 ───────────────────────────────────────────────

class BacktestBody(BaseModel):
    start_date: str                       # YYYY-MM-DD
    end_date: str                         # YYYY-MM-DD
    name: str | None = None
    commission_bps: float = 2.5           # 0.025% 单边
    slippage_bps: float = 5.0             # 0.05% 单边
    initial_capital: float | None = None  # None=用系统配置


@router.post("/systems/{system_id}/backtest")
async def run_system_backtest(system_id: int, body: BacktestBody):
    """跑一次历史回测（同步等返回，可能 30s ~ 数分钟）。"""
    async with AsyncSessionLocal() as session:
        sys = await session.get(QuantSystem, system_id)
        if not sys:
            raise HTTPException(404, "system not found")

        # 允许临时覆盖初始资金
        if body.initial_capital is not None and body.initial_capital > 0:
            sys.initial_capital = float(body.initial_capital)

        # 拍快照
        snapshot_sys = sys
        system_snapshot = {
            "name": sys.name,
            "universe_cfg": sys.universe_cfg,
            "signal_cfg": sys.signal_cfg,
            "risk_cfg": sys.risk_cfg,
            "exec_cfg": sys.exec_cfg,
            "initial_capital": float(sys.initial_capital or 1000000.0),
        }

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: run_backtest(
                snapshot_sys,
                body.start_date,
                body.end_date,
                commission_bps=body.commission_bps,
                slippage_bps=body.slippage_bps,
            ),
        )
    except Exception as e:
        logger.exception("run_backtest failed")
        raise HTTPException(500, f"backtest failed: {e}")

    async with AsyncSessionLocal() as session:
        bt = QuantBacktest(
            system_id=system_id,
            name=body.name or f"{body.start_date}~{body.end_date}",
            start_date=body.start_date,
            end_date=body.end_date,
            initial_capital=float(system_snapshot["initial_capital"]),
            system_snapshot=system_snapshot,
            params={
                "commission_bps": body.commission_bps,
                "slippage_bps": body.slippage_bps,
                "fill_price_mode": "close",
            },
            status="done" if not result.get("error") else "failed",
            equity_curve=result["equity_curve"],
            trades=result["trades"],
            positions_end=result["positions_end"],
            metrics=result["metrics"],
            trading_days=result["trading_days"],
            duration_ms=result["duration_ms"],
            error=result.get("error", ""),
        )
        session.add(bt)
        await session.commit()
        await session.refresh(bt)

    return {
        "backtest_id": bt.id,
        "system_id": system_id,
        "name": bt.name,
        "status": bt.status,
        **result,
    }


class SingleStockBacktestBody(BaseModel):
    code: str
    start_date: str
    end_date: str
    commission_bps: float = 2.5
    slippage_bps: float = 5.0


@router.post("/systems/{system_id}/backtest-stock")
async def run_single_stock_bt(system_id: int, body: SingleStockBacktestBody):
    """对单只股票执行回测（K线页用，轻量快速）。"""
    async with AsyncSessionLocal() as session:
        sys = await session.get(QuantSystem, system_id)
        if not sys:
            raise HTTPException(404, "system not found")
        snapshot_sys = sys

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: run_single_stock_backtest(
                snapshot_sys,
                body.code,
                body.start_date,
                body.end_date,
                commission_bps=body.commission_bps,
                slippage_bps=body.slippage_bps,
            ),
        )
    except Exception as e:
        logger.exception("single stock backtest failed")
        raise HTTPException(500, f"backtest failed: {e}")

    return {
        "system_id": system_id,
        "code": body.code,
        **result,
    }


@router.get("/systems/{system_id}/backtests")
async def list_backtests(system_id: int, limit: int = 30):
    async with AsyncSessionLocal() as session:
        sys = await session.get(QuantSystem, system_id)
        if not sys:
            raise HTTPException(404, "system not found")
        rs = await session.execute(
            select(QuantBacktest)
            .where(QuantBacktest.system_id == system_id)
            .order_by(QuantBacktest.created_at.desc())
            .limit(limit)
        )
        rows = rs.scalars().all()
        return [
            {
                "id": b.id,
                "name": b.name,
                "start_date": b.start_date,
                "end_date": b.end_date,
                "initial_capital": b.initial_capital,
                "status": b.status,
                "trading_days": b.trading_days,
                "metrics": b.metrics,
                "duration_ms": b.duration_ms,
                "error": b.error,
                "created_at": b.created_at.isoformat(),
            }
            for b in rows
        ]


@router.get("/backtests/{bid}")
async def get_backtest(bid: int):
    async with AsyncSessionLocal() as session:
        b = await session.get(QuantBacktest, bid)
        if not b:
            raise HTTPException(404, "backtest not found")
        return {
            "id": b.id,
            "system_id": b.system_id,
            "name": b.name,
            "start_date": b.start_date,
            "end_date": b.end_date,
            "initial_capital": b.initial_capital,
            "system_snapshot": b.system_snapshot,
            "params": b.params,
            "status": b.status,
            "equity_curve": b.equity_curve,
            "trades": b.trades,
            "positions_end": b.positions_end,
            "metrics": b.metrics,
            "trading_days": b.trading_days,
            "duration_ms": b.duration_ms,
            "error": b.error,
            "created_at": b.created_at.isoformat(),
        }


@router.delete("/backtests/{bid}")
async def delete_backtest(bid: int):
    async with AsyncSessionLocal() as session:
        b = await session.get(QuantBacktest, bid)
        if not b:
            raise HTTPException(404, "backtest not found")
        await session.delete(b)
        await session.commit()
        return {"ok": True, "id": bid}


# ── M5: 实盘交易日志 ──────────────────────────────────────────────

def _position_to_dict(p: QuantPosition) -> dict:
    return {
        "id": p.id,
        "system_id": p.system_id,
        "code": p.code,
        "name": p.name,
        "qty": p.qty,
        "entry_price": p.entry_price,
        "entry_date": p.entry_date,
        "stop_price": p.stop_price,
        "cost_basis": p.cost_basis,
        "commission_in": p.commission_in,
        "status": p.status,
        "exit_price": p.exit_price,
        "exit_date": p.exit_date,
        "exit_reason": p.exit_reason,
        "commission_out": p.commission_out,
        "pnl": p.pnl,
        "pnl_pct": p.pnl_pct,
        "hold_days": p.hold_days,
        "source_run_id": p.source_run_id,
        "source_order_index": p.source_order_index,
        "notes": p.notes,
        "created_at": p.created_at.isoformat(),
        "updated_at": p.updated_at.isoformat(),
    }


@router.get("/systems/{system_id}/positions")
async def list_positions(system_id: int, status: str = "open"):
    async with AsyncSessionLocal() as session:
        sys = await session.get(QuantSystem, system_id)
        if not sys:
            raise HTTPException(404, "system not found")
        stmt = select(QuantPosition).where(QuantPosition.system_id == system_id)
        if status in ("open", "closed"):
            stmt = stmt.where(QuantPosition.status == status)
        stmt = stmt.order_by(QuantPosition.created_at.desc())
        rs = await session.execute(stmt)
        return [_position_to_dict(p) for p in rs.scalars().all()]


class FromOrderBody(BaseModel):
    run_id: int
    order_index: int
    actual_price: float | None = None
    actual_qty: int | None = None
    commission: float = 0.0
    notes: str = ""


@router.post("/positions/from-order")
async def create_position_from_order(body: FromOrderBody):
    """从 quant_system_runs.orders[order_index] 创建一个 open 持仓。"""
    async with AsyncSessionLocal() as session:
        run = await session.get(QuantSystemRun, body.run_id)
        if not run:
            raise HTTPException(404, "run not found")
        if body.order_index < 0 or body.order_index >= len(run.orders or []):
            raise HTTPException(400, "order_index out of range")
        order = run.orders[body.order_index]
        if order.get("rejected"):
            raise HTTPException(400, "this order was rejected by risk control")

        price = float(body.actual_price if body.actual_price is not None else order["price"])
        qty = int(body.actual_qty if body.actual_qty is not None else order["qty"])
        if qty <= 0 or price <= 0:
            raise HTTPException(400, "invalid qty/price")

        cost_basis = qty * price + float(body.commission)
        pos = QuantPosition(
            system_id=run.system_id,
            code=order["code"],
            name=order["name"],
            qty=qty,
            entry_price=price,
            entry_date=run.trade_date,
            stop_price=float(order.get("stop_price") or 0.0),
            cost_basis=cost_basis,
            commission_in=float(body.commission),
            status="open",
            source_run_id=run.id,
            source_order_index=body.order_index,
            notes=body.notes,
        )
        session.add(pos)
        await session.commit()
        await session.refresh(pos)
        return _position_to_dict(pos)


class ManualPositionBody(BaseModel):
    system_id: int
    code: str
    name: str = ""
    qty: int
    entry_price: float
    entry_date: str            # YYYY-MM-DD
    stop_price: float = 0.0
    commission: float = 0.0
    notes: str = ""


@router.post("/positions/manual")
async def create_position_manual(body: ManualPositionBody):
    async with AsyncSessionLocal() as session:
        sys = await session.get(QuantSystem, body.system_id)
        if not sys:
            raise HTTPException(404, "system not found")
        if body.qty <= 0 or body.entry_price <= 0:
            raise HTTPException(400, "invalid qty/price")
        cost_basis = body.qty * body.entry_price + body.commission
        pos = QuantPosition(
            system_id=body.system_id,
            code=body.code,
            name=body.name,
            qty=body.qty,
            entry_price=body.entry_price,
            entry_date=body.entry_date,
            stop_price=body.stop_price,
            cost_basis=cost_basis,
            commission_in=body.commission,
            status="open",
            notes=body.notes,
        )
        session.add(pos)
        await session.commit()
        await session.refresh(pos)
        return _position_to_dict(pos)


class ClosePositionBody(BaseModel):
    exit_price: float
    exit_date: str
    exit_reason: str = ""
    commission: float = 0.0


@router.post("/positions/{pid}/close")
async def close_position(pid: int, body: ClosePositionBody):
    async with AsyncSessionLocal() as session:
        pos = await session.get(QuantPosition, pid)
        if not pos:
            raise HTTPException(404, "position not found")
        if pos.status != "open":
            raise HTTPException(400, "position already closed")
        if body.exit_price <= 0:
            raise HTTPException(400, "invalid exit_price")

        gross = pos.qty * body.exit_price
        net_proceeds = gross - body.commission
        pnl = net_proceeds - pos.cost_basis
        pnl_pct = (pnl / pos.cost_basis * 100) if pos.cost_basis > 0 else 0.0
        # 持有天数（按日历日；前端可基于实际交易日校准）
        try:
            from datetime import date as _date
            hold = (_date.fromisoformat(body.exit_date) - _date.fromisoformat(pos.entry_date)).days
        except Exception:
            hold = 0

        pos.status = "closed"
        pos.exit_price = body.exit_price
        pos.exit_date = body.exit_date
        pos.exit_reason = body.exit_reason
        pos.commission_out = body.commission
        pos.pnl = round(pnl, 2)
        pos.pnl_pct = round(pnl_pct, 2)
        pos.hold_days = max(0, hold)
        await session.commit()
        await session.refresh(pos)
        return _position_to_dict(pos)


class EditPositionBody(BaseModel):
    stop_price: float | None = None
    notes: str | None = None


@router.put("/positions/{pid}")
async def edit_position(pid: int, body: EditPositionBody):
    async with AsyncSessionLocal() as session:
        pos = await session.get(QuantPosition, pid)
        if not pos:
            raise HTTPException(404, "position not found")
        if body.stop_price is not None:
            pos.stop_price = float(body.stop_price)
        if body.notes is not None:
            pos.notes = body.notes
        await session.commit()
        await session.refresh(pos)
        return _position_to_dict(pos)


@router.delete("/positions/{pid}")
async def delete_position(pid: int):
    async with AsyncSessionLocal() as session:
        pos = await session.get(QuantPosition, pid)
        if not pos:
            raise HTTPException(404, "position not found")
        await session.delete(pos)
        await session.commit()
        return {"ok": True, "id": pid}


@router.get("/systems/{system_id}/trades")
async def list_trades(system_id: int, limit: int = 200):
    """已平仓交易明细，按 exit_date desc 排序。"""
    async with AsyncSessionLocal() as session:
        sys = await session.get(QuantSystem, system_id)
        if not sys:
            raise HTTPException(404, "system not found")
        rs = await session.execute(
            select(QuantPosition)
            .where(
                QuantPosition.system_id == system_id,
                QuantPosition.status == "closed",
            )
            .order_by(QuantPosition.exit_date.desc(), QuantPosition.id.desc())
            .limit(limit)
        )
        return [_position_to_dict(p) for p in rs.scalars().all()]


class NavSnapshotBody(BaseModel):
    trade_date: str | None = None     # None=今天


@router.post("/systems/{system_id}/nav/snapshot")
async def snapshot_nav(system_id: int, body: NavSnapshotBody | None = None):
    """读取当前 open 持仓，用指定日期的收盘价 mark-to-market，写入 quant_nav_daily。"""
    from datetime import date as _date
    body = body or NavSnapshotBody()
    trade_date = body.trade_date or _date.today().strftime("%Y-%m-%d")

    async with AsyncSessionLocal() as session:
        sys = await session.get(QuantSystem, system_id)
        if not sys:
            raise HTTPException(404, "system not found")
        initial_capital = float(sys.initial_capital or 1000000.0)

        # 已平仓累计盈亏
        rs_closed = await session.execute(
            select(QuantPosition).where(
                QuantPosition.system_id == system_id,
                QuantPosition.status == "closed",
                QuantPosition.exit_date <= trade_date,
            )
        )
        realized = sum(p.pnl for p in rs_closed.scalars().all())

        # 当前 open 持仓
        rs_open = await session.execute(
            select(QuantPosition).where(
                QuantPosition.system_id == system_id,
                QuantPosition.status == "open",
                QuantPosition.entry_date <= trade_date,
            )
        )
        positions = list(rs_open.scalars().all())

    # 拉收盘价 mark-to-market（在 executor 跑同步引擎）
    def _markto():
        from .pipeline.universe import _get_engine, _load_candles_bulk
        if not positions:
            return [], 0.0
        eng = _get_engine()
        with Session(eng) as ssess:
            cm = _load_candles_bulk(
                ssess, [p.code for p in positions], trade_date, 5
            )
        snap, mv = [], 0.0
        for p in positions:
            rows = cm.get(p.code, [])
            rows = [r for r in rows if r.trade_date <= trade_date]
            last_px = float(rows[-1].close) if rows else float(p.entry_price)
            value = p.qty * last_px
            mv += value
            unreal = value - p.cost_basis
            snap.append({
                "code": p.code,
                "name": p.name,
                "qty": p.qty,
                "entry_price": p.entry_price,
                "last_price": round(last_px, 3),
                "market_value": round(value, 2),
                "unrealized_pnl": round(unreal, 2),
                "unrealized_pnl_pct": (
                    round(unreal / p.cost_basis * 100, 2) if p.cost_basis > 0 else 0.0
                ),
            })
        return snap, mv

    loop = asyncio.get_running_loop()
    snapshot, positions_value = await loop.run_in_executor(None, _markto)

    # 现金 = 初始 + 已实现 - 当前持仓 cost_basis
    cost_open = sum(p.cost_basis for p in positions)
    cash = initial_capital + realized - cost_open
    equity = cash + positions_value
    unrealized = positions_value - cost_open

    # peak / drawdown：基于历史 nav
    async with AsyncSessionLocal() as session:
        rs_hist = await session.execute(
            select(QuantNavDaily.equity)
            .where(
                QuantNavDaily.system_id == system_id,
                QuantNavDaily.trade_date < trade_date,
            )
        )
        equities_hist = [e for (e,) in rs_hist.all()]
        peak = max([initial_capital] + equities_hist + [equity])
        dd_pct = (peak - equity) / peak * 100 if peak > 0 else 0.0

        # upsert
        existing = await session.execute(
            select(QuantNavDaily).where(
                QuantNavDaily.system_id == system_id,
                QuantNavDaily.trade_date == trade_date,
            )
        )
        row = existing.scalar_one_or_none()
        if row is None:
            row = QuantNavDaily(
                system_id=system_id,
                trade_date=trade_date,
                cash=round(cash, 2),
                positions_value=round(positions_value, 2),
                equity=round(equity, 2),
                n_positions=len(positions),
                realized_pnl_total=round(realized, 2),
                unrealized_pnl=round(unrealized, 2),
                drawdown_pct=round(dd_pct, 2),
                snapshot=snapshot,
            )
            session.add(row)
        else:
            row.cash = round(cash, 2)
            row.positions_value = round(positions_value, 2)
            row.equity = round(equity, 2)
            row.n_positions = len(positions)
            row.realized_pnl_total = round(realized, 2)
            row.unrealized_pnl = round(unrealized, 2)
            row.drawdown_pct = round(dd_pct, 2)
            row.snapshot = snapshot
        await session.commit()
        await session.refresh(row)
        return {
            "id": row.id,
            "trade_date": row.trade_date,
            "cash": row.cash,
            "positions_value": row.positions_value,
            "equity": row.equity,
            "n_positions": row.n_positions,
            "realized_pnl_total": row.realized_pnl_total,
            "unrealized_pnl": row.unrealized_pnl,
            "drawdown_pct": row.drawdown_pct,
            "snapshot": row.snapshot,
        }


@router.get("/systems/{system_id}/nav")
async def list_nav(
    system_id: int,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = 500,
):
    async with AsyncSessionLocal() as session:
        sys = await session.get(QuantSystem, system_id)
        if not sys:
            raise HTTPException(404, "system not found")
        stmt = select(QuantNavDaily).where(QuantNavDaily.system_id == system_id)
        if from_date:
            stmt = stmt.where(QuantNavDaily.trade_date >= from_date)
        if to_date:
            stmt = stmt.where(QuantNavDaily.trade_date <= to_date)
        stmt = stmt.order_by(QuantNavDaily.trade_date.asc()).limit(limit)
        rs = await session.execute(stmt)
        return [
            {
                "trade_date": r.trade_date,
                "cash": r.cash,
                "positions_value": r.positions_value,
                "equity": r.equity,
                "n_positions": r.n_positions,
                "realized_pnl_total": r.realized_pnl_total,
                "unrealized_pnl": r.unrealized_pnl,
                "drawdown_pct": r.drawdown_pct,
            }
            for r in rs.scalars().all()
        ]


@router.get("/systems/{system_id}/journal/summary")
async def journal_summary(system_id: int):
    """汇总指标：open positions count/cost、closed pnl 统计、最近 nav。"""
    async with AsyncSessionLocal() as session:
        sys = await session.get(QuantSystem, system_id)
        if not sys:
            raise HTTPException(404, "system not found")

        initial_capital = float(sys.initial_capital or 1000000.0)

        rs_open = await session.execute(
            select(QuantPosition).where(
                QuantPosition.system_id == system_id,
                QuantPosition.status == "open",
            )
        )
        open_positions = list(rs_open.scalars().all())
        cost_open = sum(p.cost_basis for p in open_positions)

        rs_closed = await session.execute(
            select(QuantPosition).where(
                QuantPosition.system_id == system_id,
                QuantPosition.status == "closed",
            )
        )
        closed = list(rs_closed.scalars().all())
        realized = sum(p.pnl for p in closed)
        win = [p for p in closed if p.pnl > 0]
        loss = [p for p in closed if p.pnl <= 0]
        win_rate = (len(win) / len(closed) * 100) if closed else 0.0
        sum_win = sum(p.pnl for p in win)
        sum_loss = abs(sum(p.pnl for p in loss))
        pf = sum_win / sum_loss if sum_loss > 0 else (999.99 if sum_win > 0 else 0.0)
        avg_win = (sum(p.pnl_pct for p in win) / len(win)) if win else 0.0
        avg_loss = (sum(p.pnl_pct for p in loss) / len(loss)) if loss else 0.0
        avg_hold = (sum(p.hold_days for p in closed) / len(closed)) if closed else 0.0

        rs_nav = await session.execute(
            select(QuantNavDaily)
            .where(QuantNavDaily.system_id == system_id)
            .order_by(QuantNavDaily.trade_date.desc())
            .limit(1)
        )
        latest_nav = rs_nav.scalar_one_or_none()

        return {
            "initial_capital": initial_capital,
            "open_count": len(open_positions),
            "open_cost": round(cost_open, 2),
            "closed_count": len(closed),
            "realized_pnl_total": round(realized, 2),
            "realized_pnl_pct": round(realized / initial_capital * 100, 2) if initial_capital else 0.0,
            "win_count": len(win),
            "loss_count": len(loss),
            "win_rate_pct": round(win_rate, 2),
            "profit_factor": round(pf, 2),
            "avg_win_pct": round(avg_win, 2),
            "avg_loss_pct": round(avg_loss, 2),
            "avg_hold_days": round(avg_hold, 1),
            "latest_nav": (
                {
                    "trade_date": latest_nav.trade_date,
                    "cash": latest_nav.cash,
                    "positions_value": latest_nav.positions_value,
                    "equity": latest_nav.equity,
                    "drawdown_pct": latest_nav.drawdown_pct,
                    "n_positions": latest_nav.n_positions,
                }
                if latest_nav
                else None
            ),
        }
