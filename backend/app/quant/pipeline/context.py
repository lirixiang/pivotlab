"""单股的求值上下文（M2）

把一只股票的 K 线序列转成 DSL 求值所需的 numpy 字典：
    {"open": ndarray, "high": ndarray, "low": ndarray, "close": ndarray, "vol": ndarray, "date": list[str]}

可指定 end_date，截断到该日（含）为止用于"回到历史某天看是否触发"。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ...services.data_provider import get_candles


@dataclass
class StockContext:
    code: str
    dates: list[str]
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    vol: np.ndarray

    def as_dict(self) -> dict[str, np.ndarray]:
        return {
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "vol": self.vol,
            "volume": self.vol,        # 别名
            "amount": self.close * self.vol,  # 成交额 ≈ 收盘 × 量
        }

    @property
    def last_date(self) -> str | None:
        return self.dates[-1] if self.dates else None


def load_stock_context(
    code: str, end_date: str | None = None, lookback: int = 300
) -> StockContext | None:
    """
    end_date: "YYYY-MM-DD" 或 None（取最新）
    lookback: 拉取的最大日数（信号需要 150 日线 + 留足缓冲，建议 ≥ 250）
    """
    candles = get_candles(code, period="daily", days=max(lookback, 250))
    if not candles:
        return None

    # 过滤掉盘中"估计"K线 + 截断到 end_date
    rows = [c for c in candles if not getattr(c, "estimated", False)]
    if end_date:
        rows = [c for c in rows if c.date <= end_date]
    if not rows:
        return None

    return StockContext(
        code=code,
        dates=[c.date for c in rows],
        open=np.array([c.open for c in rows], dtype=float),
        high=np.array([c.high for c in rows], dtype=float),
        low=np.array([c.low for c in rows], dtype=float),
        close=np.array([c.close for c in rows], dtype=float),
        vol=np.array([c.volume for c in rows], dtype=float),
    )
