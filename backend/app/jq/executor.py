"""用户代码安全验证。

只做静态 AST 检查，禁止危险 import 和 attribute 访问。
不走子进程隔离（内网私有部署场景，性能优先）。
"""
from __future__ import annotations

import ast
from typing import Any

# 禁止导入的模块（黑名单）
_FORBIDDEN_IMPORTS = {
    "os", "sys", "subprocess", "shutil", "socket", "urllib",
    "urllib2", "http", "ftplib", "smtplib", "telnetlib",
    "multiprocessing", "threading", "concurrent", "asyncio",
    "ctypes", "cffi", "importlib", "builtins", "gc",
    "signal", "resource", "mmap", "pty", "tty",
    "pickle", "marshal", "shelve", "dbm",
    "pathlib", "glob", "tempfile",
}

# 允许访问的顶层属性（白名单），用于 context.portfolio.cash 这类访问
_ALLOWED_ATTRS = {
    "portfolio", "cash", "positions", "total_value", "positions_value",
    "starting_cash", "pnl", "returns",
    "total_amount", "closeable_amount", "avg_cost", "price", "acc_avg_cost",
    "value", "side", "security",
    "current_dt", "run_params", "benchmark", "universe",
    "start_date", "end_date", "frequency", "initial_cash",
    "order_id", "filled", "status", "created_dt", "commission",
    # DataFrame / Series 常用属性
    "iloc", "loc", "values", "index", "columns", "shape",
    "mean", "std", "max", "min", "sum", "last", "tail", "head",
    "rolling", "shift", "diff", "pct_change", "dropna", "fillna",
    "empty", "dtypes", "T",
    # 用户自定义属性（以字母开头，不以 __ 开头）
}


class CodeValidationError(Exception):
    pass


def _check_imports(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                if name in _FORBIDDEN_IMPORTS:
                    raise CodeValidationError(
                        f"不允许 import '{name}'，请使用平台提供的数据 API"
                    )


def _check_dangerous_calls(tree: ast.AST) -> None:
    """禁止 eval/exec/compile/open/__import__ 等危险调用。"""
    _FORBIDDEN_CALLS = {
        "eval", "exec", "compile", "open", "__import__",
        "getattr", "setattr", "delattr", "vars", "dir",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in _FORBIDDEN_CALLS:
                    raise CodeValidationError(
                        f"不允许调用 '{node.func.id}'"
                    )


def validate_strategy_code(code: str) -> None:
    """对用户策略代码做静态安全检查。

    通过则静默返回；不通过则抛出 CodeValidationError。
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise CodeValidationError(f"语法错误：{e}")

    _check_imports(tree)
    _check_dangerous_calls(tree)


def extract_callbacks(code: str, ns: dict[str, Any]) -> dict[str, Any]:
    """执行用户代码，从命名空间提取四个回调函数。

    返回:
        {
          'initialize':           callable or None,
          'handle_data':          callable or None,
          'before_trading_start': callable or None,
          'after_trading_end':    callable or None,
        }
    """
    validate_strategy_code(code)
    try:
        exec(compile(code, "<strategy>", "exec"), ns)
    except Exception as e:
        raise CodeValidationError(f"策略代码执行失败：{e}")

    return {
        "initialize":           ns.get("initialize"),
        "handle_data":          ns.get("handle_data"),
        "before_trading_start": ns.get("before_trading_start"),
        "after_trading_end":    ns.get("after_trading_end"),
    }
