"""Fetches the fixed English streaming Zipformer model once. Runs offline afterwards."""
from __future__ import annotations

import bz2
import shutil
import tarfile
import urllib.request
from pathlib import Path

MODEL_NAME = "sherpa-onnx-streaming-zipformer-en-2023-06-21"


def model_url_for(model_name: str) -> str:
    return f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{model_name}.tar.bz2"


MODEL_URL = model_url_for(MODEL_NAME)


def _find_component(model_dir: Path, role: str) -> Path | None:
    # Different k2-fsa snapshots name their encoder/decoder/joiner files
    # differently -- e.g. this build's own bundled model ships
    # "encoder-epoch-99-avg-1.int8.onnx", but another snapshot might ship
    # "encoder-epoch-99-avg-1-chunk-16-left-128.int8.onnx" instead (a
    # streaming chunk-size/left-context suffix baked into the filename).
    # Matching by role via glob instead of one fixed exact name is what
    # lets both the live engine and the background model-quality check
    # (model_bench.py) work across snapshots without hardcoding any one
    # naming scheme. Prefers a quantized *.int8.onnx file when a snapshot
    # ships one (smaller, faster, and what this app has always shipped
    # with for its own encoder/joiner), falling back to plain *.onnx.
    for pattern in (f"{role}*.int8.onnx", f"{role}*.onnx"):
        matches = sorted(model_dir.glob(pattern))
        if matches:
            return matches[0]
    return None


def resolve_model_files(model_dir: Path) -> dict[str, Path] | None:
    """The four files an OnlineRecognizer actually needs, found by role
    rather than by one fixed exact filename (see _find_component). None if
    tokens.txt or any of the three model components can't be found at all
    -- callers should treat that the same as "this model isn't ready"."""
    tokens = model_dir / "tokens.txt"
    if not tokens.is_file():
        return None
    encoder = _find_component(model_dir, "encoder")
    decoder = _find_component(model_dir, "decoder")
    joiner = _find_component(model_dir, "joiner")
    if not (encoder and decoder and joiner):
        return None
    return {"tokens": tokens, "encoder": encoder, "decoder": decoder, "joiner": joiner}


def model_is_ready(model_dir: Path) -> bool:
    return resolve_model_files(model_dir) is not None


def download_and_extract(model_dir: Path, model_name: str = MODEL_NAME, progress_cb=None) -> None:
    """Downloads the model archive straight into model_dir's parent and extracts it.

    model_name defaults to this build's own bundled model, but a caller can
    pass a different k2-fsa snapshot name here too (see model_bench.py's
    background candidate-model evaluation) -- model_dir just needs to be
    named to match whatever model_name is, so extraction lands in the right
    place.

    progress_cb(stage: str, pct: float | None) is called with human-readable
    progress so the control UI can show something other than a frozen spinner.
    """
    if model_is_ready(model_dir):
        return

    model_dir.parent.mkdir(parents=True, exist_ok=True)
    archive_path = model_dir.parent / f"{model_name}.tar.bz2"

    def _report(stage, pct=None):
        if progress_cb:
            progress_cb(stage, pct)

    _report("downloading", 0.0)

    def _hook(block_num, block_size, total_size):
        if total_size > 0:
            pct = min(100.0, block_num * block_size / total_size * 100.0)
            _report("downloading", pct)

    urllib.request.urlretrieve(model_url_for(model_name), archive_path, reporthook=_hook)

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
