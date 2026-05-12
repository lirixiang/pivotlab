"""Golden-question regression suite for the agent.

Each question has:
  - id, prompt
  - expect: list of substrings that MUST appear in the final answer (case-insensitive)
  - expect_tools: list of tool names that SHOULD be invoked (subset OK)
  - tags: free-form labels
"""
from __future__ import annotations

import yaml
from pathlib import Path

GOLDEN_FILE = Path(__file__).parent / "golden.yaml"


def load_golden() -> list[dict]:
    if not GOLDEN_FILE.exists():
        return []
    return yaml.safe_load(GOLDEN_FILE.read_text(encoding="utf-8")) or []
