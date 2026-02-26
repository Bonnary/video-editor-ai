"""Log viewer dialog — shows live application logs in a popup window."""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor, QKeySequence, QAction
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


# ---------------------------------------------------------------------------
# Qt-safe logging handler
# ---------------------------------------------------------------------------

class _QtLogSignaller(QObject):
    """Holds the signal — must be a QObject to use Signal."""
    record_emitted = Signal(logging.LogRecord)


class QtLogHandler(logging.Handler):
    """A logging.Handler that re-emits each record via a Qt signal.

    Connect ``signaller.record_emitted`` to any slot that accepts a
    ``logging.LogRecord``.
    """

    def __init__(self) -> None:
        super().__init__()
        self.signaller = _QtLogSignaller()

    def emit(self, record: logging.LogRecord) -> None:  # type: ignore[override]
        try:
            self.signaller.record_emitted.emit(record)
        except Exception:
            self.handleError(record)


# Singleton handler — created once, shared by the whole application.
_handler: QtLogHandler | None = None


def get_qt_log_handler() -> QtLogHandler:
    """Return (and lazily create) the application-wide QtLogHandler."""
    global _handler
    if _handler is None:
        _handler = QtLogHandler()
    return _handler


# ---------------------------------------------------------------------------
# Level colours
# ---------------------------------------------------------------------------

_LEVEL_COLORS: dict[int, str] = {
    logging.DEBUG:    "#888888",
    logging.INFO:     "#d4d4d4",
    logging.WARNING:  "#e5c07b",
    logging.ERROR:    "#e06c75",
    logging.CRITICAL: "#ff0000",
}


def _color_for(level: int) -> str:
    for threshold in sorted(_LEVEL_COLORS.keys(), reverse=True):
        if level >= threshold:
            return _LEVEL_COLORS[threshold]
    return "#d4d4d4"


# ---------------------------------------------------------------------------
# Log viewer dialog
# ---------------------------------------------------------------------------

class LogViewerDialog(QDialog):
    """A non-blocking dialog that streams live log output."""

    _MAX_LINES = 2000  # keep the last N lines to avoid unbounded memory use

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Application Logs")
        self.resize(900, 550)
        # Keep it on top but don't block the main window
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowStaysOnTopHint
        )

        self._build_ui()

        # Connect to the global handler
        handler = get_qt_log_handler()
        handler.signaller.record_emitted.connect(self._on_record)

        # Replay any records that were buffered before the dialog opened
        for rec in _record_buffer:
            self._on_record(rec)

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # ---- toolbar row ----
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Filter… (case-insensitive substring)")
        self._filter_edit.textChanged.connect(self._apply_filter)
        toolbar.addWidget(QLabel("Filter:"))
        toolbar.addWidget(self._filter_edit, stretch=1)

        self._auto_scroll = QCheckBox("Auto-scroll")
        self._auto_scroll.setChecked(True)
        toolbar.addWidget(self._auto_scroll)

        copy_btn = QPushButton("Copy All")
        copy_btn.clicked.connect(self._copy_all)
        toolbar.addWidget(copy_btn)

        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._clear)
        toolbar.addWidget(clear_btn)

        layout.addLayout(toolbar)

        # ---- text area ----
        self._text = QTextEdit()
        self._text.setReadOnly(True)
        font = QFont("Consolas", 9)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self._text.setFont(font)
        self._text.setStyleSheet(
            "QTextEdit { background-color: #1e1e1e; color: #d4d4d4; border: none; }"
        )
        layout.addWidget(self._text, stretch=1)

        # ---- bottom row ----
        bottom = QHBoxLayout()
        self._count_label = QLabel("0 entries")
        bottom.addWidget(self._count_label)
        bottom.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.hide)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)

        # internal state
        self._all_lines: list[tuple[int, str]] = []   # (level, formatted_text)
        self._entry_count = 0

    # ------------------------------------------------------------------ slots

    @Slot(logging.LogRecord)
    def _on_record(self, record: logging.LogRecord) -> None:
        formatter = logging.Formatter(
            "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
            datefmt="%H:%M:%S",
        )
        text = formatter.format(record)
        self._all_lines.append((record.levelno, text))

        # Trim buffer
        if len(self._all_lines) > self._MAX_LINES:
            self._all_lines = self._all_lines[-self._MAX_LINES :]
            self._rebuild()
        else:
            self._append_line(record.levelno, text)

        self._entry_count += 1
        self._count_label.setText(f"{self._entry_count} entries")

        if self._auto_scroll.isChecked():
            self._text.moveCursor(QTextCursor.MoveOperation.End)

    # ------------------------------------------------------------------ helpers

    def _passes_filter(self, text: str) -> bool:
        f = self._filter_edit.text().strip().lower()
        return (not f) or (f in text.lower())

    def _append_line(self, level: int, text: str) -> None:
        if not self._passes_filter(text):
            return
        color = _color_for(level)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor = self._text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not self._text.toPlainText() == "":
            cursor.insertText("\n")
        cursor.insertText(text, fmt)

    def _rebuild(self) -> None:
        """Re-render the text area (used after filter change or buffer trim)."""
        self._text.clear()
        for level, text in self._all_lines:
            self._append_line(level, text)

    def _apply_filter(self, _: str) -> None:
        self._rebuild()

    def _copy_all(self) -> None:
        QApplication.clipboard().setText(self._text.toPlainText())

    def _clear(self) -> None:
        self._all_lines.clear()
        self._entry_count = 0
        self._count_label.setText("0 entries")
        self._text.clear()

    # Keep the dialog hidden rather than destroyed when user closes it
    def closeEvent(self, event):  # type: ignore[override]
        event.ignore()
        self.hide()


# ---------------------------------------------------------------------------
# Early-buffer: capture records emitted before the dialog is created
# ---------------------------------------------------------------------------

_record_buffer: list[logging.LogRecord] = []
_MAX_BUFFER = 500


class _BufferHandler(logging.Handler):
    """Records log entries before the UI is ready."""
    def emit(self, record: logging.LogRecord) -> None:
        if len(_record_buffer) < _MAX_BUFFER:
            _record_buffer.append(record)


_buffer_handler = _BufferHandler()
logging.getLogger().addHandler(_buffer_handler)
