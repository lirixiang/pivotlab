"""量化交易系统模块 (M1)

完整闭环：数据 → 选股 → 信号 → 风控 → 执行 → 复盘

模块组织：
  models.py     - 数据模型（systems / system_runs / trades / positions / nav_daily）
  schemas.py    - Pydantic 请求/响应模型
  defaults.py   - 默认配置模板（Stage2 趋势跟随系统）
  router.py     - FastAPI 路由 /api/quant/*

未来里程碑：
  dsl/          - M2 信号表达式 DSL
  pipeline/     - M2-M3 选股/信号/风控/执行运行时
  backtest/     - M4 回测引擎
  journal/      - M5 实盘日志聚合
"""
from . import models  # noqa: F401  触发 SQLAlchemy 模型注册
