"""市场板块工具 — 全局唯一定义"主板"白名单。

业务层(screener / recommender / 前端展示)应只处理主板;
采集层(sync_*)保持全市场抓取,以便将来扩展。
"""
from __future__ import annotations

from typing import Callable, Iterable, TypeVar

# 沪主板:600/601/603/605  深主板:000/001/002/003
# 排除:科创板(688/689)、创业板(300/301)、北交所(8xxxxx / 4xxxxx)
MAIN_BOARD_PREFIXES: tuple[str, ...] = (
    "600", "601", "603", "605",
    "000", "001", "002", "003",
)

T = TypeVar("T")


def is_main_board(code: str | None) -> bool:
    """判断一个 6 位 A 股代码是否属于主板。"""
    if not code:
        return False
    return code.startswith(MAIN_BOARD_PREFIXES)


def filter_main_board(items: Iterable[T], key: Callable[[T], str] = lambda x: x) -> list[T]:  # type: ignore[assignment]
    """从可迭代对象中筛掉非主板项。`key` 指定如何取 code(默认元素本身即 code)。"""
    return [it for it in items if is_main_board(key(it))]


def main_board_like_patterns() -> list[str]:
    """返回 SQL LIKE 用的前缀模式(供 SQLAlchemy `or_(...like)` 构造)。"""
    return [f"{p}%" for p in MAIN_BOARD_PREFIXES]
