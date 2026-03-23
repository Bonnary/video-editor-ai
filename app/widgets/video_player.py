"""Video preview widget with playback controls and interactive image overlay editor."""
from __future__ import annotations

from typing import List

from PySide6.QtCore import QPointF, QRectF, QSizeF, Qt, QUrl, Slot
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (
    QFileDialog,
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from app.models.image_overlay import ImageOverlay


def _ms_to_hms(ms: int) -> str:
    s = ms // 1000
    m = s // 60;  s %= 60
    h = m // 60;  m %= 60
    return f"{h:02d}:{m:02d}:{s:02d}"


# ---------------------------------------------------------------------------
#  Corner handle constants
# ---------------------------------------------------------------------------
_TL, _TR, _BR, _BL = 0, 1, 2, 3
_HANDLE_SIZE = 12.0   # pixels in scene (video) coordinates


class _OverlayItem(QGraphicsObject):
    """Draggable and resizable image overlay drawn on top of the video.

    Position is stored as ``self.pos()`` in scene coordinates (which match the
    video's native pixel dimensions).  Width/height are kept in ``_w``/``_h``.
    """

    def __init__(
        self,
        pixmap: QPixmap,
        w: float,
        h: float,
        image_path: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._pixmap = pixmap
        self._w = float(w)
        self._h = float(h)
        self.image_path = image_path

        # Resize-drag state
        self._drag_handle: int | None = None
        self._drag_start_scene = QPointF()
        self._drag_start_pos   = QPointF()
        self._drag_start_w     = 0.0
        self._drag_start_h     = 0.0

        self.setFlag(QGraphicsItem.ItemIsMovable,            True)
        self.setFlag(QGraphicsItem.ItemIsSelectable,         True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)

    # ------------------------------------------------------------------ geometry

    def local_rect(self) -> QRectF:
        """Bounding rect in item-local coordinates (top-left = 0,0)."""
        return QRectF(0, 0, self._w, self._h)

    def _handle_rects(self) -> list[QRectF]:
        """Four corner handle rects in item-local coordinates."""
        hs = _HANDLE_SIZE / 2
        w, h = self._w, self._h
        return [
            QRectF(-hs,     -hs,     _HANDLE_SIZE, _HANDLE_SIZE),  # TL
            QRectF(w - hs,  -hs,     _HANDLE_SIZE, _HANDLE_SIZE),  # TR
            QRectF(w - hs,  h - hs,  _HANDLE_SIZE, _HANDLE_SIZE),  # BR
            QRectF(-hs,     h - hs,  _HANDLE_SIZE, _HANDLE_SIZE),  # BL
        ]

    def boundingRect(self) -> QRectF:
        hs = _HANDLE_SIZE
        return QRectF(-hs, -hs, self._w + 2 * hs, self._h + 2 * hs)

    # ------------------------------------------------------------------ paint

    def paint(self, painter: QPainter, option, widget=None) -> None:
        rect = QRectF(0, 0, self._w, self._h)
        painter.drawPixmap(rect.toRect(), self._pixmap)

        if self.isSelected():
            pen = QPen(QColor(0, 160, 255), 2.0)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect)

            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 160, 255))
            for r in self._handle_rects():
                painter.drawRect(r)

    # ------------------------------------------------------------------ mouse events

    def _hit_handle(self, local_pos: QPointF) -> int | None:
        for i, r in enumerate(self._handle_rects()):
            if r.contains(local_pos):
                return i
        return None

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            h = self._hit_handle(event.pos())
            if h is not None:
                self._drag_handle      = h
                self._drag_start_scene = QPointF(event.scenePos())
                self._drag_start_pos   = QPointF(self.pos())
                self._drag_start_w     = self._w
                self._drag_start_h     = self._h
                event.accept()
                return
        self._drag_handle = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_handle is not None:
            delta = event.scenePos() - self._drag_start_scene
            self._apply_resize(self._drag_handle, delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_handle = None
        super().mouseReleaseEvent(event)

    def _apply_resize(self, handle: int, delta: QPointF) -> None:
        """Resize the item by dragging *handle* by *delta* scene pixels."""
        _MIN = 20.0
        dx, dy = delta.x(), delta.y()
        ox = self._drag_start_pos.x()
        oy = self._drag_start_pos.y()
        ow = self._drag_start_w
        oh = self._drag_start_h

        if handle == _TL:
            nw = max(_MIN, ow - dx);  nh = max(_MIN, oh - dy)
            self.setPos(ox + ow - nw, oy + oh - nh)
        elif handle == _TR:
            nw = max(_MIN, ow + dx);  nh = max(_MIN, oh - dy)
            self.setPos(ox, oy + oh - nh)
        elif handle == _BR:
            nw = max(_MIN, ow + dx);  nh = max(_MIN, oh + dy)
            self.setPos(ox, oy)
        elif handle == _BL:
            nw = max(_MIN, ow - dx);  nh = max(_MIN, oh + dy)
            self.setPos(ox + ow - nw, oy)
        else:
            return

        self.prepareGeometryChange()
        self._w = nw
        self._h = nh
        self.update()

    # ------------------------------------------------------------------ hover

    def hoverMoveEvent(self, event) -> None:
        h = self._hit_handle(event.pos())
        if h in (_TL, _BR):
            self.setCursor(QCursor(Qt.SizeFDiagCursor))
        elif h in (_TR, _BL):
            self.setCursor(QCursor(Qt.SizeBDiagCursor))
        else:
            self.setCursor(QCursor(Qt.SizeAllCursor))
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self.unsetCursor()
        super().hoverLeaveEvent(event)


# ---------------------------------------------------------------------------
#  Scene subclass — handles Delete key to remove selected overlays
# ---------------------------------------------------------------------------

class _OverlayScene(QGraphicsScene):
    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Delete:
            for item in list(self.selectedItems()):
                if isinstance(item, _OverlayItem):
                    self.removeItem(item)
        else:
            super().keyPressEvent(event)


# ---------------------------------------------------------------------------
#  Public widget
# ---------------------------------------------------------------------------

class VideoPlayer(QWidget):
    """Video preview with playback controls and an interactive image overlay editor.

    Overlay images can be added, dragged, and resized directly on the video
    preview.  Call ``get_overlays()`` to retrieve the current overlay list
    (normalised to 0–1 fractions of the video dimensions) for use in the
    ffmpeg export pipeline.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._video_size = QSizeF(1280, 720)
        self._build_ui()
        self._connect_signals()

    # ------------------------------------------------------------------ build

    def _build_ui(self) -> None:
        self.player       = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(1.0)

        # ---- Graphics scene / view / video item ----
        self._scene = _OverlayScene(self)
        self._scene.setSceneRect(0, 0, 1280, 720)

        self._view = QGraphicsView(self._scene)
        self._view.setRenderHint(QPainter.Antialiasing)
        self._view.setRenderHint(QPainter.SmoothPixmapTransform)
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._view.setBackgroundBrush(QBrush(QColor(0, 0, 0)))
        self._view.setFocusPolicy(Qt.StrongFocus)

        self._video_item = QGraphicsVideoItem()
        self._video_item.setSize(QSizeF(1280, 720))
        self._scene.addItem(self._video_item)
        self.player.setVideoOutput(self._video_item)

        # ---- Playback controls ----
        self.position_slider = QSlider(Qt.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_slider.setSingleStep(1000)

        self.time_label = QLabel("00:00:00 / 00:00:00")
        self.time_label.setAlignment(Qt.AlignCenter)

        self.play_btn  = QPushButton("▶  Play")
        self.pause_btn = QPushButton("⏸  Pause")
        self.stop_btn  = QPushButton("⏹  Stop")

        # ---- Overlay controls ----
        self.add_image_btn    = QPushButton("🖼  Add Image")
        self.remove_image_btn = QPushButton("✕  Remove")
        self.add_image_btn.setToolTip(
            "Add an image overlay on top of the video.\n"
            "Drag to move, drag corner handles to resize."
        )
        self.remove_image_btn.setToolTip(
            "Remove the selected overlay image  (or press Delete)"
        )

        ctrl = QHBoxLayout()
        ctrl.addWidget(self.play_btn)
        ctrl.addWidget(self.pause_btn)
        ctrl.addWidget(self.stop_btn)
        ctrl.addSpacing(16)
        ctrl.addWidget(self.add_image_btn)
        ctrl.addWidget(self.remove_image_btn)
        ctrl.addStretch()
        ctrl.addWidget(self.time_label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view, stretch=1)
        layout.addWidget(self.position_slider)
        layout.addLayout(ctrl)

    def _connect_signals(self) -> None:
        self.play_btn.clicked.connect(self.player.play)
        self.pause_btn.clicked.connect(self.player.pause)
        self.stop_btn.clicked.connect(self.player.stop)
        self.add_image_btn.clicked.connect(self._on_add_image)
        self.remove_image_btn.clicked.connect(self._on_remove_selected)

        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.position_slider.sliderMoved.connect(self.player.setPosition)

        self._video_item.nativeSizeChanged.connect(self._on_video_size_changed)

    # ------------------------------------------------------------------ resize

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_view()

    def _fit_view(self) -> None:
        self._view.fitInView(self._video_item, Qt.KeepAspectRatio)

    # ------------------------------------------------------------------ public API

    def load(self, path: str) -> None:
        """Load and immediately preview (but don't autoplay) a video file."""
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.pause()  # show first frame

    def seek_to(self, seconds: float) -> None:
        """Jump to a position in seconds."""
        self.player.setPosition(int(seconds * 1000))

    def get_overlays(self) -> List[ImageOverlay]:
        """Return all current overlay items as normalised ImageOverlay objects."""
        sw = self._video_size.width()
        sh = self._video_size.height()
        if sw <= 0 or sh <= 0:
            return []
        overlays: list[ImageOverlay] = []
        for item in self._scene.items():
            if isinstance(item, _OverlayItem):
                pos  = item.pos()
                rect = item.local_rect()
                overlays.append(ImageOverlay(
                    image_path=item.image_path,
                    x_pct=pos.x()          / sw,
                    y_pct=pos.y()          / sh,
                    w_pct=rect.width()     / sw,
                    h_pct=rect.height()    / sh,
                ))
        return overlays

    # ------------------------------------------------------------------ overlay slots

    @Slot()
    def _on_add_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Overlay Image", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp);;All Files (*)",
        )
        if not path:
            return
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return

        sw = self._video_size.width()
        sh = self._video_size.height()
        # Default width = 20 % of video width; height preserves the image aspect.
        w = sw * 0.20
        h = (w * pixmap.height() / pixmap.width()) if pixmap.width() > 0 else sh * 0.12

        item = _OverlayItem(pixmap, w, h, path)
        item.setPos(sw * 0.05, sh * 0.05)
        self._scene.addItem(item)
        self._scene.clearSelection()
        item.setSelected(True)
        self._view.setFocus()

    @Slot()
    def _on_remove_selected(self) -> None:
        for item in list(self._scene.selectedItems()):
            if isinstance(item, _OverlayItem):
                self._scene.removeItem(item)

    # ------------------------------------------------------------------ player slots

    @Slot(QSizeF)
    def _on_video_size_changed(self, size: QSizeF) -> None:
        if size.width() > 0 and size.height() > 0:
            self._video_size = size
            self._video_item.setSize(size)
            self._scene.setSceneRect(0, 0, size.width(), size.height())
            self._fit_view()

    @Slot(int)
    def _on_position_changed(self, pos_ms: int) -> None:
        if not self.position_slider.isSliderDown():
            self.position_slider.setValue(pos_ms)
        dur = self.player.duration()
        self.time_label.setText(f"{_ms_to_hms(pos_ms)} / {_ms_to_hms(dur)}")

    @Slot(int)
    def _on_duration_changed(self, dur_ms: int) -> None:
        self.position_slider.setRange(0, dur_ms)
