"""默认配置模板：Stage 2 趋势跟随系统

参考 Stan Weinstein 的 4 阶段理论 + 用户提供的完整规则：
  - 长期：150 日线（30 周线）向上 + 股价在其之上
  - 中期：20 日线向上 + 股价在其之上
  - 突破：突破过去 20 日最高价
  - 放量：成交量 > 50 日均量 × 1.5
  - 大盘：沪深 300 的 20 日线向上或走平

卖出（新手版）：跌破 20 日线即卖。
风控：单票 ≤ 10%，总仓 ≤ 80%，单笔最大亏损 ≤ 2%。

DSL 字段说明（M2 实现解析器）：
  - close/open/high/low/vol  : 时间序列变量
  - ma(x, n)                 : 简单均线
  - highest(x, n) / lowest(x, n)
  - shift(x, n)              : 序列后移 n 期
  - 比较运算符: > < >= <= == !=
  - 逻辑运算符: and / or / not
  - 算术运算符: + - * /
"""

STAGE2_TREND_FOLLOWING = {
    "name": "Stage 2 趋势跟随",
    "description": (
        "经典趋势跟随系统：长期+中期均线向上、突破 20 日高 + 放量。"
        "新手版退出条件：跌破 20 日线即卖。"
    ),
    "status": "draft",
    "initial_capital": 100000.0,

    "universe_cfg": {
        "base": "all_a_shares",  # 全 A 股（主板优先）
        "filters": [
            # amount = close * vol （成交额）；ma(amount, 5) = 5 日均成交额
            {"expr": "ma(amount, 5) > 5e7", "desc": "5 日均成交额 > 5000 万"},
            {"expr": "close > 5", "desc": "股价 > 5 元"},
            {"expr": "not is_st", "desc": "非 ST"},
            {"expr": "close > ma(close, 200)", "desc": "股价在 200 日线之上"},
        ],
        "exclude_codes": [],
        "max_size": 200,
    },

    "signal_cfg": {
        "buy": {
            "all_of": [
                # ① 长期趋势：股价 > 150 日线 且 150 日线方向向上
                {"expr": "close > ma(close, 150)", "desc": "股价 > 150 日线"},
                {"expr": "ma(close, 150) > shift(ma(close, 150), 10)", "desc": "150 日线 10 日前 → 今日向上"},
                # ② 中期趋势：股价 > 20 日线 且 20 日线方向向上
                {"expr": "close > ma(close, 20)", "desc": "股价 > 20 日线"},
                {"expr": "ma(close, 20) > shift(ma(close, 20), 5)", "desc": "20 日线 5 日前 → 今日向上"},
                # ③ 突破前高
                {"expr": "close > shift(highest(high, 20), 1)", "desc": "收盘突破过去 20 日最高"},
                # ④ 放量确认
                {"expr": "vol > ma(vol, 50) * 1.5", "desc": "成交量 > 50 日均量 × 1.5"},
                # ⑤ 大盘配合（市场过滤，单股 DSL 暂用占位；M2 引入 benchmark 变量后启用）
                # {"expr": "benchmark.ma(close, 20) >= shift(benchmark.ma(close, 20), 1)", "desc": "沪深 300 20 日线走平或向上"},
            ],
        },
        "sell": {
            "any_of": [
                # 新手版：跌破 20 日线即卖（次日收盘前未站回）
                {"expr": "close < ma(close, 20)", "desc": "收盘跌破 20 日线（止损 / 止盈合一）"},
                # 以下为进阶规则，默认注释。用户跑半年熟练后可在编辑器里启用：
                # {"expr": "close < ma(close, 150)", "desc": "止盈 A：跌破 150 日线（长期趋势逆转）"},
                # {"expr": "(highest(close, 60) - close) / highest(close, 60) > 0.08", "desc": "止盈 B：从最高点回撤 > 8%"},
                # 止盈 C（持有 > 60 日且涨幅 < 10%）需 position 上下文，M3 风控/执行层支持
            ],
        },
    },

    "risk_cfg": {
        "per_stock_max_pct": 10.0,           # 单票最大占总资金 %
        "total_position_max_pct": 80.0,      # 总仓位上限 %
        "per_trade_max_loss_pct": 2.0,       # 单笔最大亏损 %（反推手数）
        "stop_loss": {
            "type": "ma",                    # ma | percent | atr
            "ma_period": 20,                 # type=ma 时使用：跌破该均线即止损
            # "percent": 8.0,                # type=percent: 跌幅触发
            # "atr_period": 14,              # type=atr
            # "atr_mult": 2.0,
        },
        "trailing_stop": False,
        "drawdown_breaker_pct": 15.0,        # 系统级回撤熔断：系统净值回撤超此值暂停下单
    },

    "exec_cfg": {
        "mode": "semi_auto",                 # semi_auto: 只生成清单 / manual: 完全手工
        "order_type": "limit_close",         # 按收盘价限价
        "max_orders_per_day": 5,
        "notify": {"channel": "web", "enabled": True},
    },
}


def make_default(name: str = "Stage 2 趋势跟随") -> dict:
    """返回一份可直接写入数据库的默认系统配置。"""
    cfg = {k: v for k, v in STAGE2_TREND_FOLLOWING.items()}
    cfg["name"] = name
    return cfg


# ── VCP / Pivot Breakout（趋势龙头突破）────────────────────────

VCP_BREAKOUT = {
    "name": "VCP 龙头突破",
    "description": (
        "Mark Minervini 风格：VCP（波动收缩形态）+ Pivot 突破。"
        "止损极窄 3-5%，目标 20%+，高 RR。适合爆发盘仓位。"
    ),
    "status": "draft",
    "initial_capital": 100000.0,

    "universe_cfg": {
        "base": "all_a_shares",
        "filters": [
            {"expr": "ma(amount, 5) > 1e8", "desc": "5 日均成交额 > 1 亿"},
            {"expr": "close > 10", "desc": "股价 > 10 元"},
            {"expr": "not is_st", "desc": "非 ST"},
            # Stage 2 基础条件
            {"expr": "close > ma(close, 150)", "desc": "股价在 150 日线之上"},
            {"expr": "close > ma(close, 200)", "desc": "股价在 200 日线之上"},
            {"expr": "ma(close, 150) > ma(close, 200)", "desc": "150 日线 > 200 日线"},
            # 过去半年涨过 30%（证明有趋势动力）
            {"expr": "close / lowest(low, 120) > 1.3", "desc": "半年内最大涨幅 > 30%"},
            # 距离高点回撤 < 25%（VCP 收缩阶段）
            {"expr": "(highest(high, 120) - close) / highest(high, 120) < 0.25", "desc": "距半年高点回撤 < 25%"},
        ],
        "exclude_codes": [],
        "max_size": 100,
    },

    "signal_cfg": {
        "buy": {
            "all_of": [
                # ① 波动收缩：近 10 日振幅 < 近 40 日振幅的 50%
                {"expr": "(highest(high, 10) - lowest(low, 10)) / lowest(low, 10) < (highest(high, 40) - lowest(low, 40)) / lowest(low, 40) * 0.5",
                 "desc": "波动收缩：10 日振幅 < 40 日振幅 × 50%"},
                # ② Pivot 突破：突破近 20 日最高
                {"expr": "close > shift(highest(high, 20), 1)", "desc": "突破 20 日最高价（Pivot Point）"},
                # ③ 量能确认
                {"expr": "vol > ma(vol, 50) * 1.5", "desc": "放量 > 50 日均量 × 1.5"},
                # ④ 均线多头排列
                {"expr": "ma(close, 20) > ma(close, 50)", "desc": "20 日线 > 50 日线"},
                {"expr": "close > ma(close, 20)", "desc": "股价 > 20 日线"},
            ],
        },
        "sell": {
            "any_of": [
                # 窄止损（VCP 核心：错了就走，不犹豫）
                {"expr": "close < ma(close, 10)", "desc": "跌破 10 日线（短期止损）"},
                # 趋势破坏
                {"expr": "close < ma(close, 50)", "desc": "跌破 50 日线（趋势止损）"},
                # 大涨后回撤止盈
                {"expr": "(highest(close, 30) - close) / highest(close, 30) > 0.10", "desc": "从 30 日高点回撤 > 10%（止盈）"},
            ],
        },
    },

    "risk_cfg": {
        "per_stock_max_pct": 8.0,
        "total_position_max_pct": 50.0,
        "per_trade_max_loss_pct": 1.0,      # Minervini: 每笔最大亏 1% 总资金
        "stop_loss": {
            "type": "percent",
            "percent": 5.0,                  # 买入价下方 5% 硬止损
        },
        "trailing_stop": False,
        "drawdown_breaker_pct": 10.0,        # 系统回撤 10% 暂停（Minervini 原则）
    },

    "exec_cfg": {
        "mode": "semi_auto",
        "order_type": "limit_close",
        "max_orders_per_day": 3,             # 每天最多 3 只新票
        "notify": {"channel": "web", "enabled": True},
    },
}


# ── 板块龙头 + Catalyst ──────────────────────────────────────

SECTOR_LEADER = {
    "name": "板块龙头突破",
    "description": (
        "板块轮动 + 龙头股策略：锁定强势行业中涨幅领先的个股。"
        "适合热点盘仓位（10%），交易频次较高。"
    ),
    "status": "draft",
    "initial_capital": 100000.0,

    "universe_cfg": {
        "base": "all_a_shares",
        "filters": [
            {"expr": "ma(amount, 5) > 2e8", "desc": "5 日均成交额 > 2 亿（龙头标准）"},
            {"expr": "close > 10", "desc": "股价 > 10 元"},
            {"expr": "not is_st", "desc": "非 ST"},
            # 短期强势
            {"expr": "close > ma(close, 20)", "desc": "股价 > 20 日线"},
            {"expr": "close > ma(close, 60)", "desc": "股价 > 60 日线"},
            # 最近 20 日涨幅前列
            {"expr": "close / shift(close, 20) > 1.10", "desc": "20 日涨幅 > 10%"},
        ],
        "exclude_codes": [],
        "max_size": 50,
    },

    "signal_cfg": {
        "buy": {
            "all_of": [
                {"expr": "close > shift(highest(high, 10), 1)", "desc": "突破 10 日最高（短线突破）"},
                {"expr": "vol > ma(vol, 20) * 2", "desc": "放量 > 20 日均量 × 2"},
                {"expr": "ma(close, 5) > ma(close, 10)", "desc": "5 日线 > 10 日线（短期金叉）"},
                {"expr": "close / shift(close, 5) > 1.05", "desc": "5 日涨幅 > 5%（动量确认）"},
            ],
        },
        "sell": {
            "any_of": [
                {"expr": "close < ma(close, 5)", "desc": "跌破 5 日线（龙头走弱）"},
                {"expr": "close < ma(close, 20)", "desc": "跌破 20 日线"},
                {"expr": "(highest(close, 10) - close) / highest(close, 10) > 0.08", "desc": "从 10 日高点回撤 > 8%"},
            ],
        },
    },

    "risk_cfg": {
        "per_stock_max_pct": 5.0,            # 龙头票仓位小（波动大）
        "total_position_max_pct": 30.0,
        "per_trade_max_loss_pct": 1.5,
        "stop_loss": {
            "type": "ma",
            "ma_period": 5,                  # 跌破 5 日线即止损
        },
        "trailing_stop": False,
        "drawdown_breaker_pct": 8.0,
    },

    "exec_cfg": {
        "mode": "semi_auto",
        "order_type": "limit_close",
        "max_orders_per_day": 2,
        "notify": {"channel": "web", "enabled": True},
    },
}


# ── 所有模板汇总（前端用） ────────────────────────────────────

TEMPLATES = {
    "stage2": {
        "key": "stage2",
        "name": "Stage 2 趋势跟随",
        "emoji": "📈",
        "desc": "Weinstein 经典：长期均线上行 + 突破 + 放量。占仓位 60%，低频持有 1-6 个月。",
        "tags": ["趋势", "低频", "适合新手"],
        "config": STAGE2_TREND_FOLLOWING,
    },
    "vcp": {
        "key": "vcp",
        "name": "VCP 龙头突破",
        "emoji": "🎯",
        "desc": "Minervini 风格：波动收缩 + Pivot 突破，止损极窄 3-5%，目标 20%+。占仓位 30%。",
        "tags": ["突破", "高RR", "中高频"],
        "config": VCP_BREAKOUT,
    },
    "sector": {
        "key": "sector",
        "name": "板块龙头突破",
        "emoji": "🔥",
        "desc": "短线强势股：锁定强势行业龙头 + 放量突破。占仓位 10%，需要较强执行力。",
        "tags": ["短线", "高频", "进阶"],
        "config": SECTOR_LEADER,
    },
}

