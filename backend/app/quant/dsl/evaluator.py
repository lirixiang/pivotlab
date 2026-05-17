"""安全的表达式求值器（M2）

设计原则：
  - 只允许 ast 白名单节点；禁止 import / attribute / subscript / lambda 等
  - 变量只能从 context 字典解析
  - 函数只能调用 functions.BUILTINS 中的内置函数
  - 求值结果可能是标量（bool/float）或 np.ndarray
  - 顶层"是否触发"取序列最后一个非 NaN 值，与 0 比较

示例：
    eval_rule("close > ma(close, 20)", ctx)  -> True / False
    eval_rule("close > shift(highest(high, 20), 1)", ctx) -> True / False
"""
from __future__ import annotations

import ast
import operator as op
from dataclasses import dataclass
from typing import Any

import numpy as np

from .functions import BUILTINS

# 白名单节点
_ALLOWED_NODES = (
    ast.Expression,
    ast.BoolOp, ast.And, ast.Or,
    ast.UnaryOp, ast.Not, ast.USub, ast.UAdd,
    ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow,
    ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.Call, ast.Name, ast.Constant, ast.Load,
    ast.Tuple, ast.List,
)

_BINOPS = {
    ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul,
    ast.Div: op.truediv, ast.Mod: op.mod, ast.Pow: op.pow,
}
_CMPOPS = {
    ast.Eq: op.eq, ast.NotEq: op.ne,
    ast.Lt: op.lt, ast.LtE: op.le,
    ast.Gt: op.gt, ast.GtE: op.ge,
}


class DSLError(Exception):
    pass


@dataclass
class EvalResult:
    """单条规则的求值结果。"""
    expr: str
    desc: str
    passed: bool
    value: float | None = None   # 表达式在最后一根 bar 的数值（用于排错）
    error: str | None = None


def _walk_check(node: ast.AST) -> None:
    for sub in ast.walk(node):
        if not isinstance(sub, _ALLOWED_NODES):
            raise DSLError(f"不允许的语法节点：{type(sub).__name__}")


def _eval_node(node: ast.AST, ctx: dict[str, Any]) -> Any:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, ctx)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in ctx:
            return ctx[node.id]
        if node.id in BUILTINS:
            return BUILTINS[node.id]
        if node.id in ("True", "true"):
            return True
        if node.id in ("False", "false"):
            return False
        raise DSLError(f"未知变量：{node.id}")
    if isinstance(node, ast.UnaryOp):
        v = _eval_node(node.operand, ctx)
        if isinstance(node.op, ast.Not):
            return _to_bool_scalar(v) is False
        if isinstance(node.op, ast.USub):
            return -v
        if isinstance(node.op, ast.UAdd):
            return +v
    if isinstance(node, ast.BoolOp):
        vals = [_eval_node(v, ctx) for v in node.values]
        if isinstance(node.op, ast.And):
            return all(_to_bool_scalar(v) for v in vals)
        if isinstance(node.op, ast.Or):
            return any(_to_bool_scalar(v) for v in vals)
    if isinstance(node, ast.BinOp):
        fn = _BINOPS.get(type(node.op))
        if not fn:
            raise DSLError(f"不支持的二元运算：{type(node.op).__name__}")
        return fn(_eval_node(node.left, ctx), _eval_node(node.right, ctx))
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, ctx)
        result = None
        for op_node, right_node in zip(node.ops, node.comparators):
            right = _eval_node(right_node, ctx)
            fn = _CMPOPS.get(type(op_node))
            if not fn:
                raise DSLError(f"不支持的比较运算：{type(op_node).__name__}")
            cmp = fn(left, right)
            result = cmp if result is None else (result & cmp)
            left = right
        return result
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise DSLError("只能调用具名函数")
        fn_name = node.func.id
        fn = BUILTINS.get(fn_name)
        if fn is None:
            raise DSLError(f"未知函数：{fn_name}")
        args = [_eval_node(a, ctx) for a in node.args]
        kwargs = {kw.arg: _eval_node(kw.value, ctx) for kw in node.keywords}
        return fn(*args, **kwargs)
    raise DSLError(f"不支持的节点：{type(node).__name__}")


def _to_bool_scalar(v: Any) -> bool:
    """把表达式结果归约为最后一个 bar 的 True/False。"""
    if isinstance(v, np.ndarray):
        if v.size == 0:
            return False
        last = v[-1]
        if isinstance(last, (np.bool_, bool)):
            return bool(last)
        if isinstance(last, (np.floating, float, np.integer, int)):
            if np.isnan(last):
                return False
            return bool(last)
        return bool(last)
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if isinstance(v, (np.floating, float, np.integer, int)):
        if v != v:  # NaN
            return False
        return bool(v)
    return bool(v)


def _to_float_scalar(v: Any) -> float | None:
    """提取表达式在最后一根 bar 的数值（仅用于展示）。"""
    if isinstance(v, np.ndarray):
        if v.size == 0:
            return None
        last = v[-1]
        try:
            f = float(last)
            return None if f != f else f
        except (TypeError, ValueError):
            return None
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def eval_rule(expr: str, ctx: dict[str, Any], desc: str = "") -> EvalResult:
    """对一条规则求值，返回 EvalResult。"""
    try:
        tree = ast.parse(expr, mode="eval")
        _walk_check(tree)
        v = _eval_node(tree, ctx)
        return EvalResult(
            expr=expr,
            desc=desc,
            passed=_to_bool_scalar(v),
            value=_to_float_scalar(v),
        )
    except DSLError as e:
        return EvalResult(expr=expr, desc=desc, passed=False, error=str(e))
    except Exception as e:
        return EvalResult(expr=expr, desc=desc, passed=False, error=f"{type(e).__name__}: {e}")
