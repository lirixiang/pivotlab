"""DSL 内置函数（M2）

约定：
  - 输入序列为 np.ndarray，按时间升序（最右侧为最新）。
  - 输出同长度的 np.ndarray，前 n-1 个位置为 np.nan（不足窗口）。
  - 时间对齐：所有函数的输出长度与输入相同，可以参与四则运算与比较。
"""
from __future__ import annotations

import numpy as np


def _as_array(x) -> np.ndarray:
    if isinstance(x, np.ndarray):
        return x.astype(float, copy=False)
    return np.asarray(x, dtype=float)


def ma(x, n: int) -> np.ndarray:
    """简单移动平均。"""
    a = _as_array(x)
    n = int(n)
    if n <= 0:
        raise ValueError("ma window must be > 0")
    if a.size == 0:
        return a
    csum = np.concatenate([[0.0], np.cumsum(a)])
    out = (csum[n:] - csum[:-n]) / n
    pad = np.full(n - 1, np.nan)
    return np.concatenate([pad, out])


def ema(x, n: int) -> np.ndarray:
    """指数移动平均，alpha = 2/(n+1)。"""
    a = _as_array(x)
    n = int(n)
    if n <= 0:
        raise ValueError("ema window must be > 0")
    if a.size == 0:
        return a
    alpha = 2.0 / (n + 1.0)
    out = np.empty_like(a)
    out[:] = np.nan
    # seed: 用前 n 个的均值
    if a.size < n:
        return out
    seed = a[:n].mean()
    out[n - 1] = seed
    for i in range(n, a.size):
        out[i] = alpha * a[i] + (1 - alpha) * out[i - 1]
    return out


def highest(x, n: int) -> np.ndarray:
    """过去 n 期（含当前）的最高值。"""
    a = _as_array(x)
    n = int(n)
    if n <= 0:
        raise ValueError("highest window must be > 0")
    if a.size == 0:
        return a
    out = np.full_like(a, np.nan)
    for i in range(n - 1, a.size):
        out[i] = a[i - n + 1 : i + 1].max()
    return out


def lowest(x, n: int) -> np.ndarray:
    """过去 n 期（含当前）的最低值。"""
    a = _as_array(x)
    n = int(n)
    if n <= 0:
        raise ValueError("lowest window must be > 0")
    if a.size == 0:
        return a
    out = np.full_like(a, np.nan)
    for i in range(n - 1, a.size):
        out[i] = a[i - n + 1 : i + 1].min()
    return out


def shift(x, n: int) -> np.ndarray:
    """序列向后平移 n 期（即取 n 期之前的值）。"""
    a = _as_array(x)
    n = int(n)
    if n == 0:
        return a.copy()
    if n < 0:
        raise ValueError("shift n must be >= 0")
    out = np.full_like(a, np.nan)
    if n < a.size:
        out[n:] = a[:-n]
    return out


def atr(high, low, close, n: int = 14) -> np.ndarray:
    """ATR (Wilder's, 简化版用 SMA)。"""
    h = _as_array(high)
    l = _as_array(low)
    c = _as_array(close)
    if h.size == 0:
        return h
    prev_close = shift(c, 1)
    tr = np.maximum.reduce([
        h - l,
        np.abs(h - prev_close),
        np.abs(l - prev_close),
    ])
    return ma(tr, n)


def cross_up(x, y) -> np.ndarray:
    """x 上穿 y（昨日 x<=y 且 今日 x>y）。返回 0/1。"""
    a = _as_array(x)
    b = _as_array(y) if hasattr(y, "__len__") else np.full_like(a, float(y))
    if a.size == 0:
        return a
    out = np.zeros_like(a)
    out[1:] = ((a[:-1] <= b[:-1]) & (a[1:] > b[1:])).astype(float)
    return out


def cross_down(x, y) -> np.ndarray:
    a = _as_array(x)
    b = _as_array(y) if hasattr(y, "__len__") else np.full_like(a, float(y))
    if a.size == 0:
        return a
    out = np.zeros_like(a)
    out[1:] = ((a[:-1] >= b[:-1]) & (a[1:] < b[1:])).astype(float)
    return out


# ── 注册表：DSL 求值器只允许调用这些函数 ──
BUILTINS = {
    "ma": ma,
    "ema": ema,
    "highest": highest,
    "lowest": lowest,
    "shift": shift,
    "atr": atr,
    "cross_up": cross_up,
    "cross_down": cross_down,
    # 常用数学辅助
    "abs": lambda x: np.abs(_as_array(x)) if hasattr(x, "__len__") else abs(x),
    "min": lambda *xs: np.minimum.reduce([_as_array(x) if hasattr(x, "__len__") else x for x in xs]),
    "max": lambda *xs: np.maximum.reduce([_as_array(x) if hasattr(x, "__len__") else x for x in xs]),
}
