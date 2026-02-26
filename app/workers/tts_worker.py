"""QThread worker: generate Khmer TTS audio using Microsoft Edge TTS."""
from __future__ import annotations

import asyncio
import os
import time
from typing import List

from PySide6.QtCore import QObject, Signal

from app.models.caption import Caption

# Edge TTS Khmer voices
KHMER_VOICES = {
    "Female – Sreymom": "km-KH-SreymomNeural",
    "Male   – Piseth":  "km-KH-PisethNeural",
}
DEFAULT_VOICE = "km-KH-SreymomNeural"


class TTSWorker(QObject):
    """Generate a TTS audio file per caption using edge-tts.

    Signals
    -------
    progress(int):                  0–100 percent.
    caption_audio_ready(int, str):  (caption.index, audio_file_path).
    error(str):                     emitted on exception.
    finished():                     always emitted at the end.
    """

    progress            = Signal(int)
    caption_audio_ready = Signal(int, str)   # (caption index, file path)
    error               = Signal(str)
    finished            = Signal()

    def __init__(
        self,
        captions: List[Caption],
        output_dir: str,
        voice: str = DEFAULT_VOICE,
    ):
        super().__init__()
        self._captions   = captions
        self._output_dir = output_dir
        self._voice      = voice

    # ------------------------------------------------------------------ slot
    def run(self) -> None:
        try:
            import edge_tts

            os.makedirs(self._output_dir, exist_ok=True)
            total = len(self._captions) or 1

            for i, cap in enumerate(self._captions):
                if not cap.khmer_text:
                    self.progress.emit(int((i + 1) / total * 100))
                    continue

                out_path = os.path.join(self._output_dir, f"tts_{cap.index:04d}.mp3")

                # Use per-caption voice if set, otherwise fall back to worker default
                voice = cap.voice if cap.voice else self._voice

                # Retry with exponential backoff to handle Edge TTS 503 rate-limiting
                max_retries = 5
                for attempt in range(max_retries):
                    try:
                        communicate = edge_tts.Communicate(
                            text=cap.khmer_text,
                            voice=voice,
                        )
                        asyncio.run(communicate.save(out_path))
                        break  # success
                    except Exception as exc:
                        if attempt == max_retries - 1:
                            raise
                        wait = 2 ** attempt  # 1s, 2s, 4s, 8s …
                        time.sleep(wait)

                self.caption_audio_ready.emit(cap.index, out_path)
                self.progress.emit(int((i + 1) / total * 100))

                # Small delay between requests to avoid triggering rate limits
                if i < len(self._captions) - 1:
                    time.sleep(0.5)

            self.progress.emit(100)

        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            self.finished.emit()
