"""Filesystem-backed model registry.

Models live under ``backend/data/models/<name>/<artifact>``.  We keep one
"current" symlink per model name so the predictor can blindly load the
latest trained version.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# backend/app/strategy/ml/registry.py -> backend/
_BASE = Path(__file__).resolve().parents[3] / "data" / "models"
_BASE.mkdir(parents=True, exist_ok=True)


def model_dir(name: str, *, create: bool = True) -> Path:
    p = _BASE / name
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p


def write_meta(name: str, meta: dict) -> None:
    meta = {**meta, "saved_at": datetime.utcnow().isoformat()}
    (model_dir(name) / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2)
    )


def read_meta(name: str) -> dict | None:
    f = model_dir(name, create=False) / "meta.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text())
    except Exception:
        return None


def list_models() -> dict[str, dict | None]:
    out: dict[str, dict | None] = {}
    if not _BASE.exists():
        return out
    for p in sorted(_BASE.iterdir()):
        if p.is_dir():
            out[p.name] = read_meta(p.name)
    return out


def base_dir() -> Path:
    return _BASE
