"""Per-caption speaker-gender detection via fundamental-frequency analysis.

Strategy
--------
1. Extract the entire video's audio to a 16 kHz mono WAV in one ffmpeg call.
2. For each caption, slice the corresponding samples from the array (no extra
   subprocess per caption).
3. Run a simple autocorrelation-based F0 estimator on up to 1 s of speech.
4. F0 < 165 Hz  →  Male  (km-KH-PisethNeural)
   F0 ≥ 165 Hz  →  Female (km-KH-SreymomNeural)
   Silent / unclear → keep the caption's current voice (default female)

Public API
----------
- ``detect_caption_voices(video_path, captions, ...)``  — synchronous, usable
  from any thread (e.g. BatchWorker).
- ``GenderDetectWorker``  — Qt QObject wrapper that emits signals for the UI.
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import wave
from typing import Callable, List, Optional

import numpy as np
from PySide6.QtCore import QObject, Signal, Slot

from app.models.caption import Caption

logger = logging.getLogger(__name__)

MALE_VOICE   = "km-KH-PisethNeural"
FEMALE_VOICE = "km-KH-SreymomNeural"

# F0 boundary: voices below this are classified as male
_PITCH_THRESHOLD_HZ = 165

# Maximum audio window analysed per caption (seconds)
_MAX_WINDOW_S = 3.0


# ------------------------------------------------------------------ public API

def detect_caption_voices(
    video_path: str,
    captions: List[Caption],
    cancelled_fn: Optional[Callable[[], bool]] = None,
    progress_fn: Optional[Callable[[int], None]] = None,
) -> None:
    """Synchronously detect and assign voice for every caption (modifies in-place).

    Parameters
    ----------
    video_path:   path to the source video (audio track is extracted from it)
    captions:     list of Caption objects whose ``voice`` field will be set
    cancelled_fn: optional callable — return True to abort early
    progress_fn:  optional callable(pct: int) — receives 0-100 progress
    """
    samples, sr = _extract_full_audio(video_path)
    total = len(captions)
    for i, cap in enumerate(captions):
        if cancelled_fn and cancelled_fn():
            break
        try:
            cap.voice = _classify_caption(cap, samples, sr)
        except Exception as exc:
            logger.warning("Caption %d gender detect failed: %s", cap.index, exc)
        if progress_fn:
            progress_fn(int((i + 1) / total * 100))


# ------------------------------------------------------------------ Qt worker

class GenderDetectWorker(QObject):
    """Qt wrapper around ``detect_caption_voices`` that emits signals for the UI."""

    progress       = Signal(int)       # 0–100
    voice_detected = Signal(int, str)  # caption_index, voice_id
    finished       = Signal()
    error          = Signal(str)

    def __init__(
        self,
        video_path: str,
        captions: List[Caption],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._video_path = video_path
        self._captions   = captions
        self._cancelled  = False

    def cancel(self) -> None:
        self._cancelled = True

    @Slot()
    def run(self) -> None:
        try:
            samples, sr = _extract_full_audio(self._video_path)
            total = len(self._captions)
            for i, cap in enumerate(self._captions):
                if self._cancelled:
                    break
                try:
                    voice = _classify_caption(cap, samples, sr)
                    self.voice_detected.emit(cap.index, voice)
                except Exception as exc:
                    logger.warning("Caption %d: gender detect failed — %s", cap.index, exc)
                self.progress.emit(int((i + 1) / total * 100))
        except Exception as exc:
            logger.exception("GenderDetectWorker fatal error")
            self.error.emit(str(exc))
        finally:
            self.finished.emit()


# ------------------------------------------------------------------ helpers

def _extract_full_audio(video_path: str) -> tuple[np.ndarray, int]:
    """Extract entire video audio as 16 kHz mono WAV, return (samples, sr)."""
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp_path = tmp.name
    tmp.close()
    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", video_path,
                "-ar", "16000",
                "-ac", "1",
                "-vn",
                tmp_path,
            ],
            capture_output=True,
            check=True,
        )
        return _load_wav(tmp_path)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _classify_caption(cap: Caption, samples: np.ndarray, sr: int) -> str:
    start   = int(cap.start * sr)
    end     = int(min(cap.end, cap.start + _MAX_WINDOW_S) * sr)
    segment = samples[start:end]
    pitch   = _estimate_pitch(segment, sr)
    if pitch is None:
        return cap.voice or FEMALE_VOICE
    return MALE_VOICE if pitch < _PITCH_THRESHOLD_HZ else FEMALE_VOICE


def _load_wav(path: str) -> tuple[np.ndarray, int]:
    with wave.open(path, "rb") as wf:
        sr  = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return samples, sr


def _estimate_pitch(samples: np.ndarray, sr: int) -> float | None:
    """Return the dominant F0 in Hz via autocorrelation, or None if unclear."""
    seg = samples[: sr]                  # use at most 1 second
    if len(seg) < int(sr * 0.05):        # less than 50 ms → too short
        return None
    seg = seg - seg.mean()               # remove DC offset
    rms = float(np.sqrt(np.mean(seg ** 2)))
    if rms < 0.005:                      # silent / near-silent
        return None

    min_lag = int(sr / 400)              # 400 Hz upper bound
    max_lag = int(sr / 70)              # 70 Hz lower bound
    if max_lag >= len(seg):
        return None

    corr = np.correlate(seg, seg, mode="full")
    corr = corr[len(corr) // 2:]        # take positive-lag half
    if corr[0] == 0:
        return None
    corr /= corr[0]                      # normalise to [0, 1]

    window = corr[min_lag: max_lag]
    if len(window) == 0:
        return None

    peak_lag = int(np.argmax(window)) + min_lag
    return float(sr) / peak_lag
