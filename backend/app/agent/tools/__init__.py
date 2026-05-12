"""Auto-import all tool modules so they self-register."""
from app.agent.tools import (  # noqa: F401
    db_query, bash_exec, market_data, sr_levels,
    kb_search, pivotlab_services, web_search, data_sync, plan,
    chart_render,
)
from app.agent.tools.registry import registry

__all__ = ["registry"]
