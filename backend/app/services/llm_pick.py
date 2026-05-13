"""LLM精选 — quantitative filtering for LLM-generated stock candidates.

Two modes:
1. validate(codes) — take a list of codes, run quant filters, return scored results
2. generate(prompt) — call LLM API, parse candidates, then validate
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# ── LLM provider config (env vars) ──────────────────────────────────────

LLM_PROVIDERS = {
    "deepseek": {
        "label": "DeepSeek-V3",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "env_key": "DEEPSEEK_API_KEY",
    },
    "doubao": {
        "label": "豆包 (Doubao Pro)",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "",  # user sets DOUBAO_MODEL_ID
        "env_key": "DOUBAO_API_KEY",
        "model_env": "DOUBAO_MODEL_ID",
    },
    "qwen_flash": {
        "label": "Qwen3.6-Flash (免费)",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen3.6-flash",
        "env_key": "QWEN_API_KEY",
    },
    "qwen": {
        "label": "通义千问 (Qwen-Max)",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-max",
        "env_key": "QWEN_API_KEY",
    },
    "glm": {
        "label": "智谱 GLM-4-Plus",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-plus",
        "env_key": "GLM_API_KEY",
    },
    "geekplus": {
        "label": "Geekplus LLM Gateway",
        "base_url": "https://llm.geekplus.com/v1",
        "model": "claude-sonnet-4-6",
        "env_key": "GEEKPLUS_API_KEY",
    },
}

DEFAULT_PROMPT = """你是一位资深A股研究员。请基于当前宏观环境、产业政策、行业景气度变化，
列出当前市场预期最好的15只股票（排除ST、次新股上市不满6个月）。

对每只股票，请给出：
1. 股票代码（6位纯数字）
2. 股票名称
3. 预期核心逻辑（一句话，30字以内）
4. 可能被证伪的风险点（一句话，30字以内）
5. 所属主题/赛道标签（如：AI算力、消费复苏、新能源等）

请严格按以下JSON数组格式输出，不要添加任何其他文字：
[
  {"code": "600519", "name": "贵州茅台", "logic": "消费复苏+提价预期", "risk": "社零持续低迷", "theme": "消费复苏"},
  ...
]"""


# ── Data classes ─────────────────────────────────────────────────────────

@dataclass
class QuantScore:
    code: str
    name: str
    # LLM fields
    logic: str = ""
    risk: str = ""
    theme: str = ""
    # Price & quote
    close: float = 0.0
    change_pct: float | None = None
    amount: float | None = None
    turnover_rate: float | None = None
    market_cap: float | None = None
    # Valuation
    pe_ratio: float | None = None
    pe_percentile: float | None = None   # PE in 3yr history (0~1)
    pb_ratio: float | None = None
    # Fundamentals
    roe: float | None = None
    revenue_yoy: float | None = None
    net_profit_yoy: float | None = None
    # Flow / crowding
    amount_ratio: float | None = None     # 个股成交额 / 全市场成交额
    amount_pctile: float | None = None    # amount_ratio 在历史中的分位
    vol_ma5_ratio: float | None = None    # 近5日均量 / 近20日均量
    # Technical
    ma5: float | None = None
    ma10: float | None = None
    ma20: float | None = None
    ma_aligned: bool = False              # ma5 > ma10 > ma20
    above_ma20: bool = False              # close > ma20
    # Filter results
    pass_valuation: bool = True
    pass_flow: bool = True
    pass_crowding: bool = True
    pass_technical: bool = True
    passed: bool = True
    total_score: float = 0.0   # 0~100 composite
    fail_reasons: list[str] = field(default_factory=list)
    # Industry / concepts
    industry: str = ""
    market: str = ""
    concepts: list[str] = field(default_factory=list)
    sparkline: list[float] = field(default_factory=list)


def _to_dict(qs: QuantScore) -> dict:
    d = asdict(qs)
    return d


# ── Quant validation engine ─────────────────────────────────────────────

def validate_candidates(
    engine: Engine,
    candidates: list[dict],
    *,
    pe_max_pctile: float = 0.80,
    crowding_max_pctile: float = 0.90,
    require_above_ma20: bool = True,
    require_positive_flow: bool = True,
) -> list[dict]:
    """Run quant filters on a list of candidate dicts with 'code' key.
    Returns list of QuantScore dicts, sorted by total_score desc."""

    codes = [c["code"].strip().lstrip("0") if len(c.get("code", "")) > 6 else c.get("code", "").strip() for c in candidates]
    codes = [c for c in codes if c and len(c) == 6]
    if not codes:
        return []

    # Build a lookup from input
    input_map: dict[str, dict] = {}
    for c in candidates:
        code = c.get("code", "").strip()
        if len(code) == 6:
            input_map[code] = c

    with engine.connect() as conn:
        # 1) Latest candle + stock info
        ph = ",".join(f"'{c}'" for c in codes)
        rows = conn.execute(text(f"""
            SELECT d.code, s.name, d.close, d.change_pct, d.amount,
                   d.turnover_rate, d.pe_ratio, d.market_cap, d.trade_date,
                   s.industry, s.market
            FROM daily_candles d
            JOIN stocks s ON s.code = d.code
            WHERE d.code IN ({ph})
              AND d.trade_date = (
                  SELECT MAX(trade_date) FROM daily_candles WHERE code = d.code
              )
        """)).fetchall()

        latest: dict[str, dict] = {}
        for r in rows:
            latest[r[0]] = {
                "code": r[0], "name": r[1], "close": r[2],
                "change_pct": r[3], "amount": r[4], "turnover_rate": r[5],
                "pe_ratio": r[6], "market_cap": r[7], "trade_date": r[8],
                "industry": r[9] or "", "market": r[10] or "",
            }

        # 2) Financial snapshots
        fin_rows = conn.execute(text(f"""
            SELECT code, pe_ratio_ttm, roe, revenue_yoy, net_profit_yoy
            FROM financial_snapshots WHERE code IN ({ph})
        """)).fetchall()
        fins: dict[str, dict] = {}
        for r in fin_rows:
            fins[r[0]] = {"pe_ttm": r[1], "roe": r[2], "rev_yoy": r[3], "np_yoy": r[4]}

        # 3) PE percentile (3yr history)
        pe_pctiles: dict[str, float] = {}
        for code in codes:
            pe_rows = conn.execute(text("""
                SELECT pe_ratio FROM daily_candles
                WHERE code = :code AND pe_ratio IS NOT NULL AND pe_ratio > 0
                ORDER BY trade_date DESC LIMIT 750
            """), {"code": code}).fetchall()
            if pe_rows and len(pe_rows) >= 20:
                vals = sorted(v[0] for v in pe_rows)
                cur_pe = latest.get(code, {}).get("pe_ratio")
                if cur_pe and cur_pe > 0:
                    below = sum(1 for v in vals if v <= cur_pe)
                    pe_pctiles[code] = below / len(vals)

        # 4) Amount ratio & crowding
        # Total market amount for the latest trade date
        some_date = next((v["trade_date"] for v in latest.values()), None)
        total_amount = 0.0
        if some_date:
            r = conn.execute(text("""
                SELECT SUM(amount) FROM daily_candles
                WHERE trade_date = :d AND amount IS NOT NULL
            """), {"d": some_date}).fetchone()
            total_amount = r[0] if r and r[0] else 1.0

        # Historical amount percentile for each code
        amount_pctiles: dict[str, float] = {}
        for code in codes:
            amt = latest.get(code, {}).get("amount")
            if not amt or total_amount <= 0:
                continue
            ratio = amt / total_amount
            # Get historical ratios (last 60 trading days)
            hist_rows = conn.execute(text("""
                SELECT d.amount / t.total_amount AS ratio
                FROM daily_candles d
                JOIN (
                    SELECT trade_date, SUM(amount) AS total_amount
                    FROM daily_candles
                    WHERE amount IS NOT NULL
                    GROUP BY trade_date
                ) t ON d.trade_date = t.trade_date
                WHERE d.code = :code AND d.amount IS NOT NULL
                ORDER BY d.trade_date DESC LIMIT 60
            """), {"code": code}).fetchall()
            if hist_rows and len(hist_rows) >= 10:
                vals = sorted(v[0] for v in hist_rows if v[0])
                below = sum(1 for v in vals if v <= ratio)
                amount_pctiles[code] = below / len(vals)

        # 5) Technical: MA5/10/20 + sparkline
        technicals: dict[str, dict] = {}
        for code in codes:
            ma_rows = conn.execute(text("""
                SELECT close FROM daily_candles
                WHERE code = :code ORDER BY trade_date DESC LIMIT 30
            """), {"code": code}).fetchall()
            if ma_rows and len(ma_rows) >= 20:
                closes = [r[0] for r in ma_rows]
                ma5 = sum(closes[:5]) / 5
                ma10 = sum(closes[:10]) / 10
                ma20 = sum(closes[:20]) / 20
                # Volume MA ratio
                vol_rows = conn.execute(text("""
                    SELECT volume FROM daily_candles
                    WHERE code = :code ORDER BY trade_date DESC LIMIT 20
                """), {"code": code}).fetchall()
                vols = [r[0] for r in vol_rows if r[0]]
                vol_ma5 = sum(vols[:5]) / 5 if len(vols) >= 5 else 0
                vol_ma20 = sum(vols[:20]) / 20 if len(vols) >= 20 else 1
                technicals[code] = {
                    "ma5": round(ma5, 3), "ma10": round(ma10, 3), "ma20": round(ma20, 3),
                    "ma_aligned": ma5 > ma10 > ma20,
                    "above_ma20": closes[0] > ma20,
                    "vol_ma5_ratio": round(vol_ma5 / vol_ma20, 2) if vol_ma20 > 0 else 1.0,
                    "sparkline": list(reversed(closes[:20])),
                }

        # 6) Concepts
        concept_map: dict[str, list[str]] = {}
        concept_rows = conn.execute(text(f"""
            SELECT code, concept FROM stock_concepts WHERE code IN ({ph})
        """)).fetchall()
        for r in concept_rows:
            concept_map.setdefault(r[0], []).append(r[1])

    # ── Assemble & score ──
    results: list[QuantScore] = []
    for code in codes:
        lt = latest.get(code)
        if not lt:
            continue
        inp = input_map.get(code, {})
        fin = fins.get(code, {})
        tech = technicals.get(code, {})

        qs = QuantScore(
            code=code,
            name=lt["name"],
            logic=inp.get("logic", ""),
            risk=inp.get("risk", ""),
            theme=inp.get("theme", ""),
            close=lt["close"],
            change_pct=lt.get("change_pct"),
            amount=lt.get("amount"),
            turnover_rate=lt.get("turnover_rate"),
            market_cap=lt.get("market_cap"),
            pe_ratio=lt.get("pe_ratio"),
            pe_percentile=pe_pctiles.get(code),
            roe=fin.get("roe"),
            revenue_yoy=fin.get("rev_yoy"),
            net_profit_yoy=fin.get("np_yoy"),
            amount_ratio=(lt["amount"] / total_amount) if lt.get("amount") and total_amount > 0 else None,
            amount_pctile=amount_pctiles.get(code),
            vol_ma5_ratio=tech.get("vol_ma5_ratio"),
            ma5=tech.get("ma5"),
            ma10=tech.get("ma10"),
            ma20=tech.get("ma20"),
            ma_aligned=tech.get("ma_aligned", False),
            above_ma20=tech.get("above_ma20", False),
            industry=lt.get("industry", ""),
            market=lt.get("market", ""),
            concepts=concept_map.get(code, []),
            sparkline=tech.get("sparkline", []),
        )

        # ── Apply filters ──
        fails: list[str] = []

        # Filter 1: Valuation (PE percentile > threshold → too expensive)
        if qs.pe_percentile is not None and qs.pe_percentile > pe_max_pctile:
            qs.pass_valuation = False
            fails.append(f"PE分位{qs.pe_percentile:.0%}偏高")

        # Filter 2: Flow (volume shrinking → no buying power)
        if require_positive_flow and qs.vol_ma5_ratio is not None and qs.vol_ma5_ratio < 0.7:
            qs.pass_flow = False
            fails.append(f"量能萎缩(5/20日量比={qs.vol_ma5_ratio:.2f})")

        # Filter 3: Crowding (amount percentile too high)
        if qs.amount_pctile is not None and qs.amount_pctile > crowding_max_pctile:
            qs.pass_crowding = False
            fails.append(f"拥挤度{qs.amount_pctile:.0%}过高")

        # Filter 4: Technical (below MA20 → downtrend)
        if require_above_ma20 and not qs.above_ma20 and qs.ma20 is not None:
            qs.pass_technical = False
            fails.append("跌破20日均线")

        qs.fail_reasons = fails
        qs.passed = len(fails) == 0

        # Composite score (0~100)
        score = 50.0
        # Valuation bonus/penalty
        if qs.pe_percentile is not None:
            score += (0.5 - qs.pe_percentile) * 30  # low PE → bonus
        # Technical bonus
        if qs.ma_aligned:
            score += 10
        if qs.above_ma20:
            score += 5
        # Growth bonus
        if qs.revenue_yoy and qs.revenue_yoy > 10:
            score += min(qs.revenue_yoy * 0.3, 10)
        if qs.roe and qs.roe > 10:
            score += min(qs.roe * 0.3, 8)
        # Flow
        if qs.vol_ma5_ratio and qs.vol_ma5_ratio > 1.2:
            score += 5
        # Crowding penalty
        if qs.amount_pctile and qs.amount_pctile > 0.8:
            score -= (qs.amount_pctile - 0.8) * 50
        qs.total_score = max(0, min(100, round(score, 1)))

        results.append(qs)

    results.sort(key=lambda x: (-int(x.passed), -x.total_score))
    return [_to_dict(r) for r in results]


# ── LLM caller (uses agent's unified LLM factory) ───────────────────────

def get_available_providers() -> list[dict]:
    """Return list of configured LLM providers from agent factory."""
    from app.agent.llm.factory import list_available_providers as _list
    providers = _list()
    return [
        {"key": p["provider"], "label": p["provider"], "configured": p["available"]}
        for p in providers
    ]


def call_llm(
    provider: str = "deepseek",
    prompt: str = "",
    *,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Call LLM API via agent factory and parse stock candidates from response.
    Returns {"candidates": [...], "raw_response": str, "provider": str, "error": str|None}
    """
    import asyncio
    from app.agent.llm.factory import build_llm
    from app.agent.core.types import Message

    user_prompt = prompt or DEFAULT_PROMPT

    try:
        llm = build_llm(provider=provider)
    except RuntimeError as e:
        return {"candidates": [], "raw_response": "", "provider": provider, "error": str(e)}

    messages = [
        Message(role="system", content="你是一位资深A股投资研究员，专注于基本面分析和市场趋势研判。请用中文回答。"),
        Message(role="user", content=user_prompt),
    ]

    try:
        # build_llm returns async client — run in event loop
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # Already in async context — create a new thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                resp = pool.submit(
                    lambda: asyncio.run(llm.chat(messages, temperature=0.3, max_tokens=4096))
                ).result(timeout=timeout)
        else:
            resp = asyncio.run(llm.chat(messages, temperature=0.3, max_tokens=4096))

        content = resp.message.content or ""
        candidates = _parse_candidates(content)
        return {
            "candidates": candidates,
            "raw_response": content,
            "provider": provider,
            "model": llm.model,
            "error": None,
        }
    except Exception as e:
        return {"candidates": [], "raw_response": "", "provider": provider,
                "error": f"调用失败: {str(e)[:200]}"}


def _parse_candidates(text_content: str) -> list[dict]:
    """Parse stock candidates from LLM response text."""
    import re

    def _clean_code(raw: str) -> str:
        """Strip exchange suffixes like .SZ .SH and keep 6-digit code."""
        c = re.sub(r'\.(SZ|SH|BJ|sz|sh|bj)$', '', str(raw).strip())
        return c

    def _extract_list(obj) -> list[dict] | None:
        """Given parsed JSON (list or dict), find the stock list."""
        if isinstance(obj, list):
            return obj
        if isinstance(obj, dict):
            # Look for the first list-valued key
            for v in obj.values():
                if isinstance(v, list) and v:
                    return v
        return None

    def _normalize(arr: list) -> list[dict]:
        result = []
        for item in arr:
            if not isinstance(item, dict):
                continue
            code = item.get("code") or item.get("stock_code") or ""
            if not code:
                continue
            logic = item.get("logic") or item.get("buy_logic") or item.get("reason") or ""
            risk = item.get("risk") or item.get("risk_warning") or item.get("risk_point") or ""
            theme = item.get("theme") or item.get("policy_industry_catalyst") or item.get("sector") or ""
            result.append({
                "code": _clean_code(code),
                "name": str(item.get("name", "")),
                "logic": str(logic)[:200],
                "risk": str(risk)[:200],
                "theme": str(theme)[:200],
            })
        return result

    patterns = [
        r'```json\s*([\s\S]*?)```',
        r'```\s*([\s\S]*?)```',
        r'(\{[\s\S]*\})',
        r'(\[[\s\S]*\])',
    ]
    for pat in patterns:
        m = re.search(pat, text_content)
        if m:
            try:
                parsed = json.loads(m.group(1))
                arr = _extract_list(parsed)
                if arr:
                    result = _normalize(arr)
                    if result:
                        return result
            except json.JSONDecodeError:
                continue
    return []
