"""筛选策略执行引擎。

接收用户代码，遍历全市场（或指定股票池）调用 user_filter(context, code, name, candles, weekly)，
收集命中结果并返回。
"""
from __future__ import annotations

import logging
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from ..schemas import Candle
from ..services.data_provider import get_candles, list_universe
from .executor import CodeValidationError, validate_strategy_code
from .screener_api import build_screener_namespace

logger = logging.getLogger(__name__)


class ScreenerContext:
    """用户筛选策略可访问的 context 对象。

    用户可在 initialize(context) 中绑定任意属性：
        context.min_candles = 120
        context.my_param = 0.05
    """

    def __init__(self):
        self.name: str = ""
        self.description: str = ""
        self.min_candles: int = 120
        self.universe: str = "all"  # all / hs300 / 自定义代码列表


def _enrich_market(code: str) -> str:
    if code.startswith("6"):
        return "沪A"
    if code.startswith("0"):
        return "深A"
    if code.startswith("3"):
        return "创业板"
    if code.startswith("688"):
        return "科创板"
    return ""


def run_screener_strategy(
    code: str,
    universe: str = "all",
    max_workers: int = 8,
    timeout_sec: int = 600,
    limit: int = 0,  # 0 = 不限
) -> dict[str, Any]:
    """执行用户筛选策略，返回命中列表 + 日志 + 统计。

    Args:
        code: 用户 Python 代码字符串。需定义 filter(context, code, name, candles, weekly)。
        universe: 股票池范围。"all"=全主板；"hs300"=沪深300（暂未实现，先用全主板）。
        max_workers: 并发线程数。
        timeout_sec: 总超时秒数。
        limit: 限制扫描股票数（调试用，0=不限）。

    Returns:
        {
          "items": [...],          # 命中项 list[dict]
          "logs": [...],
          "stats": {...},
          "error": None or str,
        }
    """
    start_ts = time.time()
    logs: list[dict] = []
    items: list[dict] = []

    # ── 1. 代码安全验证 ──────────────────────────────
    try:
        validate_strategy_code(code)
    except CodeValidationError as e:
        return {"items": [], "logs": [], "stats": {}, "error": str(e)}

    # ── 2. 构建命名空间，exec 用户代码 ────────────────
    ns = build_screener_namespace(logs)
    try:
        exec(compile(code, "<screener_strategy>", "exec"), ns)
    except Exception as e:
        return {
            "items": [], "logs": [], "stats": {},
            "error": f"策略代码执行失败：{type(e).__name__}: {e}",
        }

    user_initialize = ns.get("initialize")
    user_filter = ns.get("filter")
    user_before = ns.get("before_scan")
    user_after = ns.get("after_scan")

    if not callable(user_filter):
        return {
            "items": [], "logs": [], "stats": {},
            "error": "策略代码必须定义 filter(context, code, name, candles, weekly) 函数",
        }

    # ── 3. 创建 context 并调用 initialize ────────────
    context = ScreenerContext()
    if callable(user_initialize):
        try:
            user_initialize(context)
        except Exception as e:
            return {
                "items": [], "logs": [], "stats": {},
                "error": f"initialize 执行失败：{type(e).__name__}: {e}",
            }

    # ── 4. 加载股票池 ──────────────────────────────
    try:
        from ..utils.markets import filter_main_board
        full_universe = list_universe()
        full_universe = filter_main_board(full_universe, key=lambda r: r[0])
    except Exception as e:
        return {"items": [], "logs": [], "stats": {}, "error": f"加载股票池失败：{e}"}

    if context.universe and context.universe != "all" and isinstance(context.universe, list):
        codes_set = set(context.universe)
        full_universe = [u for u in full_universe if u[0] in codes_set]

    if limit > 0:
        full_universe = full_universe[:limit]

    total = len(full_universe)
    logger.info("screener_strategy: scanning %d stocks", total)

    # ── 5. before_scan 钩子 ──────────────────────
    if callable(user_before):
        try:
            user_before(context)
        except Exception as e:
            logs.append({"level": "ERROR", "dt": "", "msg": f"before_scan 失败：{e}"})

    # ── 6. 并发遍历股票 ──────────────────────────
    user_logger = ns.get("_screener_logger")

    def process_one(stock_tuple) -> dict | None:
        stk_code, name, industry = stock_tuple
        try:
            candles = get_candles(stk_code, days=365)
            if not candles or len(candles) < context.min_candles:
                return None
            try:
                weekly = get_candles(stk_code, period="weekly", days=80) or None
            except Exception:
                weekly = None

            # 设置日志 scope（线程不安全，但日志只用作展示，足够）
            if user_logger is not None:
                user_logger._scope = stk_code

            result = user_filter(context, stk_code, name, candles, weekly)
            if not result:
                return None
            if not isinstance(result, dict):
                logs.append({
                    "level": "WARN", "dt": stk_code,
                    "msg": f"filter 返回值非 dict: {type(result).__name__}，已跳过",
                })
                return None

            cur = float(candles[-1].close)
            prev = float(candles[-2].close) if len(candles) >= 2 else cur
            change_pct = (cur - prev) / prev * 100 if prev > 0 else 0.0
            amount_wan = round(cur * candles[-1].volume / 10000, 1)

            item = {
                "code": stk_code,
                "name": name,
                "pattern": "custom",
                "score": float(result.get("score", 0)),
                "price": cur,
                "change_pct": change_pct,
                "volume_ratio": float(result.get("volume_ratio", 0)),
                "breakout_price": result.get("breakout_price"),
                "pullback_price": result.get("pullback_price"),
                "distance_to_support_pct": result.get("distance_to_support_pct"),
                "triggers": list(result.get("triggers", [])),
                "market": _enrich_market(stk_code),
                "industry": industry or "",
                "amount": amount_wan,
                "rr_ratio": float(result.get("rr_ratio", 0)),
                "support_score": float(result.get("support_score", 0)),
            }
            return item
        except Exception as e:
            logs.append({
                "level": "ERROR", "dt": stk_code,
                "msg": f"{type(e).__name__}: {e}",
            })
            return None

    if max_workers > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(process_one, st): st for st in full_universe}
            for fut in as_completed(futures, timeout=timeout_sec):
                try:
                    r = fut.result()
                except Exception as e:
                    logs.append({"level": "ERROR", "dt": "", "msg": f"任务异常：{e}"})
                    continue
                if r:
                    items.append(r)
                # 超时检查
                if time.time() - start_ts > timeout_sec:
                    logs.append({"level": "WARN", "dt": "", "msg": "超时，提前停止扫描"})
                    break
    else:
        for st in full_universe:
            r = process_one(st)
            if r:
                items.append(r)
            if time.time() - start_ts > timeout_sec:
                logs.append({"level": "WARN", "dt": "", "msg": "超时，提前停止扫描"})
                break

    # ── 7. after_scan 钩子（可改写 items 顺序/过滤） ──
    if callable(user_after):
        try:
            new_items = user_after(context, items)
            if isinstance(new_items, list):
                items = new_items
        except Exception as e:
            logs.append({"level": "ERROR", "dt": "", "msg": f"after_scan 失败：{e}"})

    # ── 8. 排序：按 score 降序 ──────────────────────
    items.sort(key=lambda x: x.get("score", 0), reverse=True)

    elapsed = round(time.time() - start_ts, 2)
    stats = {
        "total_scanned": total,
        "matched": len(items),
        "elapsed_sec": elapsed,
        "match_rate_pct": round(len(items) / total * 100, 2) if total > 0 else 0,
        "strategy_name": context.name or "未命名策略",
    }

    return {"items": items, "logs": logs, "stats": stats, "error": None}
