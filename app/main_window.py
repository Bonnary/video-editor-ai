"""Main application window."""
from __future__ import annotations

import os
import tempfile
from typing import List, Optional

from PySide6.QtCore import QObject, QThread, Qt, Slot
from PySide6.QtGui import QAction, QDragEnterEvent, QDropEvent, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from app.models.caption import Caption
from app.widgets.caption_table import CaptionTable
from app.widgets.loading_dialog import LoadingDialog
from app.widgets.log_viewer import LogViewerDialog
from app.widgets.video_player import VideoPlayer
from app.workers.tts_worker import DEFAULT_VOICE

# Resolve logo path relative to this file
_IMAGES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "images")


class MainWindow(QMainWindow):
    """Top-level window for the Khmer AI Video Dubber."""

    # ------------------------------------------------------------------ init
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Khmer AI Video Dubber")
        self.resize(1400, 800)

        # Window icon
        logo_path = os.path.join(_IMAGES_DIR, "logo.jpg")
        if os.path.exists(logo_path):
            self.setWindowIcon(QIcon(logo_path))

        self._video_path: Optional[str] = None
        self._tts_dir: Optional[str] = None     # temp dir for TTS audio files
        self._busy = False
        self._was_cancelled = False

        # Active QThread/worker references (prevent GC)
        self._thread: Optional[QThread] = None
        self._worker: Optional[QObject] = None

        self.setAcceptDrops(True)
        self._log_viewer: LogViewerDialog | None = None
        self._build_ui()
        self._setup_menu()
        self._setup_statusbar()
        self._update_button_states()

    # ------------------------------------------------------------------ build
    def _build_ui(self) -> None:
        # ---- Toolbar ----
        toolbar = QToolBar("Main Toolbar", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.load_btn       = QPushButton("📂  Load Video")
        self.transcribe_btn = QPushButton("🎙️  Transcribe")
        self.translate_btn  = QPushButton("🌐  Translate → Khmer")
        self.tts_btn        = QPushButton("🔊  Generate TTS")
        self.export_btn     = QPushButton("💾  Export")

        # Transcription language selector
        TRANSCRIPTION_LANGUAGES = [
            ("Chinese (Mandarin)", "zh"),
            ("English",           "en"),
            ("Korean",            "ko"),
            ("Japanese",          "ja"),
            ("Vietnamese",        "vi"),
        ]
        self.lang_combo = QComboBox()
        self.lang_combo.setFixedHeight(32)
        for label, code in TRANSCRIPTION_LANGUAGES:
            self.lang_combo.addItem(label, code)
        # Default to Chinese
        self.lang_combo.setCurrentIndex(0)
        lang_label = QLabel("  Language: ")

        # Whisper model selector
        WHISPER_MODELS = [
            ("tiny   (fastest)",  "tiny"),
            ("base",              "base"),
            ("small",             "small"),
            ("medium",            "medium"),
            ("large",             "large"),
            ("large-v2",          "large-v2"),
            ("large-v3 (best)",   "large-v3"),
            ("auto (GPU→medium, CPU→small)", "auto"),
        ]
        self.model_combo = QComboBox()
        self.model_combo.setFixedHeight(32)
        for label, value in WHISPER_MODELS:
            self.model_combo.addItem(label, value)
        # Default to "auto"
        self.model_combo.setCurrentIndex(len(WHISPER_MODELS) - 1)
        model_label = QLabel("  Whisper model: ")

        for btn in (self.load_btn, self.transcribe_btn, self.translate_btn,
                    self.tts_btn, self.export_btn):
            btn.setFixedHeight(32)
            toolbar.addWidget(btn)

        self.cancel_btn = QPushButton("\U0001f6ab  Cancel")
        self.cancel_btn.setFixedHeight(32)
        self.cancel_btn.setVisible(False)
        toolbar.addSeparator()
        toolbar.addWidget(self.cancel_btn)

        toolbar.addSeparator()
        toolbar.addWidget(model_label)
        toolbar.addWidget(self.model_combo)

        toolbar.addSeparator()
        toolbar.addWidget(lang_label)
        toolbar.addWidget(self.lang_combo)

        # ---- Central splitter ----
        self.video_player   = VideoPlayer()
        self.caption_table  = CaptionTable()

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._wrap_with_label(self.video_player, "📹  Video Preview  (drag & drop a video file here)"))
        splitter.addWidget(self._wrap_with_label(self.caption_table, "📝  Captions"))
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)

        self.setCentralWidget(splitter)

        # ---- Connect signals ----
        self.load_btn.clicked.connect(self._on_load_clicked)
        self.transcribe_btn.clicked.connect(self._on_transcribe_clicked)
        self.translate_btn.clicked.connect(self._on_translate_clicked)
        self.tts_btn.clicked.connect(self._on_tts_clicked)
        self.export_btn.clicked.connect(self._on_export_clicked)
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)

        self.caption_table.caption_selected.connect(self.video_player.seek_to)

    @staticmethod
    def _wrap_with_label(widget: QWidget, title: str) -> QWidget:
        container = QWidget()
        lbl = QLabel(f"<b>{title}</b>")
        lbl.setContentsMargins(4, 4, 4, 0)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(lbl)
        layout.addWidget(widget, stretch=1)
        return container

    def _setup_menu(self) -> None:
        menu      = self.menuBar()
        file_menu = menu.addMenu("&File")

        open_act = QAction("&Open Video…", self)
        open_act.setShortcut(QKeySequence.Open)
        open_act.triggered.connect(self._on_load_clicked)
        file_menu.addAction(open_act)

        file_menu.addSeparator()

        export_act = QAction("&Export…", self)
        export_act.setShortcut(QKeySequence("Ctrl+E"))
        export_act.triggered.connect(self._on_export_clicked)
        file_menu.addAction(export_act)

        file_menu.addSeparator()
        quit_act = QAction("&Quit", self)
        quit_act.setShortcut(QKeySequence.Quit)
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

        help_menu = menu.addMenu("&Help")
        logs_act = QAction("View &Logs…", self)
        logs_act.setShortcut(QKeySequence("Ctrl+L"))
        logs_act.triggered.connect(self._on_view_logs)
        help_menu.addAction(logs_act)

    def _on_view_logs(self) -> None:
        if self._log_viewer is None:
            self._log_viewer = LogViewerDialog(self)
        self._log_viewer.show()
        self._log_viewer.raise_()
        self._log_viewer.activateWindow()

    def _setup_statusbar(self) -> None:
        self.status_label = QLabel("Ready — drop a video file or click Load Video.")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedWidth(220)
        self.progress_bar.setVisible(False)

        self.statusBar().addWidget(self.status_label, 1)
        self.statusBar().addPermanentWidget(self.progress_bar)

    # ------------------------------------------------------------------ drag & drop
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            urls  = event.mimeData().urls()
            exts  = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv"}
            valid = any(
                os.path.splitext(u.toLocalFile())[1].lower() in exts
                for u in urls
            )
            if valid:
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            self._load_video(path)

    # ------------------------------------------------------------------ helpers
    def _load_video(self, path: str) -> None:
        if not os.path.isfile(path):
            self._show_error(f"File not found:\n{path}")
            return
        self._video_path = path
        self._tts_dir    = tempfile.mkdtemp(prefix="kh_tts_")
        self.caption_table.clear()
        self.video_player.load(path)
        self._set_status(f"Loaded: {os.path.basename(path)}")
        self._update_button_states()

    def _set_status(self, msg: str) -> None:
        self.status_label.setText(msg)

    def _set_busy(self, busy: bool, label: str = "") -> None:
        self._busy = busy
        self.progress_bar.setVisible(busy)
        if busy:
            self.progress_bar.setValue(0)
        status = label if label else ("Ready." if not busy else "Working…")
        self._set_status(status)
        self.lang_combo.setEnabled(not busy)
        self.model_combo.setEnabled(not busy)
        self.cancel_btn.setVisible(busy)
        self.cancel_btn.setEnabled(busy)
        self._update_button_states()

    def _update_button_states(self) -> None:
        if self._busy:
            # Disable everything while a background job is running
            for btn in (self.load_btn, self.transcribe_btn, self.translate_btn,
                        self.tts_btn, self.export_btn):
                btn.setEnabled(False)
            return

        has_video    = self._video_path is not None
        has_captions = bool(self.caption_table.get_captions())

        self.load_btn.setEnabled(True)
        self.transcribe_btn.setEnabled(has_video)
        self.translate_btn.setEnabled(has_captions)
        self.tts_btn.setEnabled(has_captions)
        self.export_btn.setEnabled(has_captions)

    def _show_error(self, msg: str) -> None:
        QMessageBox.critical(self, "Error", msg)

    @Slot()
    def _on_cancel_clicked(self) -> None:
        """Stop the currently running background worker."""
        self._was_cancelled = True
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setText("\U0001f6ab  Cancelling…")
        self._set_status("Cancelling…")
        if self._worker and hasattr(self._worker, "cancel"):
            self._worker.cancel()

    def _on_worker_finished(self, success_msg: str = "") -> None:
        """Called when any background worker emits finished()."""
        was_cancelled = self._was_cancelled
        self._was_cancelled = False
        self.cancel_btn.setText("\U0001f6ab  Cancel")
        self._set_busy(False, "Cancelled." if was_cancelled else success_msg)

    def _start_worker(self, worker: QObject, thread: QThread) -> None:
        """Wire up and start a worker/thread pair."""
        self._worker = worker
        self._thread = thread

        worker.moveToThread(thread)
        thread.started.connect(worker.run)

        # Generic cleanup
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        thread.start()

    # ------------------------------------------------------------------ button handlers
    @Slot()
    def _on_load_clicked(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Video", "",
            "Video Files (*.mp4 *.mkv *.avi *.mov *.webm *.flv *.wmv);;All Files (*)"
        )
        if path:
            self._load_video(path)

    @Slot()
    def _on_transcribe_clicked(self) -> None:
        if not self._video_path:
            return

        from app.workers.transcribe_worker import TranscribeWorker

        model_name = self.model_combo.currentData() or "auto"
        language   = self.lang_combo.currentData() or "zh"
        lang_label = self.lang_combo.currentText()
        self._set_busy(True, f"Transcribing audio with Whisper [{model_name}]  (this may take a while)…")

        worker = TranscribeWorker(self._video_path, model_name=model_name, language=language)
        thread = QThread(self)

        # Loading popup
        self._loading_dlg = LoadingDialog(
            self,
            title="Whisper Transcription",
            message=f"Transcribing audio with Whisper…\nModel: {model_name}\nLanguage: {lang_label}\nThis may take a while.",
        )
        worker.progress.connect(self._loading_dlg.set_progress)
        worker.finished.connect(self._loading_dlg.close)
        self._loading_dlg.cancel_requested.connect(self._on_cancel_clicked)

        worker.progress.connect(self.progress_bar.setValue)
        worker.captions_ready.connect(self._on_captions_ready)
        worker.error.connect(self._on_worker_error)
        worker.finished.connect(self._on_worker_finished)

        self._start_worker(worker, thread)
        self._loading_dlg.show()

    @Slot(list)
    def _on_captions_ready(self, captions: List[Caption]) -> None:
        self.caption_table.load_captions(captions)
        self._set_status(f"Transcription complete — {len(captions)} segments.")
        self._update_button_states()

    @Slot(int)
    def _on_caption_skipped(self, index: int) -> None:
        self._translate_skipped += 1
        self._set_status(f"Translating… ({self._translate_skipped} caption(s) skipped after retries)")

    @Slot()
    def _on_translate_finished(self) -> None:
        was_cancelled = self._was_cancelled
        self._was_cancelled = False
        self.cancel_btn.setText("\U0001f6ab  Cancel")
        skipped = self._translate_skipped
        if was_cancelled:
            self._set_busy(False, "Cancelled.")
        else:
            msg = "Translation complete."
            if skipped:
                msg += f"  {skipped} caption(s) could not be translated and were skipped."
            self._set_busy(False, msg)
        self._translate_skipped = 0

    @Slot()
    def _on_translate_clicked(self) -> None:
        captions = self.caption_table.get_captions()
        if not captions:
            return

        from app.workers.translate_worker import TranslateWorker

        self._set_busy(True, "Translating captions to Khmer…")

        worker = TranslateWorker(captions)
        thread = QThread(self)

        self._translate_skipped = 0

        self._loading_dlg = LoadingDialog(
            self,
            title="Translate to Khmer",
            message=f"Translating {len(captions)} captions to Khmer…\nThis may take a while.",
        )
        worker.progress.connect(self._loading_dlg.set_progress)
        worker.finished.connect(self._loading_dlg.close)
        self._loading_dlg.cancel_requested.connect(self._on_cancel_clicked)

        worker.progress.connect(self.progress_bar.setValue)
        worker.caption_translated.connect(self.caption_table.update_khmer_text)
        worker.caption_skipped.connect(self._on_caption_skipped)
        worker.error.connect(self._on_worker_error)
        worker.finished.connect(self._on_translate_finished)

        self._start_worker(worker, thread)
        self._loading_dlg.show()

    @Slot()
    def _on_tts_clicked(self) -> None:
        captions = self.caption_table.get_captions()
        if not captions:
            return

        voice = DEFAULT_VOICE

        from app.workers.tts_worker import TTSWorker

        self._set_busy(True, "Generating Khmer TTS audio…")

        worker = TTSWorker(captions, self._tts_dir, voice=voice)
        thread = QThread(self)

        self._loading_dlg = LoadingDialog(
            self,
            title="Generate TTS",
            message=f"Generating TTS audio for {len(captions)} captions…\nThis may take a while.",
        )
        worker.progress.connect(self._loading_dlg.set_progress)
        worker.finished.connect(self._loading_dlg.close)
        self._loading_dlg.cancel_requested.connect(self._on_cancel_clicked)

        worker.progress.connect(self.progress_bar.setValue)
        worker.caption_audio_ready.connect(self.caption_table.update_tts_path)
        worker.error.connect(self._on_worker_error)
        worker.finished.connect(lambda: self._on_worker_finished("TTS generation complete."))

        self._start_worker(worker, thread)
        self._loading_dlg.show()

    @Slot()
    def _on_export_clicked(self) -> None:
        if not self._video_path:
            return

        captions = self.caption_table.get_captions()
        if not captions:
            return

        out_path, _ = QFileDialog.getSaveFileName(
            self, "Export Video", "output_dubbed.mp4",
            "MP4 Video (*.mp4);;All Files (*)"
        )
        if not out_path:
            return

        from app.workers.export_worker import ExportWorker

        self._set_busy(True, "Exporting video with dubbed audio…")

        worker = ExportWorker(
            video_path=self._video_path,
            captions=captions,
            output_video_path=out_path,
            original_volume=1.0,
            mute_during_captions=True,
        )
        thread = QThread(self)

        # Loading popup
        self._loading_dlg = LoadingDialog(
            self,
            title="Exporting Video",
            message="Exporting video with dubbed audio…\nThis may take a while.",
        )
        worker.progress.connect(self._loading_dlg.set_progress)
        worker.finished.connect(self._loading_dlg.close)
        self._loading_dlg.cancel_requested.connect(self._on_cancel_clicked)

        worker.progress.connect(self.progress_bar.setValue)
        worker.done.connect(self._on_export_finished)
        worker.error.connect(self._on_worker_error)
        worker.finished.connect(self._on_worker_finished)

        self._start_worker(worker, thread)
        self._loading_dlg.show()

    @Slot(str)
    def _on_export_finished(self, output_path: str) -> None:
        self._set_busy(False)
        srt_path = os.path.splitext(output_path)[0] + ".srt"
        QMessageBox.information(
            self, "Export complete",
            f"Video saved to:\n{output_path}\n\nSRT saved to:\n{srt_path}"
        )

    @Slot(str)
    def _on_worker_error(self, message: str) -> None:
        self._set_busy(False)
        self._show_error(message)

    # ------------------------------------------------------------------ close
    def closeEvent(self, event) -> None:
        if self._tts_dir and os.path.isdir(self._tts_dir):
            import shutil
            try:
                shutil.rmtree(self._tts_dir, ignore_errors=True)
            except Exception:
                pass
        event.accept()
