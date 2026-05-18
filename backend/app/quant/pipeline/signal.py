"""信号层（M2）：对单股求值买/卖规则。

输入：
  - signal_cfg: 来自系统配置的 signal_cfg
    {
      "buy":  {"all_of": [{"expr": ..., "desc": ...}, ...], "any_of": [...]},
      "sell": {"all_of": [...], "any_of": [...]},
    }
  - ctx_dict: 来自 StockContext.as_dict()

输出：SignalReport
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ..dsl import EvalResult, eval_rule


@dataclass
class SideReport:
    triggered: bool
    rules: list[EvalResult] = field(default_factory=list)
    # 组合方式："all_of" / "any_of" / "empty"
    combine: str = "empty"


@dataclass
class SignalReport:
    code: str
    date: str | None
    buy: SideReport
    sell: SideReport

    def to_jsonable(self) -> dict[str, Any]:
        def side(s: SideReport) -> dict[str, Any]:
            return {
                "triggered": s.triggered,
                "combine": s.combine,
                "rules": [asdict(r) for r in s.rules],
            }
        return {
            "code": self.code,
            "date": self.date,
            "buy": side(self.buy),
            "sell": side(self.sell),
        }


def _eval_side(side_cfg: dict | None, ctx: dict) -> SideReport:
    if not side_cfg:
        return SideReport(triggered=False, combine="empty")
    all_of = side_cfg.get("all_of") or []
    any_of = side_cfg.get("any_of") or []
    optional = side_cfg.get("optional")

    if all_of:
        results = [eval_rule(r["expr"], ctx, r.get("desc", "")) for r in all_of]
        core_passed = all(r.passed for r in results) if results else False

        if optional and core_passed:
            opt_rules = optional.get("rules") or []
            min_match = optional.get("min_match", 1)
            opt_results = [eval_rule(r["expr"], ctx, r.get("desc", "")) for r in opt_rules]
            opt_passed = sum(1 for r in opt_results if r.passed) >= min_match
            return SideReport(
                triggered=core_passed and opt_passed,
                rules=results + opt_results,
                combine="all_of+optional",
            )

        return SideReport(triggered=core_passed, rules=results, combine="all_of")
    if any_of:
        results = [eval_rule(r["expr"], ctx, r.get("desc", "")) for r in any_of]
        passed = any(r.passed for r in results) if results else False
        return SideReport(triggered=passed, rules=results, combine="any_of")
    return SideReport(triggered=False, combine="empty")


def evaluate_signal(signal_cfg: dict, ctx_dict: dict, code: str, date: str | None) -> SignalReport:
    buy = _eval_side(signal_cfg.get("buy"), ctx_dict)
    sell = _eval_side(signal_cfg.get("sell"), ctx_dict)
    return SignalReport(code=code, date=date, buy=buy, sell=sell)
