"""Editable caption table widget."""
from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.workers.tts_worker import KHMER_VOICES, DEFAULT_VOICE

from app.models.caption import Caption

# Column indices
COL_IDX     = 0
COL_START   = 1
COL_END     = 2
COL_ORIG    = 3
COL_KHMER   = 4
COL_SPEED   = 5
COL_OFFSET  = 6
COL_VOICE   = 7

HEADERS = ["#", "Start (s)", "End (s)", "Original Text", "Khmer Text", "Speed ×", "Offset (s)", "Voice"]

_EDITABLE_COLS = {COL_ORIG, COL_KHMER, COL_START, COL_END, COL_SPEED, COL_OFFSET}


def _fmt(v: float) -> str:
    return f"{v:.3f}"


class CaptionTable(QWidget):
    """QTableWidget wrapper exposing caption data with edit callbacks.

    Signals
    -------
    caption_selected(float):    emitted with caption start time when a row is
                                clicked (for video player sync).
    data_changed():             emitted whenever any cell is edited.
    """

    caption_selected = Signal(float)    # start-time of clicked caption
    data_changed     = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._captions: List[Caption] = []
        self._ignore_changes = False
        self._build_ui()

    # ------------------------------------------------------------------ build
    def _build_ui(self) -> None:
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)

        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(COL_ORIG,  QHeaderView.Stretch)
        hdr.setSectionResizeMode(COL_KHMER, QHeaderView.Stretch)
        for col in (COL_IDX, COL_START, COL_END, COL_SPEED, COL_OFFSET, COL_VOICE):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeToContents)

        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.cellClicked.connect(self._on_cell_clicked)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("Captions  (double-click a cell to edit)"))
        layout.addWidget(self.table)

    # ------------------------------------------------------------------ API
    def load_captions(self, captions: List[Caption]) -> None:
        """Replace the table contents with a new caption list."""
        self._captions = list(captions)
        self._rebuild()

    def get_captions(self) -> List[Caption]:
        """Return the current (possibly edited) caption list.

        Reads the per-row Voice combo widgets so cap.voice is always up-to-date.
        """
        for row, cap in enumerate(self._captions):
            combo: QComboBox = self.table.cellWidget(row, COL_VOICE)
            if combo is not None:
                cap.voice = combo.currentData() or DEFAULT_VOICE
        return list(self._captions)

    def update_khmer_text(self, caption_index: int, text: str) -> None:
        """Set the Khmer text for a specific caption index (1-based)."""
        for cap in self._captions:
            if cap.index == caption_index:
                cap.khmer_text = text
                break
        self._update_cell_text(caption_index, COL_KHMER, text)

    def update_tts_path(self, caption_index: int, path: str) -> None:
        """Record the TTS audio path and colour the row green."""
        for cap in self._captions:
            if cap.index == caption_index:
                cap.tts_audio_path = path
                break
        row = self._row_for_index(caption_index)
        if row is not None:
            for col in range(len(HEADERS)):
                item = self.table.item(row, col)
                if item:
                    item.setBackground(QColor("#d4edda"))

    def clear(self) -> None:
        self._captions = []
        self.table.setRowCount(0)

    # ------------------------------------------------------------------ internals
    def _rebuild(self) -> None:
        self._ignore_changes = True
        self.table.setRowCount(0)
        self.table.setRowCount(len(self._captions))

        for row, cap in enumerate(self._captions):
            self._set_row(row, cap)

        self._ignore_changes = False

    def _set_row(self, row: int, cap: Caption) -> None:
        items = [
            str(cap.index),
            _fmt(cap.start),
            _fmt(cap.end),
            cap.original_text,
            cap.khmer_text,
            _fmt(cap.speed),
            _fmt(cap.offset),
        ]
        for col, text in enumerate(items):
            item = QTableWidgetItem(text)
            if col not in _EDITABLE_COLS:
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, col, item)

        # Voice combo (col 7)
        voice_combo = QComboBox()
        for label, value in KHMER_VOICES.items():
            voice_combo.addItem(label, value)
        # Select current voice (default to first item = Female)
        target = cap.voice or DEFAULT_VOICE
        idx = voice_combo.findData(target)
        if idx >= 0:
            voice_combo.setCurrentIndex(idx)
        self.table.setCellWidget(row, COL_VOICE, voice_combo)

    def _row_for_index(self, caption_index: int) -> Optional[int]:
        for row, cap in enumerate(self._captions):
            if cap.index == caption_index:
                return row
        return None

    def _update_cell_text(self, caption_index: int, col: int, text: str) -> None:
        row = self._row_for_index(caption_index)
        if row is None:
            return
        self._ignore_changes = True
        item = self.table.item(row, col)
        if item:
            item.setText(text)
        self._ignore_changes = False

    # ------------------------------------------------------------------ slots
    @Slot(QTableWidgetItem)
    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._ignore_changes:
            return
        row = item.row()
        col = item.column()
        if row >= len(self._captions):
            return

        cap  = self._captions[row]
        text = item.text().strip()

        try:
            if col == COL_ORIG:
                cap.original_text = text
            elif col == COL_KHMER:
                cap.khmer_text = text
            elif col == COL_START:
                cap.start = float(text)
            elif col == COL_END:
                cap.end = float(text)
            elif col == COL_SPEED:
                val = float(text)
                cap.speed = max(0.25, min(4.0, val))
            elif col == COL_OFFSET:
                cap.offset = float(text)
        except ValueError:
            pass    # keep old value on bad input

        self.data_changed.emit()

    @Slot(int, int)
    def _on_cell_clicked(self, row: int, _col: int) -> None:
        if row < len(self._captions):
            self.caption_selected.emit(self._captions[row].start)
