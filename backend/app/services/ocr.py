"""Lightweight OCR helpers for stock-code extraction from screenshots.

Uses PaddleOCR (Chinese model) loaded lazily on first call. Model files
are bundled in `ocr_models/{det,rec}` so no network download is needed.
Accepts raw image bytes and returns A-share style 6-digit codes.
"""
from __future__ import annotations

import io
import logging
import os
import re
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

_engine: Any = None
_lock = Lock()

_MODEL_DIR = os.path.join(os.path.dirname(__file__), "ocr_models")
_DET_DIR = os.path.join(_MODEL_DIR, "det")
_REC_DIR = os.path.join(_MODEL_DIR, "rec")
_CLS_DIR = os.path.join(_MODEL_DIR, "cls")

# Valid prefixes for A-share + 北交所 codes.
_VALID_PREFIXES = (
    "60", "68", "00", "30",          # 沪深主板/科创/中小/创业
    "43", "83", "87", "88", "92",    # 北交所
)
# Match 6 contiguous digits not surrounded by other digits.
_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")


def _get_engine():
    global _engine
    if _engine is not None:
        return _engine
    with _lock:
        if _engine is None:
            from paddleocr import PaddleOCR  # type: ignore
            kwargs: dict[str, Any] = dict(use_angle_cls=False, lang="ch", show_log=False)
            if os.path.isdir(_DET_DIR) and os.path.isdir(_REC_DIR):
                kwargs["det_model_dir"] = _DET_DIR
                kwargs["rec_model_dir"] = _REC_DIR
                if os.path.isdir(_CLS_DIR):
                    kwargs["cls_model_dir"] = _CLS_DIR
                logger.info("Initializing PaddleOCR with bundled models from %s", _MODEL_DIR)
            else:
                logger.info("Bundled OCR models not found, PaddleOCR will download defaults")
            _engine = PaddleOCR(**kwargs)
            logger.info("PaddleOCR ready.")
    return _engine


def extract_codes_from_image(data: bytes) -> list[dict]:
    """OCR the image bytes and return a list of dicts:
    [{"code": "600519", "text": "<line>", "confidence": 0.97}, ...]
    Codes are deduplicated keeping the highest-confidence occurrence,
    and ordered by first appearance (top-to-bottom)."""
    engine = _get_engine()

    import numpy as np
    from PIL import Image

    img = Image.open(io.BytesIO(data)).convert("RGB")
    arr = np.array(img)
    result = engine.ocr(arr, cls=False)
    if not result or not result[0]:
        return []

    seen: dict[str, dict] = {}
    order: list[str] = []
    for line in result[0]:
        try:
            text, score = line[1]
        except (TypeError, IndexError, ValueError):
            continue
        if not isinstance(text, str):
            continue
        for m in _CODE_RE.finditer(text):
            code = m.group(1)
            if not code.startswith(_VALID_PREFIXES):
                continue
            prev = seen.get(code)
            if prev is None:
                seen[code] = {"code": code, "text": text.strip(), "confidence": float(score)}
                order.append(code)
            elif score > prev["confidence"]:
                prev["confidence"] = float(score)
                prev["text"] = text.strip()
    return [seen[c] for c in order]
