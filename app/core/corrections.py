"""Persisted log of operator-made word corrections.

This does not retrain the speech model -- that's not something a desktop
app can do locally. What it actually does: each correction is added to the
custom vocabulary (see hotwords.py) so the engine is boosted toward
recognizing that word correctly next time, and logged here so the operator
can see what they've corrected and how often a given mistake recurs.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

MAX_ENTRIES = 500


def load_corrections(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def add_correction(path: str | Path, original: str, corrected: str) -> list[dict]:
    entries = load_corrections(path)
    entries.append({"original": original, "corrected": corrected, "ts": time.time()})
    entries = entries[-MAX_ENTRIES:]
    Path(path).write_text(json.dumps(entries, indent=2), encoding="utf-8")
    return entries
