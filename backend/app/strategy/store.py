"""Persist Recommendations + TradePlans to the database."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select, delete
from sqlalchemy.orm import Session

from ..models import Recommendation, TradePlan
from .trade_plan import TradePlanData


def save_batch(
    sync_session: Session,
    *,
    style: str,
    scan_date: str,
    items: list[dict],
) -> int:
    """Replace today's recommendations for a given style.

    Each item dict must contain:
      code, name, score, rank, price, industry, concept,
      reasons (list[str]), factors (dict), plan (TradePlanData), expires_date
    """
    # Wipe today's records for this style (idempotent re-run)
    sync_session.execute(
        delete(Recommendation).where(
            Recommendation.style == style,
            Recommendation.scan_date == scan_date,
        )
    )
    # Cascade-delete linked plans created by previous run for this style on this date
    # (we look them up via the recommendation IDs after recreating below)
    sync_session.commit()

    inserted = 0
    for it in items:
        reco = Recommendation(
            code=it["code"],
            name=it.get("name", ""),
            style=style,
            score=float(it["score"]),
            rank=int(it["rank"]),
            price=float(it.get("price", 0)),
            industry=it.get("industry", ""),
            concept=it.get("concept", ""),
            reasons=list(it.get("reasons", [])),
            factors=dict(it.get("factors", {})),
            scan_date=scan_date,
            expires_date=it.get("expires_date", ""),
            status="active",
        )
        sync_session.add(reco)
        sync_session.flush()  # to get reco.id

        plan: TradePlanData = it["plan"]
        # Stash new tradability fields inside the factors JSON so we don't
        # need a DB migration for the new columns.
        plan_factors = dict(plan.factors)
        plan_factors["state"] = getattr(plan, "state", "buy")
        plan_factors["tradable"] = getattr(plan, "tradable", True)
        plan_factors["risk_warning"] = getattr(plan, "risk_warning", "")
        # Remove any orphan plans from prior runs with same code+style
        sync_session.execute(
            delete(TradePlan).where(
                TradePlan.code == it["code"],
                TradePlan.style == style,
            )
        )
        tp = TradePlan(
            recommendation_id=reco.id,
            code=it["code"],
            style=style,
            buy_low=plan.buy_low,
            buy_high=plan.buy_high,
            buy_trigger=plan.buy_trigger,
            stop_loss=plan.stop_loss,
            take_profit_1=plan.take_profit_1,
            take_profit_2=plan.take_profit_2,
            position_pct=plan.position_pct,
            holding_days_min=plan.holding_days_min,
            holding_days_max=plan.holding_days_max,
            risk_reward=plan.risk_reward,
            atr_pct=plan.atr_pct,
            confidence=plan.confidence,
            reason=plan.reason,
            factors=plan_factors,
        )
        sync_session.add(tp)
        inserted += 1

    sync_session.commit()
    return inserted


def list_recent(
    sync_session: Session,
    *,
    style: str | None = None,
    scan_date: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Return recent recommendations joined with their plans."""
    stmt = select(Recommendation)
    if style:
        stmt = stmt.where(Recommendation.style == style)
    if scan_date:
        stmt = stmt.where(Recommendation.scan_date == scan_date)
    stmt = stmt.order_by(
        Recommendation.scan_date.desc(),
        Recommendation.rank.asc(),
    ).limit(limit)

    recos = sync_session.execute(stmt).scalars().all()
    if not recos:
        return []

    # Load matching plans by recommendation_id
    ids = [r.id for r in recos]
    plans = sync_session.execute(
        select(TradePlan).where(TradePlan.recommendation_id.in_(ids))
    ).scalars().all()
    plan_by_rid = {p.recommendation_id: p for p in plans}

    out = []
    for r in recos:
        p = plan_by_rid.get(r.id)
        rk = r.rank or 9999
        tier = "core" if rk <= 20 else ("watch" if rk <= 100 else "observe")
        out.append({
            "id": r.id,
            "code": r.code,
            "name": r.name,
            "style": r.style,
            "score": r.score,
            "rank": r.rank,
            "tier": tier,
            "price": r.price,
            "industry": r.industry,
            "concept": r.concept,
            "reasons": r.reasons or [],
            "factors": r.factors or {},
            "scan_date": r.scan_date,
            "expires_date": r.expires_date,
            "status": r.status,
            "plan": _plan_dict(p) if p else None,
        })
    return out


def get_for_code(sync_session: Session, code: str, style: str | None = None) -> list[dict]:
    """All active recommendations for a code (one per style at most)."""
    stmt = select(Recommendation).where(Recommendation.code == code)
    if style:
        stmt = stmt.where(Recommendation.style == style)
    stmt = stmt.order_by(Recommendation.scan_date.desc()).limit(20)
    recos = sync_session.execute(stmt).scalars().all()
    if not recos:
        return []
    ids = [r.id for r in recos]
    plans = sync_session.execute(
        select(TradePlan).where(TradePlan.recommendation_id.in_(ids))
    ).scalars().all()
    plan_by_rid = {p.recommendation_id: p for p in plans}
    return [{
        "id": r.id, "code": r.code, "name": r.name, "style": r.style,
        "score": r.score, "rank": r.rank, "price": r.price,
        "industry": r.industry, "concept": r.concept,
        "reasons": r.reasons or [], "factors": r.factors or {},
        "scan_date": r.scan_date, "expires_date": r.expires_date,
        "status": r.status,
        "plan": _plan_dict(plan_by_rid.get(r.id)) if plan_by_rid.get(r.id) else None,
    } for r in recos]


def _plan_dict(p: TradePlan | None) -> dict | None:
    if not p:
        return None
    factors = p.factors or {}
    return {
        "buy_low": p.buy_low, "buy_high": p.buy_high, "buy_trigger": p.buy_trigger,
        "stop_loss": p.stop_loss,
        "take_profit_1": p.take_profit_1, "take_profit_2": p.take_profit_2,
        "position_pct": p.position_pct,
        "holding_days_min": p.holding_days_min, "holding_days_max": p.holding_days_max,
        "risk_reward": p.risk_reward, "atr_pct": p.atr_pct,
        "confidence": p.confidence,
        "reason": p.reason, "factors": factors,
        # Surface new tradability fields for the frontend
        "state": factors.get("state", "buy"),
        "tradable": factors.get("tradable", True),
        "risk_warning": factors.get("risk_warning", ""),
    }
