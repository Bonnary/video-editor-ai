"""QThread worker: transcribe a video using the DeepInfra Whisper API.

Requests word-level timestamps and regroups them into short captions
comparable to local Whisper output (≤5 s / ≤12 words per segment).
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

# Regrouping tunables
_MAX_SEG_DURATION = 5.0   # seconds — split segment if longer
_MAX_SEG_WORDS    = 12    # words   — split segment if more words
_GAP_THRESHOLD    = 0.5   # seconds — natural pause → always split here


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


def _group_words(words: list, language: str) -> List[tuple]:
    """Group word-level dicts into (start, end, text) caption tuples.

    Splitting rules (whichever triggers first):
    - Gap between words > _GAP_THRESHOLD  → always split
    - Segment duration  > _MAX_SEG_DURATION
    - Word count        >= _MAX_SEG_WORDS
    """
    segments: List[tuple] = []
    current: list = []
    seg_start: float | None = None

    for w in words:
        w_start = float(w.get("start") or 0)
        w_end   = float(w.get("end")   or w_start + 0.3)
        w_text  = w.get("word", "").strip()
        if not w_text:
            continue

        if seg_start is None:
            seg_start = w_start
            current   = [w]
        else:
            prev_end = float(current[-1].get("end") or current[-1].get("start") or 0)
            gap      = w_start - prev_end
            duration = w_end - seg_start

            split = (
                gap      >= _GAP_THRESHOLD
                or duration > _MAX_SEG_DURATION
                or len(current) >= _MAX_SEG_WORDS
            )
            if split:
                text = _words_to_text(current, language)
                segments.append((seg_start, prev_end, text))
                seg_start = w_start
                current   = [w]
            else:
                current.append(w)

    if current and seg_start is not None:
        prev_end = float(current[-1].get("end") or current[-1].get("start") or 0)
        text = _words_to_text(current, language)
        segments.append((seg_start, prev_end, text))

    return segments


def _words_to_text(words: list, language: str) -> str:
    """Join word tokens.  Chinese/Japanese/Thai skip spaces."""
    no_space_langs = {"zh", "ja", "th", "ko"}
    sep = "" if language in no_space_langs else " "
    return sep.join(w.get("word", "").strip() for w in words).strip()


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

            # Step 2: call API with word-level timestamps
            logger.info("Calling DeepInfra Whisper API…")
            with open(tmp_mp3, "rb") as audio_file:
                response = requests.post(
                    _API_URL,
                    headers={"Authorization": f"Bearer {api_key}"},
                    data={
                        "model":                     _MODEL,
                        "language":                  self._language,
                        "response_format":           "verbose_json",
                        "timestamp_granularities[]": "word",
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
            words = data.get("words") or []

            # Fall back to segments if no word-level data returned
            if not words:
                logger.warning("No word-level timestamps returned; falling back to segments")
                raw_segs = data.get("segments") or []
                segments_out = [
                    (float(s["start"]), float(s["end"]), s.get("text", "").strip())
                    for s in raw_segs
                ]
            else:
                logger.info("Received %d word timestamps — regrouping…", len(words))
                segments_out = _group_words(words, self._language)

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
