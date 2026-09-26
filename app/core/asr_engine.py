"""Continuous streaming ASR: mic -> Sherpa-ONNX streaming Zipformer -> partial/final events.

Runs its own thread because sherpa-onnx's decode calls and sounddevice's blocking
read are both synchronous. Nothing here ever writes raw audio to disk (GDPR: only
finalised caption text may reach TranscriptWriter).
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np
import sherpa_onnx
import sounddevice as sd

from app.core import audio_devices, dsp
from app.core.hub import ConnectionHub
from app.core.model_download import resolve_model_files
from app.core.transcript import TranscriptWriter

RETRY_SECONDS = 3.0
CHUNK_SECONDS = 0.06
SILENCE_FLOOR_DB = -60.0


def _dbfs(samples: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(np.square(samples)))) if samples.size else 0.0
    if rms <= 1e-8:
        return SILENCE_FLOOR_DB
    return max(SILENCE_FLOOR_DB, 20.0 * math.log10(rms))


# The model has no whole-word piece for most acronyms, so a spoken "UK" or
# "NHS" comes back as separate single-letter words ("U K", "N H S") rather
# than one token. Collapse runs of 2+ consecutive single-letter words into
# one acronym so captions read the way they're actually meant.
_ACRONYM_RE = re.compile(r"\b([A-Za-z])(?:\s+([A-Za-z]))+\b")


def _join_spelled_acronyms(text: str) -> str:
    return _ACRONYM_RE.sub(lambda m: m.group(0).replace(" ", "").upper(), text)


# Background music/noise has no real words for the model to latch onto, so
# it tends to guess a filler sound instead of staying silent -- heard live
# as caption spam like "um", finalized over and over (each short gap in the
# music re-triggers the endpoint detector, so it's usually many separate
# single-word "um" finals in a row, not one multi-word utterance). This
# isn't a real acoustic music detector (that would need a trained
# classifier and a labelled dataset to tune reliably, which isn't
# something to guess at) -- it's a narrow, honest fix for that exact
# symptom: any utterance made up of nothing but filler words -- one word
# or several -- is relabelled rather than shown as noise. The tradeoff:
# someone's genuine one-word "um" while thinking will also show as
# [MUSIC] rather than "um" -- a small cost, since captioning "um" on its
# own was never informative anyway.
_FILLER_WORDS = {"um", "uh", "umm", "uhh", "erm", "hmm", "mm", "mmm", "huh"}


def _is_filler_spam(text: str) -> bool:
    words = re.findall(r"[a-zA-Z']+", text.lower())
    return bool(words) and all(w in _FILLER_WORDS for w in words)


def _merge_acronym_words(words: list[dict]) -> list[dict]:
    merged: list[dict] = []
    i, n = 0, len(words)
    while i < n:
        j = i
        while j < n and len(words[j]["text"]) == 1 and words[j]["text"].isalpha():
            j += 1
        if j - i >= 2:
            run = words[i:j]
            merged.append(
                {
                    "text": "".join(w["text"] for w in run).upper(),
                    "confidence": round(sum(w["confidence"] for w in run) / len(run), 3),
                }
            )
            i = j
        else:
            merged.append(words[i])
            i += 1
    return merged


def _words_from_tokens(tokens: list[str], ys_probs: list[float]) -> list[dict]:
    """Sherpa-onnx emits BPE pieces, not words -- a piece starting with a
    space marks the start of a new word (mid-word pieces don't). Confidence
    per word is exp(mean log-prob) over its pieces, for the operator-only
    live-preview highlighting in the control panel (see control.html);
    audience-facing pages never see this."""
    words: list[dict] = []
    cur_text = ""
    cur_probs: list[float] = []
    for tok, prob in zip(tokens, ys_probs):
        if tok.startswith(" "):
            if cur_text:
                words.append({"text": cur_text, "confidence": round(math.exp(sum(cur_probs) / len(cur_probs)), 3)})
            cur_text = tok.strip()
            cur_probs = [prob]
        else:
            cur_text += tok
            cur_probs.append(prob)
    if cur_text:
        words.append({"text": cur_text, "confidence": round(math.exp(sum(cur_probs) / len(cur_probs)), 3)})
    return words


class CaptionEngine:
    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        hub: ConnectionHub,
        transcript_writer: TranscriptWriter,
    ) -> None:
        self.loop = loop
        self.hub = hub
        self.transcript_writer = transcript_writer

        self.recognizer: Optional[sherpa_onnx.OnlineRecognizer] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        # Guards start/stop/load_model/refresh_devices against each other --
        # each is dispatched from a FastAPI request handler via
        # run_in_executor, so two requests arriving close together (e.g. a
        # device change while a vocabulary save is mid-reload) land on
        # different threads and would otherwise race on self._thread /
        # self.recognizer. Reentrant because load_model() and
        # refresh_devices() call self.stop()/self.start() themselves.
        self._lifecycle_lock = threading.RLock()
        self.device_index: Optional[int] = None
        self.device_name: Optional[str] = None
        self.model_ready = False

        self.eq: Optional[dsp.GraphicEQ] = None
        self._gain_db = 0.0
        self._band_gains_db = [0.0] * len(dsp.EQ_BANDS_HZ)

    # ---- live audio processing --------------------------------------------
    def set_gain_db(self, gain_db: float) -> None:
        gain_db = max(-dsp.MAX_GAIN_DB, min(dsp.MAX_GAIN_DB, gain_db))
        self._gain_db = gain_db
        if self.eq:
            self.eq.set_gain_db(gain_db)

    def set_band_gain_db(self, index: int, gain_db: float) -> None:
        if not 0 <= index < len(self._band_gains_db):
            return
        gain_db = max(-dsp.MAX_BAND_GAIN_DB, min(dsp.MAX_BAND_GAIN_DB, gain_db))
        self._band_gains_db[index] = gain_db
        if self.eq:
            self.eq.set_band_gain_db(index, gain_db)

    def audio_settings(self) -> dict:
        return {
            "gain_db": self._gain_db,
            "eq_bands_hz": dsp.EQ_BANDS_HZ,
            "eq_band_gains_db": self.eq.band_gains_db() if self.eq else list(self._band_gains_db),
            "max_gain_db": dsp.MAX_GAIN_DB,
            "max_band_gain_db": dsp.MAX_BAND_GAIN_DB,
            "rta_num_bands": dsp.RTA_NUM_BANDS,
            "rta_freq_min": dsp.RTA_FREQ_MIN,
            "rta_freq_max": dsp.RTA_FREQ_MAX,
            "rta_floor_db": dsp.RTA_FLOOR_DB,
        }

    # ---- model -------------------------------------------------------
    def load_model(
        self, model_dir: str, hotwords_file: str = "", hotwords_score: float = 1.5
    ) -> None:
        with self._lifecycle_lock:
            model_path = Path(model_dir)
            # Different k2-fsa snapshots name their encoder/decoder/joiner
            # files differently (see resolve_model_files) -- this build's
            # active model isn't necessarily the one it shipped with
            # anymore, since the background model-quality check can
            # auto-promote a different, better-tested snapshot.
            files = resolve_model_files(model_path)
            if files is None:
                raise RuntimeError(f"Model files not found or incomplete in {model_path}")
            was_running = self.is_running()
            device_index, device_name = self.device_index, self.device_name
            if was_running:
                self.stop()
            self.recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
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
                # hotwords_file is pre-tokenized by app/core/hotwords.py against
                # this model's own tokens.txt -- no modeling_unit/bpe_vocab
                # needed (and passing modeling_unit="bpe" without a real
                # sentencepiece model segfaults the native decoder).
                hotwords_file=hotwords_file,
                hotwords_score=hotwords_score,
                provider="cpu",
            )
            self.model_ready = True
            if was_running:
                self.start(device_index, device_name or "")

    # ---- lifecycle -----------------------------------------------------
    def start(self, device_index: int, device_name: str) -> None:
        with self._lifecycle_lock:
            self.stop()
            self.device_index = device_index
            self.device_name = device_name
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run, args=(device_index, device_name), daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lifecycle_lock:
            self._stop_event.set()
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=5)
            self._thread = None

    def refresh_devices(self) -> None:
        """Forces PortAudio to notice devices plugged/unplugged since it
        started. Safe to call any time: if a stream is currently open it is
        stopped first (rescanning while a stream is open is undefined
        behaviour per PortAudio), then restarted on the same device if it's
        still present."""
        with self._lifecycle_lock:
            was_running = self.device_index is not None and self.is_running()
            device_index, device_name = self.device_index, self.device_name
            if was_running:
                self.stop()
            audio_devices.rescan_devices()
            if was_running:
                self.start(device_index, device_name)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _emit(self, message: dict) -> None:
        asyncio.run_coroutine_threadsafe(self.hub.broadcast(message), self.loop)

    def _emit_status(self, connected: bool, device_name: str, level_db: float, note: str = "") -> None:
        self._emit(
            {
                "type": "status",
                "audio": {
                    "connected": connected,
                    "device": device_name,
                    "level_db": level_db,
                    "note": note,
                },
                "engine": {"ready": self.model_ready, "state": "live" if connected else "disconnected"},
            }
        )

    # ---- worker thread ---------------------------------------------------
    def _run(self, device_index: int, device_name: str) -> None:
        while not self._stop_event.is_set():
            try:
                info = sd.query_devices(device_index)
                if info.get("name") != device_name:
                    raise RuntimeError(f"device index {device_index} no longer matches {device_name!r}")
                samplerate = int(info.get("default_samplerate", 16000)) or 16000
                samples_per_read = int(CHUNK_SECONDS * samplerate)

                stream = self.recognizer.create_stream()
                self.eq = dsp.GraphicEQ(samplerate, band_gains_db=self._band_gains_db, gain_db=self._gain_db)

                with sd.InputStream(
                    device=device_index,
                    channels=1,
                    dtype="float32",
                    samplerate=samplerate,
                ) as input_stream:
                    self._emit_status(True, device_name, SILENCE_FLOOR_DB, "connected")
                    last_status_at = 0.0
                    last_rta_at = 0.0
                    last_partial = ""

                    while not self._stop_event.is_set():
                        samples, _ = input_stream.read(samples_per_read)
                        samples = self.eq.process(samples.reshape(-1))

                        level_db = _dbfs(samples)
                        now = time.monotonic()
                        if now - last_status_at > 0.2:
                            self._emit_status(True, device_name, level_db, "connected")
                            last_status_at = now
                        if now - last_rta_at > 0.1:
                            self._emit({"type": "rta", "bands_db": dsp.compute_rta_bands(samples, samplerate)})
                            last_rta_at = now

                        stream.accept_waveform(samplerate, samples)
                        while self.recognizer.is_ready(stream):
                            self.recognizer.decode_stream(stream)

                        is_endpoint = self.recognizer.is_endpoint(stream)
                        result = json.loads(self.recognizer.get_result_as_json_string(stream))
                        text = _join_spelled_acronyms(result["text"].strip())
                        words = _merge_acronym_words(_words_from_tokens(result["tokens"], result["ys_probs"])) if text else []

                        if is_endpoint:
                            if text:
                                if _is_filler_spam(text):
                                    text, words = "[MUSIC]", []
                                self._emit({"type": "final", "text": text, "words": words})
                                self.transcript_writer.write_final(text)
                            self.recognizer.reset(stream)
                            last_partial = ""
                        elif text and text != last_partial:
                            self._emit({"type": "partial", "text": text, "words": words})
                            last_partial = text

            except Exception as exc:  # device unplugged, driver error, etc.
                if self._stop_event.is_set():
                    break
                self._emit_status(False, device_name, SILENCE_FLOOR_DB, f"{exc}")
                time.sleep(RETRY_SECONDS)

                # PortAudio caches its device table at init and won't notice a
                # USB mic/interface coming back on its own -- force a rescan,
                # then re-resolve by name since a reconnect can land at a
                # different index if other devices changed in the meantime.
                try:
                    audio_devices.rescan_devices()
                    for d in audio_devices.list_input_devices():
                        if d["name"] == device_name:
                            device_index = d["index"]
                            self.device_index = device_index
                            break
                except Exception:
                    pass
