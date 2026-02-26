"""Video preview widget with playback controls."""
from __future__ import annotations

from PySide6.QtCore import Qt, QUrl, Slot
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)


def _ms_to_hms(ms: int) -> str:
    s  = ms // 1000
    m  = s  // 60;  s  %= 60
    h  = m  // 60;  m  %= 60
    return f"{h:02d}:{m:02d}:{s:02d}"


class VideoPlayer(QWidget):
    """Embeds a QMediaPlayer + controls inside a QWidget."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self._connect_signals()

    # ------------------------------------------------------------------ build
    def _build_ui(self) -> None:
        self.player       = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(1.0)

        self.video_widget = QVideoWidget()
        self.video_widget.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Expanding
        )
        self.player.setVideoOutput(self.video_widget)

        # ---- Position slider ----
        self.position_slider = QSlider(Qt.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_slider.setSingleStep(1000)

        # ---- Time label ----
        self.time_label = QLabel("00:00:00 / 00:00:00")
        self.time_label.setAlignment(Qt.AlignCenter)

        # ---- Control buttons ----
        self.play_btn  = QPushButton("▶  Play")
        self.pause_btn = QPushButton("⏸  Pause")
        self.stop_btn  = QPushButton("⏹  Stop")

        ctrl = QHBoxLayout()
        ctrl.addWidget(self.play_btn)
        ctrl.addWidget(self.pause_btn)
        ctrl.addWidget(self.stop_btn)
        ctrl.addStretch()
        ctrl.addWidget(self.time_label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.video_widget, stretch=1)
        layout.addWidget(self.position_slider)
        layout.addLayout(ctrl)

    def _connect_signals(self) -> None:
        self.play_btn.clicked.connect(self.player.play)
        self.pause_btn.clicked.connect(self.player.pause)
        self.stop_btn.clicked.connect(self.player.stop)

        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)

        # Allow manual scrubbing
        self.position_slider.sliderMoved.connect(self.player.setPosition)

    # ------------------------------------------------------------------ API
    def load(self, path: str) -> None:
        """Load and immediately preview (but don't autoplay) a video file."""
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.pause()     # show first frame

    def seek_to(self, seconds: float) -> None:
        """Jump to a position in seconds."""
        self.player.setPosition(int(seconds * 1000))

    # ------------------------------------------------------------------ slots
    @Slot(int)
    def _on_position_changed(self, pos_ms: int) -> None:
        if not self.position_slider.isSliderDown():
            self.position_slider.setValue(pos_ms)
        dur = self.player.duration()
        self.time_label.setText(f"{_ms_to_hms(pos_ms)} / {_ms_to_hms(dur)}")

    @Slot(int)
    def _on_duration_changed(self, dur_ms: int) -> None:
        self.position_slider.setRange(0, dur_ms)
