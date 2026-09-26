"""Background quality check for a candidate AI model against the model
currently in use.

k2-fsa bundles a handful of fixed reference clips (test_wavs/ + trans.txt)
with every model release -- meant for a quick sanity check after download,
but also useful here as a small, consistent benchmark: feed the SAME audio
(always the current model's own bundled clips, so both sides are compared
on identical input) through both the current and candidate models, score
each against the known-correct transcript with word error rate (WER), and
let the lower score decide. This is a coarse signal -- three short clips is
a tiny sample, and a percentage point or two of difference is noise, not a
real improvement -- so callers should only act on a clear, sizeable gap.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import sherpa_onnx
from scipy.io import wavfile
from scipy.signal import resample

from app.core.model_download import resolve_model_files

_WORD_RE = re.compile(r"[A-Z']+")


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(text.upper())


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Standard edit-distance WER: (substitutions + deletions + insertions)
    divided by the reference word count. 0.0 is a perfect match."""
    ref = _words(reference)
    hyp = _words(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i in range(1, len(ref) + 1):
        cur = [i] + [0] * len(hyp)
        for j in range(1, len(hyp) + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[len(hyp)] / len(ref)


def _load_wav_16k_mono(path: Path) -> np.ndarray:
    rate, data = wavfile.read(path)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if np.issubdtype(data.dtype, np.integer):
        data = data.astype(np.float32) / float(np.iinfo(data.dtype).max)
    else:
        data = data.astype(np.float32)
    if rate != 16000:
        data = resample(data, int(round(len(data) * 16000 / rate))).astype(np.float32)
    return data


def _build_recognizer(model_dir: Path) -> "sherpa_onnx.OnlineRecognizer":
    # Same construction as asr_engine.py's live recognizer (minus hotwords,
    # irrelevant to a fixed reference transcript) -- must match production
    # settings for the comparison to mean anything. resolve_model_files
    # finds each component by role rather than exact filename, since
    # different k2-fsa snapshots (this is exactly the "candidate" case)
    # can name them differently.
    files = resolve_model_files(model_dir)
    if files is None:
        raise RuntimeError(f"Model files not found or incomplete in {model_dir}")
    return sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=str(files["tokens"]),
        encoder=str(files["encoder"]),
        decoder=str(files["decoder"]),
        joiner=str(files["joiner"]),
        num_threads=2,
        sample_rate=16000,
        feature_dim=80,
        enable_endpoint_detection=True,
        rule1_min_trailing_silence=2.4,
        rule2_min_trailing_silence=1.2,
        rule3_min_utterance_length=300,
        decoding_method="modified_beam_search",
        max_active_paths=6,
        provider="cpu",
    )


def _transcribe(recognizer, samples: np.ndarray) -> str:
    stream = recognizer.create_stream()
    stream.accept_waveform(16000, samples)
    stream.input_finished()
    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)
    result = json.loads(recognizer.get_result_as_json_string(stream))
    return result["text"].strip()


def evaluate_model(model_dir: Path, test_wavs_dir: Path, trans_path: Path) -> float | None:
    """Average WER of the model at model_dir over the fixed reference clips
    at test_wavs_dir/trans_path. None if a recognizer can't even be built,
    or none of the reference clips are readable -- callers should treat
    that as "couldn't test", not "tested perfectly"."""
    if not trans_path.is_file():
        return None
    try:
        recognizer = _build_recognizer(model_dir)
    except Exception:
        return None

    scores = []
    for line in trans_path.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2:
            continue
        wav_name, reference = parts
        wav_path = test_wavs_dir / wav_name
        if not wav_path.is_file():
            continue
        try:
            samples = _load_wav_16k_mono(wav_path)
            hypothesis = _transcribe(recognizer, samples)
        except Exception:
            continue
        scores.append(word_error_rate(reference, hypothesis))

    return sum(scores) / len(scores) if scores else None
