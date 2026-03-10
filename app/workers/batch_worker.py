"""QThread worker: batch-process a folder of videos through the full pipeline.

Pipeline per video
------------------
  1. Transcribe (Whisper)
  2. Translate each caption → Khmer (googletrans)
  3. Generate TTS audio per caption (edge-tts)
  4. Export dubbed video  →  <output_dir>/<stem>_dubbed.mp4

Failed videos are logged and skipped; processing continues with the next file.
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import sys
import tempfile
import time
import traceback
from typing import List

from PySide6.QtCore import QObject, Signal

from app.models.caption import Caption
from app.utils.ffmpeg_utils import export_video
from app.utils.srt_utils import write_srt
from app.workers.tts_worker import DEFAULT_VOICE

logger = logging.getLogger(__name__)

# Video extensions accepted for batch processing
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv"}

# ---- lazy-load libraries that are not importable by standard name ----

_WHISPER_LIB = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "libs", "whisper")
)
if _WHISPER_LIB not in sys.path:
    sys.path.insert(0, _WHISPER_LIB)

_TRANS_MAIN = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "libs", "googletrans", "main.py")
)
_spec = importlib.util.spec_from_file_location("googletrans_main", _TRANS_MAIN)
_googletrans_main = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_googletrans_main)
translate_text = _googletrans_main.translate_text


def collect_videos(folder: str) -> List[str]:
    """Return sorted list of video file paths inside *folder* (non-recursive)."""
    paths = []
    try:
        for name in sorted(os.listdir(folder)):
            if os.path.splitext(name)[1].lower() in VIDEO_EXTENSIONS:
                paths.append(os.path.join(folder, name))
    except OSError as exc:
        logger.error("Cannot list folder %s: %s", folder, exc)
    return paths


class BatchWorker(QObject):
    """Run the full dubbing pipeline on every video in a folder.

    Signals
    -------
    video_started(int, int, str):       (1-based index, total, video filename)
    video_step(str):                    current step name ("Transcribing…" etc.)
    video_progress(int):                0-100 progress within the current video
    video_done(int, int, str):          (index, total, output_path)
    video_failed(int, int, str, str):   (index, total, video filename, error msg)
    batch_done(int, int):               (succeeded_count, failed_count)
    finished():                         always emitted at the very end
    """

    video_started   = Signal(int, int, str)    # (idx, total, name)
    video_step      = Signal(str)              # step label
    video_progress  = Signal(int)             # 0-100 within current video
    video_done      = Signal(int, int, str)    # (idx, total, out_path)
    video_failed    = Signal(int, int, str, str)  # (idx, total, name, err)
    batch_done      = Signal(int, int)         # (ok, failed)
    finished        = Signal()

    # Translation retry settings (mirrors TranslateWorker)
    _MAX_RETRIES = 3
    _RETRY_DELAY = 2.0

    def __init__(
        self,
        video_paths: List[str],
        output_dir: str,
        model_name: str = "auto",
        language: str = "zh",
        voice: str = DEFAULT_VOICE,
    ):
        super().__init__()
        self._video_paths = video_paths
        self._output_dir  = output_dir
        self._model_name  = model_name
        self._language    = language
        self._voice       = voice
        self._cancelled   = False

    # ------------------------------------------------------------------ public
    def cancel(self) -> None:
        logger.info("BatchWorker: cancel requested")
        self._cancelled = True

    # ------------------------------------------------------------------ slot
    def run(self) -> None:
        total     = len(self._video_paths)
        succeeded = 0
        failed    = 0

        logger.info(
            "BatchWorker starting — %d video(s)  model=%s  lang=%s",
            total, self._model_name, self._language,
        )

        # Load Whisper model once and reuse across all videos
        whisper_model = None
        try:
            self.video_step.emit("Loading Whisper model…")
            from main import load_model  # from libs/whisper/main.py
            whisper_model, device = load_model(self._model_name)
            logger.info("Whisper model loaded on device=%s", device)
        except Exception as exc:
            logger.error("Failed to load Whisper model: %s", exc)
            self.video_failed.emit(0, total, "", f"Failed to load Whisper model: {exc}")
            self.batch_done.emit(0, total)
            self.finished.emit()
            return

        for idx, video_path in enumerate(self._video_paths, start=1):
            if self._cancelled:
                logger.info("BatchWorker: cancelled before video %d", idx)
                break

            video_name = os.path.basename(video_path)
            self.video_started.emit(idx, total, video_name)
            logger.info("BatchWorker [%d/%d] starting: %s", idx, total, video_path)

            try:
                self._process_one(
                    idx, total, video_path, video_name, whisper_model
                )
                succeeded += 1
                logger.info("BatchWorker [%d/%d] done: %s", idx, total, video_name)
            except Exception as exc:
                failed += 1
                err_msg = str(exc)
                logger.error(
                    "BatchWorker [%d/%d] FAILED (%s): %s\n%s",
                    idx, total, video_name, err_msg, traceback.format_exc(),
                )
                self.video_failed.emit(idx, total, video_name, err_msg)

        self.batch_done.emit(succeeded, failed)
        logger.info(
            "BatchWorker finished — succeeded=%d  failed=%d", succeeded, failed
        )
        self.finished.emit()

    # ------------------------------------------------------------------ private
    def _emit_progress(self, overall_pct: int) -> None:
        """Clamp and emit video-level progress."""
        self.video_progress.emit(max(0, min(100, overall_pct)))

    def _process_one(
        self,
        idx: int,
        total: int,
        video_path: str,
        video_name: str,
        whisper_model,
    ) -> None:
        """Run the full pipeline for a single video. Raises on failure."""
        tts_dir = tempfile.mkdtemp(prefix="kh_batch_tts_")
        try:
            # ---- Step 1: Transcribe ----------------------------------------
            if self._cancelled:
                raise InterruptedError("Cancelled")
            self.video_step.emit("Transcribing…")
            self._emit_progress(5)

            result   = whisper_model.transcribe(
                video_path,
                language=self._language,
                verbose=False,
                word_timestamps=False,
                fp16=False,   # safe default; GPU transcription still works
            )
            segments = result.get("segments", [])
            captions: List[Caption] = [
                Caption(
                    index=i,
                    start=float(s["start"]),
                    end=float(s["end"]),
                    original_text=s["text"].strip(),
                )
                for i, s in enumerate(segments, start=1)
            ]
            logger.info(
                "[%d/%d] Transcribed %d segments from %s",
                idx, total, len(captions), video_name,
            )
            self._emit_progress(25)

            # ---- Step 2: Translate ------------------------------------------
            if self._cancelled:
                raise InterruptedError("Cancelled")
            self.video_step.emit("Translating to Khmer…")

            cap_total = len(captions) or 1
            for ci, cap in enumerate(captions):
                if self._cancelled:
                    raise InterruptedError("Cancelled")
                translated = None
                for attempt in range(1, self._MAX_RETRIES + 1):
                    try:
                        result_txt = translate_text(cap.original_text, target_language="km")
                        if result_txt:
                            translated = result_txt
                            break
                    except Exception as exc:
                        logger.warning(
                            "[%d/%d] Translation attempt %d/%d failed for caption %d: %s",
                            idx, total, attempt, self._MAX_RETRIES, cap.index, exc,
                        )
                        if attempt < self._MAX_RETRIES:
                            time.sleep(self._RETRY_DELAY)
                if translated:
                    cap.khmer_text = translated
                else:
                    logger.warning(
                        "[%d/%d] Caption %d skipped (all translation attempts failed)",
                        idx, total, cap.index,
                    )
                # Progress: 25 → 55 across translation
                self._emit_progress(25 + int((ci + 1) / cap_total * 30))

            logger.info("[%d/%d] Translation done for %s", idx, total, video_name)

            # ---- Step 3: Generate TTS --------------------------------------
            if self._cancelled:
                raise InterruptedError("Cancelled")
            self.video_step.emit("Generating TTS audio…")

            import edge_tts

            tts_total = len(captions) or 1
            for ti, cap in enumerate(captions):
                if self._cancelled:
                    raise InterruptedError("Cancelled")
                if not cap.khmer_text:
                    self._emit_progress(55 + int((ti + 1) / tts_total * 20))
                    continue

                out_path = os.path.join(tts_dir, f"tts_{cap.index:04d}.mp3")
                voice    = cap.voice or self._voice
                max_tts_retries = 5
                for attempt in range(max_tts_retries):
                    try:
                        communicate = edge_tts.Communicate(text=cap.khmer_text, voice=voice)
                        asyncio.run(communicate.save(out_path))
                        break
                    except Exception as exc:
                        logger.warning(
                            "[%d/%d] TTS attempt %d/%d failed for caption %d: %s",
                            idx, total, attempt + 1, max_tts_retries, cap.index, exc,
                        )
                        if attempt == max_tts_retries - 1:
                            raise
                        time.sleep(2 ** attempt)

                cap.tts_audio_path = out_path
                # Progress: 55 → 75
                self._emit_progress(55 + int((ti + 1) / tts_total * 20))

                if ti < len(captions) - 1:
                    time.sleep(0.3)

            logger.info("[%d/%d] TTS done for %s", idx, total, video_name)

            # ---- Step 4: Export --------------------------------------------
            if self._cancelled:
                raise InterruptedError("Cancelled")
            self.video_step.emit("Exporting video…")

            stem        = os.path.splitext(video_name)[0]
            out_video   = os.path.join(self._output_dir, f"{stem}_dubbed.mp4")
            srt_path    = os.path.join(self._output_dir, f"{stem}_dubbed.srt")

            os.makedirs(self._output_dir, exist_ok=True)
            write_srt(captions, srt_path, use_khmer=True)

            def _export_progress(pct: int) -> None:
                # Map ffmpeg progress (0-100) into the 75-100 range
                self._emit_progress(75 + int(pct * 0.25))

            export_video(
                video_path=video_path,
                captions=captions,
                output_video_path=out_video,
                original_volume=1.0,
                mute_during_captions=True,
                progress_callback=_export_progress,
            )

            self._emit_progress(100)
            self.video_done.emit(idx, total, out_video)
            logger.info("[%d/%d] Exported → %s", idx, total, out_video)

        finally:
            # Clean up per-video TTS temp files
            import shutil
            try:
                shutil.rmtree(tts_dir, ignore_errors=True)
            except Exception:
                pass
