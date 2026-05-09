"""New strategy / recommender layer (v2).

Replaces the legacy `services/{ai_strategy,ml_scorer,rl_*,screener,
signal_generator,...}` modules. Sits on top of the data layer
(`services/data_provider`, `services/levels_multifactor`).

Public entry points:
    from app.strategy.recommender import scan_universe, rebuild_for_code
    from app.strategy.trade_plan import build_trade_plan

Style keys: "short_term" | "swing" | "value" | "multi_factor"
"""

from .recommender import scan_universe, rebuild_for_code  # noqa: F401
from .trade_plan import build_trade_plan  # noqa: F401

STYLES = ["short_term", "swing", "value", "multi_factor", "ai_ensemble"]
STYLE_LABELS = {
    "short_term": "短线打板",
    "swing": "波段交易",
    "value": "中长线价值",
    "multi_factor": "多因子量化",
    "ai_ensemble": "AI 集成(LGBM+TCN+规则)",
}
