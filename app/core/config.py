"""Persisted operator settings (config.json). No audio or transcript content lives here."""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

from app.core.dsp import EQ_BANDS_HZ
from app.core.model_download import MODEL_NAME, model_is_ready

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
    # Set when the background model-quality check (see model_bench.py)
    # auto-promotes a candidate model, so "Revert to previous model" in the
    # control panel has something to swap back to. Empty means either
    # nothing's ever been auto-promoted, or a revert already used this up.
    "previous_model_dir": "",
    # Full name (e.g. "sherpa-onnx-streaming-zipformer-en-2024-01-01") of
    # the last candidate model the background check downloaded and tested,
    # whatever the outcome -- keeps a rejected candidate from being
    # re-downloaded and re-tested on every single future launch.
    "model_update_checked": "",
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
            "show_disclaimer": False,
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
            "show_disclaimer": False,
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

        # A persisted model_dir pointing at a model that doesn't actually
        # exist on disk (e.g. left over from an older app version whose
        # default model has since changed) is stale, not a user preference
        # -- upgrading the shipped model must not require every existing
        # install to somehow migrate its own config by hand. This checks
        # the directory actually has a working model in it rather than
        # comparing its name against today's default, because a *valid*
        # model_dir can legitimately differ from the default now: the
        # background model-quality check (model_bench.py) can auto-promote
        # a different, better-tested model, and that choice must survive
        # the next load_config() call, not get silently reverted by it.
        if not model_is_ready(Path(merged.get("model_dir", ""))):
            merged["model_dir"] = MODEL_DIR_DEFAULT
            merged["previous_model_dir"] = ""
            save_config(merged)

        return merged


def save_config(config: dict[str, Any]) -> None:
    with _lock:
        CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
