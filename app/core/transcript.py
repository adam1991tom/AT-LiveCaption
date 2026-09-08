"""Writes ONLY finalised caption lines to a local text file. No audio, no interim text."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path


class TranscriptWriter:
    def __init__(self, transcript_dir: str, enabled: bool) -> None:
        self.enabled = enabled
        self.dir = Path(transcript_dir)
        self._path: Path | None = None

    def _ensure_session_file(self) -> Path:
        if self._path is None:
            self.dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            self._path = self.dir / f"{stamp}.txt"
        return self._path

    def write_final(self, text: str) -> None:
        if not self.enabled or not text.strip():
            return
        path = self._ensure_session_file()
        timestamp = datetime.now().strftime("%H:%M:%S")
        with path.open("a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {text}\n")
