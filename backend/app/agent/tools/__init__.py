"""Auto-import all tool modules so they self-register."""
from app.agent.tools import (  # noqa: F401
    db_query, bash_exec, file_ops, plan, subagent,
    # market_data, sr_levels, kb_search, web_search,
    # pivotlab_services,
)
from app.agent.tools.registry import registry

__all__ = ["registry"]
