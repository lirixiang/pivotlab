"""Tencent Finance real-time data provider (qt.gtimg.cn).

Fast, reliable source for A-share quotes and index data.
Used as the primary real-time data source; akshare is the fallback for
historical candles.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_HEADERS = {
    "Referer": "https://finance.qq.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}
_TIMEOUT = 12
_BATCH = 100


def _sym(code: str) -> str:
    """Convert 6-digit code to Tencent symbol (sh/sz prefix)."""
    return f"sh{code}" if code[:1] in ("5", "6", "9") else f"sz{code}"


def _safe_float(s: str, default: float = 0.0) -> float:
    try:
        return float(s) if s else default
    except ValueError:
        return default


def _parse_line(line: str) -> Optional[dict]:
    """Parse one 'v_shXXXXXX=...' line into a quote dict."""
    line = line.strip().rstrip(";")
    m = re.match(r'v_\w+="(.*)"$', line)
    if not m:
        return None
    data = m.group(1)
    if not data or "pv_none_match" in data:
        return None
    f = data.split("~")
    if len(f) < 40:
        return None
    code = f[2].strip()
    name = f[1].strip()
    if not code or not name:
        return None
    price = _safe_float(f[3])

    amount = 0.0
    if len(f) > 35 and "/" in f[35]:
        parts = f[35].split("/")
        if len(parts) >= 3:
            amount = _safe_float(parts[2])

    return {
        "code": code,
        "name": name,
        "price": price,
        "prev_close": _safe_float(f[4], price),
        "open": _safe_float(f[5], price),
        "volume": _safe_float(f[6]) * 100,  # lots → shares
        "change_amt": _safe_float(f[31]),
        "change_pct": _safe_float(f[32]),
        "high": _safe_float(f[33], price),
        "low": _safe_float(f[34], price),
        "amount": amount,
        "turnover_rate": _safe_float(f[38] if len(f) > 38 else ""),
    }


def fetch_quotes(codes: list[str]) -> list[dict]:
    """Batch-fetch real-time quotes from Tencent for 6-digit codes."""
    results: list[dict] = []
    for i in range(0, len(codes), _BATCH):
        batch = codes[i: i + _BATCH]
        symbols = ",".join(_sym(c) for c in batch)
        try:
            resp = requests.get(
                f"https://qt.gtimg.cn/q={symbols}",
                headers=_HEADERS,
                timeout=_TIMEOUT,
            )
            resp.encoding = "gbk"
            for line in resp.text.strip().split("\n"):
                q = _parse_line(line)
                if q:
                    results.append(q)
        except Exception as e:
            logger.warning("tencent batch %d–%d failed: %s", i, i + _BATCH, e)
    return results


def fetch_index_quotes() -> list[dict]:
    """Fetch real-time data for the main A-share indices."""
    specs = [
        ("sh000001", "上证指数"),
        ("sz399001", "深证成指"),
        ("sz399006", "创业板指"),
    ]
    symbols = ",".join(s for s, _ in specs)
    try:
        resp = requests.get(
            f"https://qt.gtimg.cn/q={symbols}",
            headers=_HEADERS,
            timeout=_TIMEOUT,
        )
        resp.encoding = "gbk"
        results = []
        for line in resp.text.strip().split("\n"):
            q = _parse_line(line)
            if q:
                results.append({
                    "code": q["code"],
                    "name": q["name"],
                    "price": q["price"],
                    "change_pct": q["change_pct"],
                })
        return results
    except Exception as e:
        logger.warning("tencent index fetch failed: %s", e)
        return []
