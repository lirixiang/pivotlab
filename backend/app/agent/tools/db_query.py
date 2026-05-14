"""query_db: read-only SQL against the pivotlab database.

Uses the shared AsyncSessionLocal from app.database — no separate engine.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

from app.agent.config import get_settings
from app.agent.security.sql_guard import assert_readonly, UnsafeSQLError
from app.agent.tools.registry import registry
from app.database import AsyncSessionLocal


@registry.register(
    name="query_db",
    description=(
        "Run a read-only SQL SELECT against the pivotlab PostgreSQL database. "
        "KEY tables (trade_date is varchar 'YYYYMMDD'): "
        "stocks(code, name, industry, market, is_st), "
        "daily_candles(code, trade_date, open, high, low, close, change_pct, turnover_rate, market_cap, amount) — NO name column, JOIN stocks, "
        "index_candles(code, trade_date, open, high, low, close, pct_change) — 指数K线(000001=上证,399001=深证,399006=创业板), "
        "concept_boards(board_code, concept, change_pct_1d, net_inflow, rank), "
        "stock_concepts(code, concept, board_code), "
        "concept_heat_history(trade_date, concept, change_pct, net_inflow, heat_score, heat_level, zt_count, leader_code, leader_name), "
        "zt_pool_daily(code, name, trade_date, pool_type, consecutive, seal_amount, zt_status, concept, industry), "
        "lhb_records(code, name, trade_date, reason, buy_total, sell_total, net_amount), "
        "lhb_seat_details(code, trade_date, rank, side, seat_name, buy_amount, sell_amount, is_known_hot, hot_money_tag), "
        "dragon_signals(code, name, trade_date, signal_type, dragon_rank, dragon_score, consecutive, entry_price, stop_price, target_price, market_cycle), "
        "recommendations(code, name, style, score, rank, reasons[json], scan_date, status), "
        "trade_plans(code, style, buy_low, buy_high, stop_loss, take_profit_1, take_profit_2, risk_reward, confidence), "
        "recommendation_outcomes(code, style, state, triggered_date, exit_date, realized_return_pct, max_favorable_pct, max_adverse_pct), "
        "financial_snapshots(code, eps_ttm, roe, revenue_yoy, net_profit_yoy, pe_ratio_ttm), "
        "financial_history(code, report_period, eps, roe, revenue, net_profit, revenue_yoy, net_profit_yoy), "
        "analyst_consensus(code, name, target_price_high, target_price_low, analyst_count, buy_count), "
        "scan_results(code, name, pattern, score, price, change_pct, detail[json], scanned_at). "
        "Use (SELECT MAX(trade_date) FROM daily_candles) for 'latest day'. Always add LIMIT."
    ),
    parameters={
        "type": "object",
        "properties": {
            "sql": {"type": "string", "description": "A single SELECT statement (PostgreSQL dialect)"},
        },
        "required": ["sql"],
    },
    permission="safe",
)
async def query_db(args: dict[str, Any]) -> dict[str, Any]:
    # Handle _raw fallback from malformed LLM JSON
    if "_raw" in args and "sql" not in args:
        import re
        m = re.search(r'"sql"\s*:\s*"((?:[^"\\]|\\.)*)"', args["_raw"])
        if m:
            sql = m.group(1).encode().decode('unicode_escape')
        else:
            return {"error": "无法解析 SQL 参数，LLM 返回了格式错误的 JSON"}
    else:
        sql = args["sql"]
    sql = sql.strip().rstrip(";")
    s = get_settings()
    try:
        assert_readonly(sql)
    except UnsafeSQLError as e:
        return {"error": str(e)}

    async with AsyncSessionLocal() as session:
        result = await session.execute(text(sql).execution_options(timeout=s.sql_query_timeout_sec))
        rows = result.mappings().fetchmany(s.sql_max_rows)
        truncated = len(rows) >= s.sql_max_rows
        return {
            "columns": list(result.keys()),
            "rows": [dict(r) for r in rows],
            "row_count": len(rows),
            "truncated": truncated,
        }
