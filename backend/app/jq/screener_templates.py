"""内置形态的代码模板。

用户可在前端"内置模板"下拉选择，加载到编辑器后随意修改。
每个模板都是可执行的最小实现，对应 services/screener.py 里的 detect_xxx 简化版本。
"""
from __future__ import annotations

# ─────────────────────── 默认示例（突破回踩） ───────────────────────

TEMPLATE_DEFAULT = '''\
# ── 筛选策略示例：突破回踩 ─────────────────────────────────────
# 逻辑：股价创 20 日新高后回踩 3~15%，且接近重要支撑位
#
# 可用 API：
#   get_sr_levels(candles)        - 计算支撑/压力位
#   nearest_support(levels, price) - 找最近下方支撑
#   distance_pct(cur, target)      - 计算距离百分比
#   MA(closes, n)                  - n 日均线值
#   MACD(closes)                   - 返回 {dif, dea, hist}
#   volume_ratio(candles, 5)       - 5 日量比
#   highest(highs, n) / lowest()
#   log.info / log.warn / log.error
#
# filter 返回值格式：
#   {"score": 0-100, "triggers": [...], "breakout_price": ?, "pullback_price": ?,
#    "rr_ratio": ?, "support_score": ?, ...}
#   或 None 表示不命中

def initialize(context):
    context.name = "突破回踩"
    context.min_candles = 120


def filter(context, code, name, candles, weekly):
    closes = closes_of(candles)
    highs = highs_of(candles)
    cur = closes[-1]

    # ── Step 1: 粗筛 — 20 日新高 + 回踩 3~15% ─────────────
    high20 = highest(highs, 20)
    pullback = (high20 - cur) / high20
    if not (0.03 <= pullback <= 0.15):
        return None

    # ── Step 2: 支撑位检查 ─────────────────────────────
    levels = get_sr_levels(candles)
    sup = nearest_support(levels, cur)
    if not sup:
        return None
    dist = distance_pct(cur, sup.price)
    if abs(dist) > 1.5:
        return None
    if sup.score < 75:
        return None

    # ── Step 3: 量能 ─────────────────────────────────
    vr = volume_ratio(candles, 5)

    # ── 评分 ──────────────────────────────────────
    score = 60
    if sup.score >= 80: score += 10
    if abs(dist) <= 1.0: score += 10
    if vr >= 1.2: score += 10
    if vr >= 1.5: score += 5
    score = min(100, score)

    triggers = ["20日新高", f"回踩{pullback*100:.1f}%"]
    if vr >= 1.5:
        triggers.append(f"量比{vr:.2f}")

    return {
        "score": score,
        "triggers": triggers,
        "breakout_price": high20,
        "pullback_price": sup.price,
        "distance_to_support_pct": dist,
        "volume_ratio": vr,
        "support_score": sup.score,
        "rr_ratio": pullback / 0.02,  # 简化盈亏比
    }
'''

# ─────────────────────── MACD 底背离 ───────────────────────

TEMPLATE_MACD_DIVERGENCE = '''\
# ── 筛选策略：MACD 底背离 ──────────────────────────────────────
# 逻辑：价格创近 60 日新低 但 MACD DIF 比上一个低点高

def initialize(context):
    context.name = "MACD底背离"
    context.min_candles = 120


def filter(context, code, name, candles, weekly):
    closes = closes_of(candles)
    cur = closes[-1]

    # 价格创 60 日新低
    if cur > min(closes[-60:]) * 1.02:
        return None

    # 计算 MACD
    macd = MACD(closes)
    dif = macd["dif"]

    # 找当前 dif 与近期低点 dif 的关系
    # 简化：当前 dif > 前 30 日的最低 dif
    recent_dif = [d for d in dif[-30:] if not (d != d)]  # 过滤 nan
    if not recent_dif:
        return None
    if dif[-1] <= min(recent_dif) * 1.05:  # 当前 dif 比前低高 5% 以上
        return None

    # 支撑位
    levels = get_sr_levels(candles)
    sup = nearest_support(levels, cur)
    sup_score = sup.score if sup else 0

    score = 65
    if sup_score >= 50:
        score += 10
    if dif[-1] > dif[-2]:  # DIF 抬头
        score += 10
    score = min(100, score)

    return {
        "score": score,
        "triggers": ["底背离形成", "DIF 抬头" if dif[-1] > dif[-2] else "DIF 探底"],
        "pullback_price": sup.price if sup else None,
        "support_score": sup_score,
        "rr_ratio": 3.0,
    }
'''

# ─────────────────────── 均线支撑 ───────────────────────

TEMPLATE_MA_SUPPORT = '''\
# ── 筛选策略：均线支撑 ────────────────────────────────────────
# 逻辑：回踩 MA20/MA60 + 多头排列

def initialize(context):
    context.name = "均线支撑"
    context.min_candles = 120


def filter(context, code, name, candles, weekly):
    closes = closes_of(candles)
    cur = closes[-1]

    ma20 = MA(closes, 20)
    ma60 = MA(closes, 60)
    if ma20 != ma20 or ma60 != ma60:  # nan check
        return None

    # 必须多头排列
    if not (cur > ma20 > ma60):
        return None

    # 回踩到 MA20 附近（距离 < 2%）
    dist_ma20 = (cur - ma20) / ma20 * 100
    if not (0 <= dist_ma20 <= 2):
        return None

    vr = volume_ratio(candles, 5)
    score = 60
    if dist_ma20 <= 1: score += 15
    if vr >= 1.0: score += 10
    if vr >= 1.5: score += 5
    score = min(100, score)

    return {
        "score": score,
        "triggers": ["多头排列", f"回踩MA20({dist_ma20:.2f}%)"],
        "pullback_price": ma20,
        "volume_ratio": vr,
        "rr_ratio": 2.5,
    }
'''

# ─────────────────────── 强势趋势 ───────────────────────

TEMPLATE_TREND_STRONG = '''\
# ── 筛选策略：强势趋势 ────────────────────────────────────────
# 逻辑：MA10 > MA20 > MA60 多头排列 + 近 20 日涨幅 > 10%

def initialize(context):
    context.name = "强势趋势"
    context.min_candles = 120


def filter(context, code, name, candles, weekly):
    closes = closes_of(candles)
    cur = closes[-1]

    ma10 = MA(closes, 10)
    ma20 = MA(closes, 20)
    ma60 = MA(closes, 60)
    if any(x != x for x in [ma10, ma20, ma60]):
        return None

    if not (cur > ma10 > ma20 > ma60):
        return None

    # 20 日涨幅
    gain20 = (cur - closes[-21]) / closes[-21] * 100 if len(closes) >= 21 else 0
    if gain20 < 10:
        return None

    # 评分
    score = 60
    if gain20 >= 20: score += 15
    elif gain20 >= 15: score += 10

    spread = (ma10 - ma60) / ma60 * 100  # 均线发散度
    if spread >= 5: score += 10
    score = min(100, score)

    return {
        "score": score,
        "triggers": ["多头排列", f"20日+{gain20:.1f}%"],
        "rr_ratio": 2.0,
    }
'''

# ─────────────────────── 放量突破压力位 ───────────────────────

TEMPLATE_VOLUME_BREAKOUT = '''\
# ── 筛选策略：放量突破压力位 ────────────────────────────────────
# 逻辑：当日收盘突破近期压力位 + 量比 >= 1.5

def initialize(context):
    context.name = "放量突破压力位"
    context.min_candles = 120


def filter(context, code, name, candles, weekly):
    closes = closes_of(candles)
    cur = closes[-1]
    prev = closes[-2] if len(closes) >= 2 else cur

    vr = volume_ratio(candles, 5)
    if vr < 1.5:
        return None

    # 找到刚被突破的压力位（昨日还在它下面，今天上去了）
    levels = get_sr_levels(candles)
    resistances = [lv for lv in levels if lv.kind == "resistance"]
    broken = None
    for r in resistances:
        if prev < r.price <= cur:
            broken = r
            break
    if not broken:
        return None

    score = 60
    if vr >= 2.0: score += 15
    elif vr >= 1.7: score += 10

    change_pct = (cur - prev) / prev * 100
    if change_pct >= 3: score += 10

    score = min(100, score)

    return {
        "score": score,
        "triggers": [f"突破{broken.price:.2f}", f"量比{vr:.2f}"],
        "breakout_price": broken.price,
        "volume_ratio": vr,
        "rr_ratio": 2.5,
    }
'''


# ─────────────────────── 模板注册表 ───────────────────────

SCREENER_TEMPLATES = {
    "default": {"label": "突破回踩（示例）", "code": TEMPLATE_DEFAULT},
    "macd_divergence": {"label": "MACD 底背离", "code": TEMPLATE_MACD_DIVERGENCE},
    "ma_support": {"label": "均线支撑", "code": TEMPLATE_MA_SUPPORT},
    "trend_strong": {"label": "强势趋势", "code": TEMPLATE_TREND_STRONG},
    "volume_breakout_resistance": {"label": "放量突破压力位", "code": TEMPLATE_VOLUME_BREAKOUT},
}
