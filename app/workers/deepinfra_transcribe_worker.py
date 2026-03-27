"""QThread worker: transcribe a video using the DeepInfra Whisper API.

Uses segment-level timestamps from the API to create captions.
API key is read from the project .env file (DEEPINFRA_API_KEY=...).
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import traceback
from typing import List

from PySide6.QtCore import QObject, Signal

from app.models.caption import Caption

logger = logging.getLogger(__name__)

_API_URL = "https://api.deepinfra.com/v1/openai/audio/transcriptions"
_MODEL   = "openai/whisper-large-v3"


def _load_api_key() -> str:
    """Read DEEPINFRA_API_KEY from .env file next to the project root."""
    env_path = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    )
    try:
        with open(env_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("DEEPINFRA_API_KEY="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return os.environ.get("DEEPINFRA_API_KEY", "")


def _extract_audio_mp3(video_path: str, out_mp3: str) -> None:
    """Extract audio track to a mono 16 kHz MP3 (minimizes upload size)."""
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-b:a", "64k",
        out_mp3,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg audio extraction failed:\n{result.stderr[-1000:]}")


class DeepInfraTranscribeWorker(QObject):
    """Run DeepInfra Whisper transcription in a background thread.

    Signals
    -------
    progress(int):          0–100 percent.
    captions_ready(list):   emitted when done.
    error(str):             emitted on exception.
    finished():             always emitted at the end.
    """

    progress       = Signal(int)
    captions_ready = Signal(list)
    error          = Signal(str)
    finished       = Signal()

    def __init__(self, video_path: str, language: str = "zh"):
        super().__init__()
        self._video_path = video_path
        self._language   = language
        self._cancelled  = False

    def cancel(self) -> None:
        logger.info("DeepInfraTranscribeWorker cancel requested")
        self._cancelled = True

    # ------------------------------------------------------------------ slot
    def run(self) -> None:
        logger.info(
            "DeepInfraTranscribeWorker starting — video=%s  lang=%s",
            self._video_path, self._language,
        )
        tmp_mp3 = None
        try:
            import requests  # noqa: PLC0415

            api_key = _load_api_key()
            if not api_key:
                raise RuntimeError(
                    "DeepInfra API key not found.\n"
                    "Add  DEEPINFRA_API_KEY=<your-key>  to the .env file in the project root."
                )

            # Step 1: extract audio
            self.progress.emit(5)
            logger.info("Extracting audio to temp MP3…")
            tmp_fd, tmp_mp3 = tempfile.mkstemp(suffix=".mp3", prefix="di_audio_")
            os.close(tmp_fd)
            _extract_audio_mp3(self._video_path, tmp_mp3)
            logger.info("Audio extracted: %s", tmp_mp3)
            self.progress.emit(25)

            if self._cancelled:
                return

            # Step 2: call API with segment-level timestamps
            logger.info("Calling DeepInfra Whisper API…")
            with open(tmp_mp3, "rb") as audio_file:
                response = requests.post(
                    _API_URL,
                    headers={"Authorization": f"Bearer {api_key}"},
                    data={
                        "model":           _MODEL,
                        "language":        self._language,
                        "response_format": "verbose_json",
                    },
                    files={"file": ("audio.mp3", audio_file, "audio/mpeg")},
                    timeout=300,
                )

            if response.status_code != 200:
                raise RuntimeError(
                    f"DeepInfra API error {response.status_code}:\n{response.text[:500]}"
                )

            self.progress.emit(80)

            if self._cancelled:
                return

            data  = response.json()
            raw_segs = data.get("segments") or []

            logger.info("Processing %d segments…", len(raw_segs))
            segments_out = [
                (float(s["start"]), float(s["end"]), s.get("text", "").strip())
                for s in raw_segs
            ]

            logger.info("Produced %d caption segments", len(segments_out))
            captions: List[Caption] = []
            for i, (start, end, text) in enumerate(segments_out, start=1):
                if not text:
                    continue
                captions.append(Caption(index=i, start=start, end=end, original_text=text))

            self.progress.emit(100)
            logger.info("DeepInfraTranscribeWorker done — %d captions", len(captions))
            self.captions_ready.emit(captions)

        except Exception as exc:
            logger.error("DeepInfraTranscribeWorker failed: %s", exc)
            logger.debug(traceback.format_exc())
            self.error.emit(str(exc))
        finally:
            if tmp_mp3 and os.path.isfile(tmp_mp3):
                try:
                    os.remove(tmp_mp3)
                except OSError:
                    pass
            self.finished.emit()
