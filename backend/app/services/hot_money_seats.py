"""Hot money seat (游资席位) knowledge base.

Maps East Money 营业部 names to known hot money trader tags.
Used to enrich 龙虎榜 data with trader identity for the dragon strategy model.

Source: Public 营业部 trader profiles (赵老哥、章盟主、欢乐海岸 etc.).
This is a static curated list — extend as new traders become known.
"""
from __future__ import annotations

# (substring_in_seat_name, tag) — first match wins
_HOT_MONEY_KEYWORDS: list[tuple[str, str]] = [
    # 一线游资 (top tier)
    ("国泰君安证券股份有限公司上海江苏路", "赵老哥"),
    ("中国中金财富证券有限公司上海分公司", "章盟主"),
    ("华泰证券股份有限公司厦门厦禾路", "欢乐海岸"),
    ("银河证券绍兴营业部", "欢乐海岸"),
    ("中信证券股份有限公司上海溧阳路", "炒股养家"),
    ("机构专用", "机构席位"),

    # 一线游资别名
    ("江苏路", "赵老哥"),
    ("厦门厦禾路", "欢乐海岸"),
    ("溧阳路", "炒股养家"),
    ("中信上海溧阳路", "炒股养家"),
    ("中信杭州延安路", "宁波桑田路"),

    # 知名营业部
    ("宁波解放南路", "宁波解放南路"),
    ("宁波桑田路", "宁波桑田路"),
    ("拉萨", "拉萨系"),                    # 拉萨东环路 / 团结路 (东方财富散户席位 - 半游资)
    ("北京西三环", "北京三环"),
    ("方新侠", "方新侠"),
    ("作手新一", "作手新一"),
    ("葛卫东", "葛卫东"),
    ("孙哥", "孙哥"),

    # 二线游资聚集地
    ("深圳益田路荣超商务中心", "深圳益田路"),
    ("华泰证券深圳益田路", "深圳益田路"),
    ("国泰君安福州五一", "福州五一"),
    ("方正成都宁夏街", "成都系"),
    ("国信证券深圳泰然九路", "深圳泰然九路"),
    ("华林证券广州中山五路", "广州系"),
    ("华鑫证券上海分公司", "上海华鑫"),
    ("东方财富", "东财游资"),
]

# Known machine-institutional indicator
_INSTITUTIONAL_KEYWORD = "机构专用"


def classify_seat(seat_name: str) -> str:
    """Return hot money tag for the given 营业部 name, or empty string."""
    if not seat_name:
        return ""
    s = seat_name.strip()
    if _INSTITUTIONAL_KEYWORD in s:
        return "机构席位"
    for kw, tag in _HOT_MONEY_KEYWORDS:
        if kw in s:
            return tag
    return ""


def is_hot_money(seat_name: str) -> bool:
    """True if the seat is a known hot money trader (excludes 机构专用)."""
    tag = classify_seat(seat_name)
    return bool(tag) and tag != "机构席位"


def is_institutional(seat_name: str) -> bool:
    return _INSTITUTIONAL_KEYWORD in (seat_name or "")
