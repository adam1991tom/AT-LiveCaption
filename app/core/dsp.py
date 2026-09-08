"""Live mic processing: gain + a graphic EQ, applied per-chunk before ASR.

Uses standard RBJ (Audio EQ Cookbook) peaking-filter biquads. Each band
keeps its own IIR filter state (`zi`) across chunks so the audio stays
continuous instead of clicking at chunk boundaries.
"""
from __future__ import annotations

import math
import threading

import numpy as np
from scipy.signal import lfilter, lfilter_zi

EQ_BANDS_HZ = [31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]  # ISO 10-band graphic EQ
BAND_Q = 1.4
MAX_BAND_GAIN_DB = 12.0
MAX_GAIN_DB = 20.0

RTA_NUM_BANDS = 31
RTA_FREQ_MIN = 20.0
RTA_FREQ_MAX = 20000.0
RTA_FLOOR_DB = -60.0


def _peaking_coeffs(freq_hz: float, gain_db: float, q: float, sample_rate: int):
    a = 10 ** (gain_db / 40.0)
    w0 = 2 * math.pi * freq_hz / sample_rate
    alpha = math.sin(w0) / (2 * q)
    cos_w0 = math.cos(w0)

    b0 = 1 + alpha * a
    b1 = -2 * cos_w0
    b2 = 1 - alpha * a
    a0 = 1 + alpha / a
    a1 = -2 * cos_w0
    a2 = 1 - alpha / a

    b = np.array([b0, b1, b2]) / a0
    a = np.array([1.0, a1 / a0, a2 / a0])
    return b, a


class GraphicEQ:
    """Gain + N-band peaking EQ. Not thread-safe across concurrent callers,
    but safe for the single-producer (set_*) / single-consumer (process)
    pattern used here: the audio thread only reads, the API thread only
    writes, and each write swaps a small tuple/array in one step."""

    def __init__(self, sample_rate: int, band_gains_db: list[float] | None = None, gain_db: float = 0.0) -> None:
        self._lock = threading.Lock()
        self.sample_rate = sample_rate
        self.gain_linear = 10 ** (gain_db / 20.0)
        self._band_gains_db = list(band_gains_db) if band_gains_db else [0.0] * len(EQ_BANDS_HZ)
        self._coeffs = [
            _peaking_coeffs(freq, g, BAND_Q, sample_rate)
            for freq, g in zip(EQ_BANDS_HZ, self._band_gains_db)
        ]
        self._zi = [np.zeros_like(lfilter_zi(b, a)) for b, a in self._coeffs]

    def set_gain_db(self, gain_db: float) -> None:
        gain_db = max(-MAX_GAIN_DB, min(MAX_GAIN_DB, gain_db))
        with self._lock:
            self.gain_linear = 10 ** (gain_db / 20.0)

    def set_band_gain_db(self, index: int, gain_db: float) -> None:
        if not 0 <= index < len(EQ_BANDS_HZ):
            return
        gain_db = max(-MAX_BAND_GAIN_DB, min(MAX_BAND_GAIN_DB, gain_db))
        coeffs = _peaking_coeffs(EQ_BANDS_HZ[index], gain_db, BAND_Q, self.sample_rate)
        with self._lock:
            self._band_gains_db[index] = gain_db
            self._coeffs[index] = coeffs

    def band_gains_db(self) -> list[float]:
        with self._lock:
            return list(self._band_gains_db)

    def process(self, samples: np.ndarray) -> np.ndarray:
        with self._lock:
            gain = self.gain_linear
            coeffs = list(self._coeffs)

        out = samples * gain
        for i, (b, a) in enumerate(coeffs):
            out, self._zi[i] = lfilter(b, a, out, zi=self._zi[i])
        return np.clip(out, -1.0, 1.0).astype(np.float32)


_RTA_BAND_EDGES_CACHE: dict[int, np.ndarray] = {}


def _rta_band_edges(sample_rate: int) -> np.ndarray:
    edges = _RTA_BAND_EDGES_CACHE.get(sample_rate)
    if edges is None:
        f_max = min(RTA_FREQ_MAX, sample_rate / 2.0)
        edges = np.logspace(math.log10(RTA_FREQ_MIN), math.log10(f_max), RTA_NUM_BANDS + 1)
        _RTA_BAND_EDGES_CACHE[sample_rate] = edges
    return edges


def compute_rta_bands(samples: np.ndarray, sample_rate: int) -> list[float]:
    """Log-spaced spectrum for a real-time analyzer display, in the same
    dBFS-ish scale as the level meter (0 dBFS = a full-scale sine)."""
    n = samples.size
    if n < 8:
        return [RTA_FLOOR_DB] * RTA_NUM_BANDS

    window = np.hanning(n)
    window_sum = window.sum() or 1.0
    spectrum = np.abs(np.fft.rfft(samples * window))
    # Scale so a full-scale sine in one bin reads ~0 dBFS: amplitude = 2*|X|/sum(window).
    amplitude = spectrum * (2.0 / window_sum)
    freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate)

    edges = _rta_band_edges(sample_rate)
    bands_db = []
    for i in range(RTA_NUM_BANDS):
        mask = (freqs >= edges[i]) & (freqs < edges[i + 1])
        peak = amplitude[mask].max() if mask.any() else 0.0
        rms_equiv = peak / math.sqrt(2)
        db = 20 * math.log10(rms_equiv) if rms_equiv > 1e-8 else RTA_FLOOR_DB
        bands_db.append(max(RTA_FLOOR_DB, min(0.0, db)))
    return bands_db
