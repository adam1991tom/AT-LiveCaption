"""Fetches the fixed English streaming Zipformer model once. Runs offline afterwards."""
from __future__ import annotations

import bz2
import shutil
import tarfile
import urllib.request
from pathlib import Path

MODEL_NAME = "sherpa-onnx-streaming-zipformer-en-2023-06-21"
MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    f"asr-models/{MODEL_NAME}.tar.bz2"
)
REQUIRED_FILES = [
    "tokens.txt",
    "encoder-epoch-99-avg-1.int8.onnx",
    "decoder-epoch-99-avg-1.onnx",
    "joiner-epoch-99-avg-1.int8.onnx",
]


def model_is_ready(model_dir: Path) -> bool:
    return all((model_dir / name).exists() for name in REQUIRED_FILES)


def download_and_extract(model_dir: Path, progress_cb=None) -> None:
    """Downloads the model archive straight into model_dir's parent and extracts it.

    progress_cb(stage: str, pct: float | None) is called with human-readable
    progress so the control UI can show something other than a frozen spinner.
    """
    if model_is_ready(model_dir):
        return

    model_dir.parent.mkdir(parents=True, exist_ok=True)
    archive_path = model_dir.parent / f"{MODEL_NAME}.tar.bz2"

    def _report(stage, pct=None):
        if progress_cb:
            progress_cb(stage, pct)

    _report("downloading", 0.0)

    def _hook(block_num, block_size, total_size):
        if total_size > 0:
            pct = min(100.0, block_num * block_size / total_size * 100.0)
            _report("downloading", pct)

    urllib.request.urlretrieve(MODEL_URL, archive_path, reporthook=_hook)

    _report("extracting", None)
    with tarfile.open(archive_path, mode="r:bz2") as tar:
        # filter="data" rejects path traversal / absolute paths / device
        # files -- defense in depth in case the release URL is ever
        # redirected or compromised (Python 3.11.4+).
        tar.extractall(path=model_dir.parent, filter="data")
    archive_path.unlink(missing_ok=True)

    if not model_is_ready(model_dir):
        raise RuntimeError(
            f"Model extraction completed but expected files are missing in {model_dir}"
        )

    _report("ready", 100.0)
