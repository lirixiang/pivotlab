"""Load system prompts from disk."""
from __future__ import annotations

from pathlib import Path

_DIR = Path(__file__).parent


def load_system_prompt(name: str = "system") -> str:
    p = _DIR / f"{name}.md"
    return p.read_text(encoding="utf-8")
