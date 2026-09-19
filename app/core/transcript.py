"""Writes ONLY finalised caption lines to a local text file. No audio, no interim text."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path


class TranscriptWriter:
    def __init__(self, transcript_dir: str, enabled: bool) -> None:
        self.enabled = False
        self.dir = Path(transcript_dir)
        self._path: Path | None = None
        if enabled:
            self.start_new()

    def start_new(self) -> Path:
        """Always begins a fresh file -- unlike the old toggle, clicking
        Start again after a Stop does not resume appending to the previous
        one. Returns the new file's path so callers (main.py) can report it
        back to the control panel immediately."""
        self.dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self._path = self.dir / f"{stamp}.txt"
        self.enabled = True
        return self._path

    def stop(self) -> None:
        self.enabled = False

    @property
    def current_filename(self) -> str | None:
        return self._path.name if (self.enabled and self._path) else None

    def set_enabled(self, enabled: bool) -> None:
        """Back-compat shim for the old on/off checkbox semantics."""
        if enabled:
            self.start_new()
        else:
            self.stop()

    def write_final(self, text: str) -> None:
        if not self.enabled or self._path is None or not text.strip():
            return
        timestamp = datetime.now().strftime("%H:%M:%S")
        with self._path.open("a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {text}\n")


# ---- SRT/VTT export ---------------------------------------------------------
# Saved transcripts only carry wall-clock [HH:MM:SS] timestamps (see
# write_final above), not the precise per-caption start/end the live ASR
# briefly has -- exporting from the saved file instead of the live stream
# means one saved .txt is enough to re-export at any time, without having
# to keep a session running. Each caption's end time is the next caption's
# start; the last one gets an estimated reading duration.

def _parse_lines(text: str) -> list[tuple[str, str]]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("["):
            continue
        end = line.find("]")
        if end == -1:
            continue
        caption = line[end + 1 :].strip()
        if caption:
            out.append((line[1:end], caption))
    return out


def _estimate_duration(text: str) -> float:
    words = max(1, len(text.split()))
    return max(1.5, min(6.0, words / 2.5))  # ~150 wpm reading speed, clamped


def _srt_timestamp(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    hours, rem_ms = divmod(total_ms, 3_600_000)
    minutes, rem_ms = divmod(rem_ms, 60_000)
    secs, ms = divmod(rem_ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _vtt_timestamp(seconds: float) -> str:
    return _srt_timestamp(seconds).replace(",", ".")


def _build_blocks(text: str, ts_fn) -> list[str]:
    lines = _parse_lines(text)
    if not lines:
        return []
    times = [datetime.strptime(ts, "%H:%M:%S") for ts, _ in lines]
    t0 = times[0]
    starts = [(t - t0).total_seconds() for t in times]
    for i in range(1, len(starts)):
        if starts[i] < starts[i - 1]:
            starts[i] += 86400  # session crossed midnight
    blocks = []
    for i, (start, (_, caption)) in enumerate(zip(starts, lines)):
        end = starts[i + 1] if i + 1 < len(starts) else start + _estimate_duration(caption)
        end = max(end, start + 0.5)
        blocks.append(f"{i + 1}\n{ts_fn(start)} --> {ts_fn(end)}\n{caption}\n")
    return blocks


def to_srt(text: str) -> str:
    return "\n".join(_build_blocks(text, _srt_timestamp))


def to_vtt(text: str) -> str:
    blocks = _build_blocks(text, _vtt_timestamp)
    return "WEBVTT\n\n" + "\n".join(blocks) if blocks else "WEBVTT\n"
