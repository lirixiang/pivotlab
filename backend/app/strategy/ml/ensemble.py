"""Ensemble scorer that blends rule-based (v1) + LightGBM + sequence model.

Used by the new `ai_ensemble` style. If neither LGBM nor the sequence model
is trained, the ensemble gracefully degrades to rule-only and tags the
result so the UI can show "(尚未训练 ML 模型)".
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from ..features import FeatureSet
from ..styles import score_multi_factor
from . import lgbm, sequence

logger = logging.getLogger(__name__)


@dataclass
class EnsembleParts:
    rule: float
    lgbm: float | None
    seq: float | None


def score_ai_ensemble(
    fs: FeatureSet,
    seq_window: np.ndarray | None = None,
) -> tuple[float, list[str], dict]:
    """Return (score, reasons, factors).

    Weights:
        - rule:  0.35
        - lgbm:  0.40 (if trained, else redistribute)
        - seq :  0.25 (if trained, else redistribute)
    """
    rule_score, rule_reasons, rule_factors = score_multi_factor(fs)

    lgbm_score = lgbm.predict_score_0_100(fs) if lgbm.is_trained() else None
    seq_score = (sequence.predict_score_0_100(seq_window)
                 if (seq_window is not None and sequence.is_trained()) else None)

    weights = {"rule": 0.35, "lgbm": 0.40, "seq": 0.25}
    parts: dict[str, float] = {"rule": rule_score}
    if lgbm_score is not None:
        parts["lgbm"] = lgbm_score
    if seq_score is not None:
        parts["seq"] = seq_score

    # Renormalise weights over available parts
    w = {k: weights[k] for k in parts}
    s = sum(w.values())
    w = {k: v / s for k, v in w.items()}

    blended = sum(parts[k] * w[k] for k in parts)

    reasons: list[str] = []
    if lgbm_score is None and seq_score is None:
        reasons.append("AI 模型尚未训练,使用规则评分")
    else:
        reasons.append(f"规则 {rule_score:.0f}")
        if lgbm_score is not None:
            reasons.append(f"LGBM {lgbm_score:.0f}")
        if seq_score is not None:
            reasons.append(f"TCN {seq_score:.0f}")
    # Pull through 1-2 most informative rule reasons
    reasons.extend(rule_reasons[:2])

    factors = {
        "rule": rule_score,
        "lgbm": lgbm_score if lgbm_score is not None else -1,
        "seq": seq_score if seq_score is not None else -1,
        "weights": w,
        "rule_factors": rule_factors,
    }

    return float(np.clip(blended, 0, 100)), reasons, factors


def status() -> dict:
    return {
        "lgbm_trained": lgbm.is_trained(),
        "seq_trained": sequence.is_trained(),
    }
