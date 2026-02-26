"""Modal loading dialog shown during long background tasks."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QProgressBar,
    QVBoxLayout,
)


class LoadingDialog(QDialog):
    """Blocking modal dialog with a progress bar and status message.

    Usage
    -----
    dlg = LoadingDialog(parent, title="Whisper Transcription",
                        message="Transcribing audio with Whisper…\\nThis may take a while.")
    worker.progress.connect(dlg.set_progress)
    worker.finished.connect(dlg.close)
    dlg.show()          # non-blocking show; the worker runs in a QThread
    """

    def __init__(
        self,
        parent=None,
        *,
        title: str = "Please wait…",
        message: str = "Working…",
    ) -> None:
        super().__init__(parent)

        # Remove the close / minimise / maximise buttons
        self.setWindowFlags(
            Qt.Dialog
            | Qt.CustomizeWindowHint
            | Qt.WindowTitleHint
        )
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(360)
        self.setFixedHeight(130)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        self._label = QLabel(message)
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setWordWrap(True)
        layout.addWidget(self._label)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setTextVisible(True)
        layout.addWidget(self._bar)

    # ------------------------------------------------------------------ slots
    def set_progress(self, value: int) -> None:
        """Update the progress bar (0–100)."""
        self._bar.setValue(value)

    def set_message(self, text: str) -> None:
        """Update the status message."""
        self._label.setText(text)
