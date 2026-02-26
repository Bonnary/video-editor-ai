"""QThread worker: transcribe a video file using openai-whisper."""
from __future__ import annotations

import os
import sys
from typing import List

from PySide6.QtCore import QObject, QThread, Signal

from app.models.caption import Caption

# Add libs/whisper to path so we can reuse load_model
_WHISPER_LIB = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "libs", "whisper")
)
if _WHISPER_LIB not in sys.path:
    sys.path.insert(0, _WHISPER_LIB)


class TranscribeWorker(QObject):
    """Run Whisper transcription in a background thread.

    Signals
    -------
    progress(int):          0–100 percent estimate (segment-based).
    captions_ready(list):   emitted when transcription is complete.
    error(str):             emitted on exception.
    finished():             always emitted at the end.
    """

    progress       = Signal(int)
    captions_ready = Signal(list)
    error          = Signal(str)
    finished       = Signal()

    def __init__(self, video_path: str, model_name: str = "auto"):
        super().__init__()
        self._video_path  = video_path
        self._model_name  = model_name

    # ------------------------------------------------------------------ slot
    def run(self) -> None:
        try:
            from main import load_model  # from libs/whisper/main.py

            self.progress.emit(5)
            model, device = load_model(self._model_name)
            self.progress.emit(20)

            result = model.transcribe(
                self._video_path,
                language="zh",          # Chinese (Mandarin)
                verbose=False,
                word_timestamps=False,
                fp16=(device == "cuda"),
            )

            segments = result.get("segments", [])
            total    = len(segments) or 1
            captions: List[Caption] = []

            for i, seg in enumerate(segments, start=1):
                captions.append(
                    Caption(
                        index=i,
                        start=float(seg["start"]),
                        end=float(seg["end"]),
                        original_text=seg["text"].strip(),
                    )
                )
                self.progress.emit(20 + int(i / total * 75))

            self.progress.emit(100)
            self.captions_ready.emit(captions)

        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            self.finished.emit()
