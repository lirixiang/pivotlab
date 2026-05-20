"""筛选策略用户代码可调用的 API 函数集合。

类似 jq/api.py，但面向"对单只股票做形态判断"场景。
所有函数都接收 list[Candle] 或基础数值，不依赖账户状态。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..schemas import Candle, Level
from ..services.levels_multifactor import detect_levels_multifactor


# ─────────────────────── 日志器 ───────────────────────

class ScreenerLogger:
    """收集策略输出的日志，最终一并返回给前端。"""

    def __init__(self, logs: list[dict]):
        self._logs = logs
        self._scope = ""  # 当前正在处理的股票代码，便于追踪

    def _append(self, level: str, msg: str) -> None:
        self._logs.append({
            "level": level,
            "dt": self._scope or "",
            "msg": str(msg),
        })

    def info(self, msg): self._append("INFO", msg)
    def warn(self, msg): self._append("WARN", msg)
    def warning(self, msg): self._append("WARN", msg)
    def error(self, msg): self._append("ERROR", msg)
    def debug(self, msg): self._append("DEBUG", msg)


# ─────────────────────── 指标计算 ───────────────────────

def MA(values, n: int) -> float:
    """简单移动平均（取最后 n 个）。values 可以是 list/array/Series。"""
    arr = list(values)
    if len(arr) < n:
        return float("nan")
    return float(sum(arr[-n:]) / n)


def MA_series(values, n: int) -> list[float]:
    """完整移动平均序列（与 values 等长，前 n-1 个为 nan）。"""
    arr = np.asarray(list(values), dtype=float)
    if len(arr) < n:
        return [float("nan")] * len(arr)
    out = np.full(len(arr), np.nan)
    cumsum = np.cumsum(arr)
    out[n - 1] = cumsum[n - 1] / n
    out[n:] = (cumsum[n:] - cumsum[:-n]) / n
    return out.tolist()


def EMA_series(values, n: int) -> list[float]:
    """指数移动平均，与 values 等长。"""
    arr = np.asarray(list(values), dtype=float)
    if len(arr) == 0:
        return []
    alpha = 2.0 / (n + 1)
    out = np.empty(len(arr))
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = alpha * arr[i] + (1 - alpha) * out[i - 1]
    return out.tolist()


def MACD(closes, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """MACD 指标。返回 dict: {dif, dea, hist}，每个为等长 list。"""
    arr = list(closes)
    ema_fast = EMA_series(arr, fast)
    ema_slow = EMA_series(arr, slow)
    dif = [a - b for a, b in zip(ema_fast, ema_slow)]
    dea = EMA_series(dif, signal)
    hist = [(d - e) * 2 for d, e in zip(dif, dea)]
    return {"dif": dif, "dea": dea, "hist": hist}


def RSI(closes, n: int = 14) -> list[float]:
    """相对强弱指数序列。"""
    arr = np.asarray(list(closes), dtype=float)
    if len(arr) < n + 1:
        return [float("nan")] * len(arr)
    diff = np.diff(arr, prepend=arr[0])
    gain = np.where(diff > 0, diff, 0.0)
    loss = np.where(diff < 0, -diff, 0.0)
    avg_gain = np.zeros_like(arr)
    avg_loss = np.zeros_like(arr)
    avg_gain[n] = gain[1:n + 1].mean()
    avg_loss[n] = loss[1:n + 1].mean()
    for i in range(n + 1, len(arr)):
        avg_gain[i] = (avg_gain[i - 1] * (n - 1) + gain[i]) / n
        avg_loss[i] = (avg_loss[i - 1] * (n - 1) + loss[i]) / n
    rs = np.divide(avg_gain, avg_loss, out=np.zeros_like(arr), where=avg_loss != 0)
    rsi = 100 - 100 / (1 + rs)
    rsi[:n] = np.nan
    return rsi.tolist()


def volume_ratio(candles: list, window: int = 5) -> float:
    """量比：最新成交量 / 前 N 日平均成交量。"""
    if len(candles) < window + 1:
        return 0.0
    recent = candles[-1].volume
    prev = np.mean([c.volume for c in candles[-window - 1:-1]])
    return float(recent / prev) if prev > 0 else 0.0


def highest(values, n: int) -> float:
    arr = list(values)
    return float(max(arr[-n:])) if arr else float("nan")


def lowest(values, n: int) -> float:
    arr = list(values)
    return float(min(arr[-n:])) if arr else float("nan")


def crossover(a: list[float], b: list[float]) -> bool:
    """a 上穿 b（最后一根：a[-2] <= b[-2] 且 a[-1] > b[-1]）"""
    if len(a) < 2 or len(b) < 2:
        return False
    if math.isnan(a[-1]) or math.isnan(b[-1]) or math.isnan(a[-2]) or math.isnan(b[-2]):
        return False
    return a[-2] <= b[-2] and a[-1] > b[-1]


def crossunder(a: list[float], b: list[float]) -> bool:
    """a 下穿 b。"""
    if len(a) < 2 or len(b) < 2:
        return False
    if math.isnan(a[-1]) or math.isnan(b[-1]) or math.isnan(a[-2]) or math.isnan(b[-2]):
        return False
    return a[-2] >= b[-2] and a[-1] < b[-1]


# ─────────────────────── 支撑/压力位 ───────────────────────

def get_sr_levels(candles: list, lookback: int = 250) -> list[Level]:
    """计算支撑/压力位，复用 multifactor 检测器。"""
    if not candles:
        return []
    try:
        return detect_levels_multifactor(candles, lookback=min(len(candles), lookback))
    except Exception:
        return []


def nearest_support(levels: list[Level], price: float) -> Level | None:
    """返回当前价格下方最近的支撑位（最大的 sup.price < price）。"""
    supports = [lv for lv in levels if lv.kind == "support" and lv.price < price]
    return max(supports, key=lambda lv: lv.price) if supports else None


def nearest_resistance(levels: list[Level], price: float) -> Level | None:
    """返回当前价格上方最近的压力位（最小的 res.price > price）。"""
    resistances = [lv for lv in levels if lv.kind == "resistance" and lv.price > price]
    return min(resistances, key=lambda lv: lv.price) if resistances else None


def distance_pct(cur: float, target: float) -> float:
    """距离百分比：(cur - target) / target * 100"""
    if target == 0:
        return 0.0
    return (cur - target) / target * 100


# ─────────────────────── 提取常用字段 ───────────────────────

def closes_of(candles: list) -> list[float]:
    return [c.close for c in candles]


def highs_of(candles: list) -> list[float]:
    return [c.high for c in candles]


def lows_of(candles: list) -> list[float]:
    return [c.low for c in candles]


def volumes_of(candles: list) -> list[float]:
    return [c.volume for c in candles]


# ─────────────────────── 命名空间构建 ───────────────────────

def build_screener_namespace(logs: list[dict]) -> dict[str, Any]:
    """构建注入用户筛选策略代码的全局命名空间。

    Args:
        logs: 共享日志列表，log.info 等调用会写入此列表。

    Returns:
        dict 形式的全局命名空间。
    """
    logger = ScreenerLogger(logs)

    return {
        # 数据访问
        "get_sr_levels":        get_sr_levels,
        "nearest_support":      nearest_support,
        "nearest_resistance":   nearest_resistance,
        "distance_pct":         distance_pct,

        # 提取序列
        "closes_of":            closes_of,
        "highs_of":             highs_of,
        "lows_of":              lows_of,
        "volumes_of":           volumes_of,

        # 指标
        "MA":                   MA,
        "MA_series":            MA_series,
        "EMA_series":           EMA_series,
        "MACD":                 MACD,
        "RSI":                  RSI,
        "volume_ratio":         volume_ratio,
        "highest":              highest,
        "lowest":               lowest,
        "crossover":            crossover,
        "crossunder":           crossunder,

        # 日志
        "log":                  logger,
        "_screener_logger":     logger,  # 内部引用，便于 engine 在 filter 调用前设置 scope

        # 数学库
        "math":                 math,
        "np":                   np,
    }
