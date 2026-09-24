"""Persisted operator settings (config.json). No audio or transcript content lives here."""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

from app.core.dsp import EQ_BANDS_HZ
from app.core.model_download import MODEL_NAME

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

if getattr(sys, "frozen", False):
    # A PyInstaller onefile build's own folder is a temp extraction dir that
    # gets wiped every launch -- config/model/transcripts must live outside it.
    DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ATLiveCaption"
else:
    DATA_DIR = PROJECT_ROOT

DATA_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = DATA_DIR / "config.json"
MODEL_DIR_DEFAULT = str(DATA_DIR / "models" / MODEL_NAME)

THEMES = ["blue", "green", "purple", "orange"]

DEFAULT_CONFIG: dict[str, Any] = {
    "audio_device_index": None,
    "audio_device_name": None,
    "model_dir": MODEL_DIR_DEFAULT,
    "save_transcript": False,
    "transcript_dir": str(DATA_DIR / "transcripts"),
    "theme": "blue",
    "audio_gain_db": 0.0,
    "eq_band_gains_db": [0.0] * len(EQ_BANDS_HZ),
    "vocabulary": [],
    "hotwords_score": 2.5,
    "trusted_control_devices": [],
    "appearance": {
        "audience": {
            "font_family": "'Segoe UI', system-ui, -apple-system, sans-serif",
            "font_size": 64,
            "font_weight": 700,
            "text_align": "center",
            "line_height": 1.3,
            "text_color": "#ffffff",
            "background_color": "#000000",
            "background_opacity": 1.0,
            "max_lines": 3,
            "position": "bottom",
            "hold_seconds": 8,
            "fade_seconds": 1.5,
        },
        "overlay": {
            "font_family": "'Segoe UI', system-ui, -apple-system, sans-serif",
            "font_size": 44,
            "font_weight": 700,
            "text_align": "center",
            "line_height": 1.2,
            "text_color": "#ffffff",
            "background_color": "#000000",
            "background_opacity": 0.6,
            "max_lines": 2,
            "position": "bottom",
            "hold_seconds": 8,
            "fade_seconds": 1.5,
        },
    },
}

_lock = threading.RLock()


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config() -> dict[str, Any]:
    with _lock:
        if not CONFIG_PATH.exists():
            save_config(DEFAULT_CONFIG)
            return json.loads(json.dumps(DEFAULT_CONFIG))
        try:
            on_disk = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            on_disk = {}
        # _deep_merge only copies keys present in `on_disk`; anything missing
        # (e.g. every key, if the file was corrupt) would otherwise fall
        # through as a *reference* to DEFAULT_CONFIG's own nested dicts, so
        # a later in-place mutation of the returned config (settings
        # updates) would silently corrupt the shared module-level default
        # for the rest of the process. Deep-copy first so every call gets
        # its own independent tree regardless of how much of it gets merged.
        default_copy = json.loads(json.dumps(DEFAULT_CONFIG))
        merged = _deep_merge(default_copy, on_disk)

        # A persisted model_dir from a previous version pointing at a model
        # this build no longer knows the filenames for is stale, not a user
        # preference -- upgrading the shipped model must not require every
        # existing install to somehow migrate its own config by hand.
        if Path(merged.get("model_dir", "")).name != MODEL_NAME:
            merged["model_dir"] = MODEL_DIR_DEFAULT
            save_config(merged)

        return merged


def save_config(config: dict[str, Any]) -> None:
    with _lock:
        CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
