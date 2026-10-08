"""
FILE: tourgen/media/tts.py
ROLE: Pluggable text-to-speech interface for optional voiceover (off by default).

HOW IT WORKS:
  - TTSProvider.synthesize(text, language, out) writes an audio file and returns it.
  - NullTTS is the default and produces nothing; stages/assemble.py skips voiceover
    when the provider returns None.

CONNECTS TO:
  - stages/assemble.py (narration lines from TourPlan shots)

INVARIANTS / GOTCHAS:
  - Add a real provider as a new class here + a selection flag; keep it behind
    this interface so tests stay offline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class TTSProvider(Protocol):
    def synthesize(self, text: str, language: str, out: Path) -> Path | None: ...


class NullTTS:
    def synthesize(self, text: str, language: str, out: Path) -> Path | None:
        return None
