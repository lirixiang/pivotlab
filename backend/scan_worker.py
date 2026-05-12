#!/usr/bin/env python3
"""AI Stock Scanner — runs as subprocess, scans stocks for buy/sell signals.

Usage:
    CUDA_VISIBLE_DEVICES=0 python scan_worker.py /path/to/scan_task.json

Task JSON:
  { "task_id", "scope", "scope_code", "model_types",
    "buy_threshold", "sell_threshold", "progress_file" }

Progress is written to progress_file as JSON, polled by the API server.
"""
import json
import os
import sys
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s :: %(message)s")
logger = logging.getLogger("scan_worker")


def write_progress(path: str, **kw):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(kw, f, ensure_ascii=False)
    os.replace(tmp, path)


_base_kw: dict = {}


def wprog(path: str, **kw):
    merged = {**_base_kw, **kw}
    write_progress(path, **merged)


def _consolidate(agg: dict, available_models: list,
                 min_agreement: float = 0.0,
                 min_rating: float = 0.0) -> list[dict]:
    """Reduce per-stock model_hits into a single row with rating/agreement/triggers.

    Optional filters:
      - min_agreement: drop rows where < this share of available models agree on direction.
      - min_rating:    drop rows whose composite rating is below this threshold.
    """
    out: list[dict] = []
    for code, cur in agg.items():
        mh = cur["model_hits"]
        if not mh:
            continue
        buys = [h for h in mh if h["action"] == "buy"]
        sells = [h for h in mh if h["action"] == "sell"]
        if len(buys) >= len(sells):
            action = "buy"
            same_dir = buys
            rep = max(buys, key=lambda h: h["confidence"]) if buys else mh[0]
        else:
            action = "sell"
            same_dir = sells
            rep = max(sells, key=lambda h: h["confidence"])
        avg_conf = sum(h["confidence"] for h in same_dir) / max(len(same_dir), 1)
        agreement = len(same_dir) / max(len(available_models), 1)
        rr = rep["risk_reward"]
        rating = (avg_conf / 100.0) * 2.5 + agreement * 1.5 + min(rr, 3.0) / 3.0 * 1.0
        # Strictness gates
        if agreement < min_agreement:
            continue
        if rating < min_rating:
            continue
        triggers: list[str] = []
        for h in same_dir:
            for f in h.get("factors", []):
                if f and f not in triggers and not f.startswith("买入概率") and not f.startswith("卖出概率"):
                    triggers.append(f)
        out.append({
            "code": code, "name": cur["name"],
            "action": action,
            "confidence": round(avg_conf, 1),
            "agreement": round(agreement, 2),
            "models_total": len(available_models),
            "models_agree": len(same_dir),
            "rating": round(rating, 2),
            "current_price": cur["current_price"],
            "entry_price": rep["entry_price"],
            "stop_loss": rep["stop_loss"],
            "target_price": rep["target_price"],
            "risk_reward": rr,
            "trend": cur["trend"],
            "sparkline": cur["sparkline"],
            "industry": cur["industry"],
            "market": cur["market"],
            "concepts": cur["concepts"],
            "pe": cur["pe"], "roe": cur["roe"],
            "market_cap": cur["market_cap"],
            "change_pct": cur["change_pct"],
            "amount": cur["amount"],
            "turnover_rate": cur["turnover_rate"],
            "fundamental_status": cur["fundamental_status"],
            "triggers": triggers[:6],
            "model_hits": [
                {"model": h["model"], "action": h["action"],
                 "confidence": h["confidence"], "rr": h["risk_reward"]}
                for h in mh
            ],
            "model": rep["model"],
            "reason": rep["reason"],
        })
    out.sort(key=lambda x: (x["rating"], x["confidence"]), reverse=True)
    return out


def main():
    global _base_kw
    if len(sys.argv) < 2:
        print("Usage: python scan_worker.py <task.json>")
        sys.exit(1)

    with open(sys.argv[1]) as f:
        task = json.load(f)

    task_id = task["task_id"]
    scope = task.get("scope", "watchlist")  # watchlist / industry / cached
    scope_code = task.get("scope_code", "")
    model_types = task.get("model_types", ["lightgbm"])
    buy_threshold = task.get("buy_threshold", 0.55)
    sell_threshold = task.get("sell_threshold", 0.55)
    min_agreement = float(task.get("min_agreement", 0.5))
    min_rating = float(task.get("min_rating", 2.0))
    pf = task["progress_file"]
    t0 = time.time()

    _base_kw = dict(task_id=task_id, scope=scope, started_at=t0,
                    ended_at=None, results=[], total=0, scanned=0)

    logger.info("Scanner pid=%d task=%s scope=%s models=%s",
                os.getpid(), task_id, scope, model_types)

    wprog(pf, status="loading", progress=0, message="加载股票列表...")

    # ── Determine stock codes to scan ──
    enrich_map: dict[str, dict] = {}
    try:
        from sqlalchemy import create_engine, text
        from sqlalchemy.orm import Session
        sync_url = os.environ.get("DATABASE_URL", "").replace("+asyncpg", "+psycopg2")
        engine = create_engine(sync_url)

        codes_names: list[tuple[str, str]] = []

        if scope == "watchlist":
            with Session(engine) as s:
                rows = s.execute(text("SELECT code, name FROM watchlist ORDER BY code")).fetchall()
                codes_names = [(r[0], r[1]) for r in rows]

        elif scope == "industry":
            with Session(engine) as s:
                # Get industry of scope_code
                ind = s.execute(text("SELECT industry FROM stocks WHERE code = :c"),
                                {"c": scope_code}).scalar()
                if ind:
                    rows = s.execute(text(
                        "SELECT s.code, s.name FROM stocks s "
                        "INNER JOIN (SELECT code FROM daily_candles GROUP BY code HAVING COUNT(*) >= 100) dc "
                        "ON s.code = dc.code "
                        "WHERE s.industry = :ind AND s.is_st = false ORDER BY s.code"
                    ), {"ind": ind}).fetchall()
                    codes_names = [(r[0], r[1]) for r in rows]

        elif scope == "cached":
            with Session(engine) as s:
                rows = s.execute(text(
                    "SELECT s.code, s.name FROM stocks s "
                    "INNER JOIN (SELECT code FROM daily_candles GROUP BY code HAVING COUNT(*) >= 100) dc "
                    "ON s.code = dc.code "
                    "WHERE s.is_st = false ORDER BY s.code"
                )).fetchall()
                codes_names = [(r[0], r[1]) for r in rows]

        if not codes_names:
            wprog(pf, status="failed", progress=0, message="没有找到可扫描的股票",
                  ended_at=time.time())
            sys.exit(1)

        # ── Batch enrichment: industry/market/concepts/fundamentals/quote ──
        try:
            with Session(engine) as s:
                code_list = [c for c, _ in codes_names]
                # Stocks: industry/market
                rows = s.execute(text(
                    "SELECT code, industry, market FROM stocks WHERE code = ANY(:codes)"
                ), {"codes": code_list}).fetchall()
                for r in rows:
                    enrich_map.setdefault(r[0], {})
                    enrich_map[r[0]].update({"industry": r[1] or "", "market": r[2] or ""})
                # Concepts: top 3 per code
                rows = s.execute(text(
                    "SELECT code, concept FROM stock_concepts WHERE code = ANY(:codes) "
                    "ORDER BY code, id"
                ), {"codes": code_list}).fetchall()
                for r in rows:
                    d = enrich_map.setdefault(r[0], {})
                    cs = d.setdefault("concepts", [])
                    if len(cs) < 3 and r[1]:
                        cs.append(r[1])
                # Latest quote: pe/market_cap/change_pct/amount/turnover_rate
                rows = s.execute(text(
                    "SELECT DISTINCT ON (code) code, pe_ratio, market_cap, change_pct, amount, turnover_rate "
                    "FROM daily_candles WHERE code = ANY(:codes) "
                    "ORDER BY code, trade_date DESC"
                ), {"codes": code_list}).fetchall()
                for r in rows:
                    d = enrich_map.setdefault(r[0], {})
                    d.update({
                        "pe": float(r[1]) if r[1] is not None else None,
                        "market_cap": float(r[2]) if r[2] is not None else None,
                        "change_pct": float(r[3]) if r[3] is not None else None,
                        "amount": float(r[4]) if r[4] is not None else None,
                        "turnover_rate": float(r[5]) if r[5] is not None else None,
                    })
                # Fundamental status
                rows = s.execute(text(
                    "SELECT code, fundamental_status, roe, pe_ratio_ttm "
                    "FROM financial_snapshots WHERE code = ANY(:codes)"
                ), {"codes": code_list}).fetchall()
                for r in rows:
                    d = enrich_map.setdefault(r[0], {})
                    d.update({
                        "fundamental_status": r[1] or "unknown",
                        "roe": float(r[2]) if r[2] is not None else None,
                    })
                    if d.get("pe") is None and r[3] is not None:
                        d["pe"] = float(r[3])
        except Exception as e:
            logger.warning("Enrichment query failed: %s", e)

        _base_kw["total"] = len(codes_names)
        logger.info("Scanning %d stocks in scope=%s", len(codes_names), scope)
        wprog(pf, status="scanning", progress=5,
              message=f"开始扫描 {len(codes_names)} 只股票...")

    except Exception as e:
        logger.exception("Failed to load stock list")
        wprog(pf, status="failed", progress=0, message=f"加载失败: {e}",
              ended_at=time.time())
        sys.exit(1)

    # ── Load candle data and run predictions ──
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from app.services.data_provider import _cache_read
        from app.services.ai_strategy import generate_ai_signal, model_status

        # Check which models are actually trained
        status = model_status()
        available_models = [mt for mt in model_types if status.get(mt, {}).get("trained")]

        if not available_models:
            wprog(pf, status="failed", progress=0,
                  message=f"没有已训练的模型 (需要: {', '.join(model_types)})",
                  ended_at=time.time())
            sys.exit(1)

        logger.info("Available models: %s", available_models)

        # Per-stock aggregation: code -> {action_votes, models[], confidences[], best hit}
        agg: dict[str, dict] = {}
        scanned = 0

        for code, name in codes_names:
            scanned += 1
            pct = 5 + int(90 * scanned / len(codes_names))

            if scanned % 10 == 0 or scanned == len(codes_names):
                wprog(pf, status="scanning", progress=pct, scanned=scanned,
                      message=f"扫描中 {scanned}/{len(codes_names)} ... 已发现 {len(agg)} 只股票",
                      results=_consolidate(agg, available_models,
                                           min_agreement=min_agreement,
                                           min_rating=min_rating))

            candles = _cache_read(code, limit=500)
            if not candles or len(candles) < 100:
                continue

            # Sparkline: last 30 closes (use raw float)
            sparkline = [round(float(c.close), 3) for c in candles[-30:]]

            stock_meta = enrich_map.get(code, {})

            for mt in available_models:
                try:
                    sig = generate_ai_signal(
                        candles,
                        model_type=mt,
                        buy_threshold=buy_threshold,
                        sell_threshold=sell_threshold,
                    )
                    if sig.get("action") in ("buy", "sell") and sig.get("confidence", 0) > 0:
                        model_hit = {
                            "model": mt,
                            "action": sig["action"],
                            "confidence": sig["confidence"],
                            "entry_price": sig["entry_price"],
                            "stop_loss": sig["stop_loss"],
                            "target_price": sig["target_price"],
                            "risk_reward": sig["risk_reward"],
                            "factors": sig.get("factors", []),
                            "reason": sig.get("reason", ""),
                        }
                        cur = agg.get(code)
                        if cur is None:
                            cur = {
                                "code": code,
                                "name": name,
                                "current_price": sig["current_price"],
                                "trend": sig.get("trend", ""),
                                "sparkline": sparkline,
                                "industry": stock_meta.get("industry", ""),
                                "market": stock_meta.get("market", ""),
                                "concepts": stock_meta.get("concepts", []),
                                "pe": stock_meta.get("pe"),
                                "roe": stock_meta.get("roe"),
                                "market_cap": stock_meta.get("market_cap"),
                                "change_pct": stock_meta.get("change_pct"),
                                "amount": stock_meta.get("amount"),
                                "turnover_rate": stock_meta.get("turnover_rate"),
                                "fundamental_status": stock_meta.get("fundamental_status", "unknown"),
                                "model_hits": [],
                            }
                            agg[code] = cur
                        cur["model_hits"].append(model_hit)
                except Exception as e:
                    logger.debug("Predict failed %s/%s: %s", code, mt, e)

        # ── Consolidate per-stock results: consensus action, avg confidence, ★ rating ──
        hits = _consolidate(agg, available_models,
                            min_agreement=min_agreement,
                            min_rating=min_rating)

        # ── Persist snapshot to history ──
        try:
            history_dir = os.path.join(os.path.dirname(pf), "history")
            os.makedirs(history_dir, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M", time.localtime(t0))
            snapshot = {
                "ts": ts,
                "task_id": task_id,
                "scope": scope,
                "scope_code": scope_code,
                "model_types": model_types,
                "buy_threshold": buy_threshold,
                "sell_threshold": sell_threshold,
                "scanned": scanned,
                "total": len(codes_names),
                "hits_total": len(hits),
                "started_at": t0,
                "ended_at": time.time(),
                "results": hits,
            }
            snap_path = os.path.join(history_dir, f"{ts}.json")
            with open(snap_path, "w") as f:
                json.dump(snapshot, f, ensure_ascii=False)
            logger.info("Snapshot saved: %s", snap_path)
        except Exception as e:
            logger.warning("Failed to save snapshot: %s", e)

        wprog(pf, status="completed", progress=100, scanned=scanned,
              message=f"扫描完成: {len(codes_names)} 只股票, 发现 {len(hits)} 只股票有信号",
              ended_at=time.time(), results=hits)
        logger.info("Done: scanned=%d hits=%d elapsed=%.1fs",
                    scanned, len(hits), time.time() - t0)

    except Exception as e:
        logger.exception("Scan failed")
        wprog(pf, status="failed", progress=0, message=f"扫描失败: {e}",
              ended_at=time.time())
        sys.exit(1)


if __name__ == "__main__":
    main()
