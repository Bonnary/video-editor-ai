"""QThread worker: export the final video with mixed audio using ffmpeg."""
from __future__ import annotations

import logging
import os
import traceback
from typing import List

from PySide6.QtCore import QObject, Signal

from app.models.caption import Caption
from app.utils.ffmpeg_utils import export_video
from app.utils.srt_utils import write_srt

logger = logging.getLogger(__name__)


class ExportWorker(QObject):
    """Render the final video + SRT file.

    Pipeline
    --------
    1. Write Khmer SRT file alongside the output video.
    2. Call ffmpeg export: lower original audio, overlay TTS clips at
       their effective start times, encode to AAC audio / copy video.

    Signals
    -------
    progress(int):   0–100 percent.
    finished(str):   emitted with the output video path on success.
    error(str):      emitted on exception.
    """

    progress = Signal(int)
    done     = Signal(str)       # output video path — emitted on success
    finished = Signal()          # always emitted at the end (for QThread cleanup)
    error    = Signal(str)

    def __init__(
        self,
        video_path: str,
        captions: List[Caption],
        output_video_path: str,
        original_volume: float = 0.3,
        mute_during_captions: bool = False,
    ):
        super().__init__()
        self._video_path           = video_path
        self._captions             = captions
        self._output_video_path    = output_video_path
        self._original_volume      = original_volume
        self._mute_during_captions = mute_during_captions
        self._cancelled            = False

    def cancel(self) -> None:
        """Request cancellation. The worker will stop at the next safe checkpoint."""
        logger.info("ExportWorker cancel requested")
        self._cancelled = True

    # ------------------------------------------------------------------ slot
    def run(self) -> None:
        logger.info("ExportWorker starting — output=%s", self._output_video_path)
        try:
            if self._cancelled:
                logger.info("ExportWorker: cancelled before start")
                return

            # 1. Write SRT
            srt_path = os.path.splitext(self._output_video_path)[0] + ".srt"
            logger.info("Writing SRT to %s", srt_path)
            write_srt(self._captions, srt_path, use_khmer=True)
            self.progress.emit(5)

            if self._cancelled:
                logger.info("ExportWorker: cancelled before ffmpeg")
                return

            # 2. Render video
            logger.info("Running ffmpeg export (volume=%.2f, mute=%s)…",
                        self._original_volume, self._mute_during_captions)
            export_video(
                video_path=self._video_path,
                captions=self._captions,
                output_video_path=self._output_video_path,
                original_volume=self._original_volume,
                mute_during_captions=self._mute_during_captions,
                progress_callback=lambda pct: self.progress.emit(5 + int(pct * 0.95)),
            )

            logger.info("ExportWorker done — %s", self._output_video_path)
            self.done.emit(self._output_video_path)

        except Exception as exc:
            logger.error("ExportWorker failed: %s", exc)
            logger.debug(traceback.format_exc())
            self.error.emit(str(exc))
        finally:
            self.finished.emit()
