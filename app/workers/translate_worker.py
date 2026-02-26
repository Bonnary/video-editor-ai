"""QThread worker: translate captions to Khmer using googletrans."""
from __future__ import annotations

import importlib.util
import logging
import os
import sys
import time
import traceback
from typing import List

from PySide6.QtCore import QObject, Signal

from app.models.caption import Caption

logger = logging.getLogger(__name__)

# Load libs/googletrans/main.py directly to avoid name clash with libs/whisper/main.py
_TRANS_MAIN = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "libs", "googletrans", "main.py")
)
_spec = importlib.util.spec_from_file_location("googletrans_main", _TRANS_MAIN)
_googletrans_main = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_googletrans_main)
translate_text = _googletrans_main.translate_text


class TranslateWorker(QObject):
    """Translate each caption's original_text to Khmer in a background thread.

    Signals
    -------
    progress(int):           0–100 percent.
    caption_translated(int, str): emitted per caption (index, khmer_text).
    error(str):              emitted on exception.
    finished():              always emitted at the end.
    """

    MAX_RETRIES  = 3
    RETRY_DELAY  = 2.0   # seconds between retries

    progress           = Signal(int)
    caption_translated = Signal(int, str)   # (caption index, translated text)
    caption_skipped    = Signal(int)         # (caption index) – emitted when all retries fail
    error              = Signal(str)
    finished           = Signal()

    def __init__(self, captions: List[Caption]):
        super().__init__()
        self._captions  = captions
        self._cancelled = False

    def cancel(self) -> None:
        """Request cancellation. The worker will stop at the next safe checkpoint."""
        logger.info("TranslateWorker cancel requested")
        self._cancelled = True

    # ------------------------------------------------------------------ slot
    def run(self) -> None:
        logger.info("TranslateWorker starting — %d captions", len(self._captions))
        try:
            total = len(self._captions) or 1

            for i, cap in enumerate(self._captions):
                if self._cancelled:
                    logger.info("TranslateWorker: cancelled at caption %d", i)
                    return
                translated = None
                last_exc: Exception | None = None

                for attempt in range(1, self.MAX_RETRIES + 1):
                    try:
                        result = translate_text(cap.original_text, target_language="km")
                        if result:
                            translated = result
                            break
                    except Exception as exc:
                        last_exc = exc
                        logger.warning(
                            "Translation attempt %d/%d failed for caption %d: %s",
                            attempt, self.MAX_RETRIES, cap.index, exc,
                        )
                        if attempt < self.MAX_RETRIES:
                            time.sleep(self.RETRY_DELAY)

                if translated:
                    self.caption_translated.emit(cap.index, translated)
                else:
                    # All retries exhausted – skip
                    logger.error(
                        "Caption %d skipped — all %d translation attempts failed. Last error: %s",
                        cap.index, self.MAX_RETRIES, last_exc,
                    )
                    self.caption_skipped.emit(cap.index)

                pct = int((i + 1) / total * 100)
                self.progress.emit(pct)

            logger.info("TranslateWorker done")
            self.progress.emit(100)

        except Exception as exc:
            logger.error("TranslateWorker failed: %s", exc)
            logger.debug(traceback.format_exc())
            self.error.emit(str(exc))
        finally:
            self.finished.emit()
