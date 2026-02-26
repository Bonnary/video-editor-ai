---
name: pyside6-qt-python
description: 
  "Practical guidelines and recipes for PySide6 (Qt for Python 6) in a desktop video editor application. Covers widgets, layouts, signals & slots, QThread for background processing, QMediaPlayer for video playback, and file dialogs. Triggers when writing or reviewing code that imports from PySide6, creates QWidget/QMainWindow subclasses, uses Qt signals/slots, or runs background tasks with QThread."
license: MIT
metadata:
  author: project-skill
  version: "1.0.0"
---

# PySide6 (Qt for Python 6) Skill

Official Python bindings for Qt 6 provided by The Qt Company.

- **Package:** `pyside6` (`pip install pyside6`)
- **API Reference:** https://doc.qt.io/qtforpython-6/api.html
- **Tutorials:** https://doc.qt.io/qtforpython-6/tutorials/index.html
- **Examples:** https://doc.qt.io/qtforpython-6/examples/index.html
- **Porting from PySide2:** https://doc.qt.io/qtforpython-6/faq/porting_from2.html

---

## When to Apply

Use this skill when:
- Creating or modifying Qt widgets, windows, or dialogs
- Connecting signals to slots or defining custom signals
- Running ffmpeg or other heavy work off the main thread (QThread)
- Implementing video playback with `QMediaPlayer`
- Designing layouts (grid, vertical, horizontal, splitters)
- Opening/saving files via `QFileDialog`

---

## Core Application Skeleton

```python
import sys
from PySide6.QtWidgets import QApplication, QMainWindow

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Video Editor")
        self.resize(1280, 720)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
```

- **One `QApplication` per process** — create before any widgets.
- `app.exec()` starts the event loop; `sys.exit()` forwards the exit code.

---

## Widgets

### Common Widgets

| Widget | Module | Purpose |
|---|---|---|
| `QMainWindow` | `QtWidgets` | Top-level window with menu/toolbar/statusbar |
| `QWidget` | `QtWidgets` | Base class for all UI elements |
| `QPushButton` | `QtWidgets` | Clickable button |
| `QLabel` | `QtWidgets` | Static text or image |
| `QLineEdit` | `QtWidgets` | Single-line text input |
| `QSlider` | `QtWidgets` | Slider (e.g. timeline scrubber) |
| `QProgressBar` | `QtWidgets` | Progress indicator |
| `QComboBox` | `QtWidgets` | Dropdown selector |
| `QListWidget` | `QtWidgets` | Scrollable list of items |
| `QSplitter` | `QtWidgets` | Resizable split panes |
| `QScrollArea` | `QtWidgets` | Scrollable container |
| `QVideoWidget` | `QtMultimediaWidgets` | Video output surface |
| `QGraphicsView` | `QtWidgets` | Custom scene rendering (timeline canvas) |

### Setting Up a Central Widget

```python
from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        # add child widgets to layout
```

---

## Layouts

### Layout Types

| Layout | Description |
|---|---|
| `QVBoxLayout` | Stack widgets vertically |
| `QHBoxLayout` | Stack widgets horizontally |
| `QGridLayout` | Place widgets in a grid by row/col |
| `QFormLayout` | Label + field pairs |
| `QStackedLayout` | Show one widget at a time (like tabs) |

```python
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QPushButton, QLabel
)
from PySide6.QtCore import Qt

# Horizontal split: timeline left, properties right
splitter = QSplitter(Qt.Horizontal)
splitter.addWidget(timeline_widget)
splitter.addWidget(properties_widget)
splitter.setStretchFactor(0, 3)   # timeline gets 3x space
splitter.setStretchFactor(1, 1)

# Nested layouts
toolbar = QHBoxLayout()
toolbar.addWidget(QPushButton("Play"))
toolbar.addWidget(QPushButton("Pause"))
toolbar.addStretch()              # push remaining buttons to right

main_layout = QVBoxLayout()
main_layout.addLayout(toolbar)
main_layout.addWidget(splitter)
```

---

## Signals & Slots

Signals decouple UI events from logic. Connect them with `.connect()`.

### Built-in Signal Examples

```python
btn = QPushButton("Export")
btn.clicked.connect(self.on_export)          # no args

slider = QSlider(Qt.Horizontal)
slider.valueChanged.connect(self.on_seek)    # int arg

combo = QComboBox()
combo.currentTextChanged.connect(self.on_format_changed)  # str arg
```

### Custom Signals

```python
from PySide6.QtCore import QObject, Signal

class VideoProcessor(QObject):
    progress_changed = Signal(int)    # emits an int (0-100)
    finished         = Signal(str)    # emits output file path
    error_occurred   = Signal(str)    # emits error message

    def process(self):
        # ... do work ...
        self.progress_changed.emit(50)
        self.finished.emit('output.mp4')
```

### Connecting Signals to Slots

```python
processor = VideoProcessor()
processor.progress_changed.connect(progress_bar.setValue)   # direct binding
processor.finished.connect(lambda path: self.show_done(path))
processor.error_occurred.connect(self.show_error)

# Disconnect when done
processor.progress_changed.disconnect(progress_bar.setValue)
```

---

## Background Threads (QThread)

**Never run ffmpeg or other blocking code on the main thread** — it freezes the UI.

### Pattern: Worker + QThread

```python
from PySide6.QtCore import QObject, QThread, Signal
import ffmpeg

class FfmpegWorker(QObject):
    progress = Signal(int)        # 0-100
    finished = Signal()
    error    = Signal(str)

    def __init__(self, input_path: str, output_path: str):
        super().__init__()
        self._input  = input_path
        self._output = output_path

    def run(self):
        try:
            import re, subprocess
            process = (
                ffmpeg
                .input(self._input)
                .output(self._output, vcodec='libx264')
                .overwrite_output()
                .run_async(pipe_stderr=True)
            )
            # Parse ffmpeg stderr to emit progress
            duration = self._get_duration()
            for line in process.stderr:
                m = re.search(r'time=(\d+):(\d+):(\d+\.\d+)', line.decode('utf-8', errors='ignore'))
                if m and duration:
                    elapsed = int(m.group(1))*3600 + int(m.group(2))*60 + float(m.group(3))
                    self.progress.emit(int(elapsed / duration * 100))
            process.wait()
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))

    def _get_duration(self) -> float:
        info = ffmpeg.probe(self._input)
        return float(info['format'].get('duration', 0))


class ExportController:
    def start_export(self, input_path, output_path):
        self.thread = QThread()
        self.worker = FfmpegWorker(input_path, output_path)
        self.worker.moveToThread(self.thread)

        # Wire up signals
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.worker.progress.connect(self.progress_bar.setValue)
        self.worker.error.connect(self.show_error)

        self.thread.start()
```

> **Key rules:**
> - Call `worker.moveToThread(thread)` before starting.
> - Connect `thread.started` to the worker's `run` slot.
> - Always clean up with `deleteLater` to avoid memory leaks.
> - Do NOT access Qt widgets from the worker thread — use signals only.

---

## Video Playback (QMediaPlayer)

```python
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtCore import QUrl

class VideoPlayer(QWidget):
    def __init__(self):
        super().__init__()
        self.player = QMediaPlayer()
        self.audio  = QAudioOutput()
        self.player.setAudioOutput(self.audio)

        self.video_widget = QVideoWidget()
        self.player.setVideoOutput(self.video_widget)

        layout = QVBoxLayout(self)
        layout.addWidget(self.video_widget)

        # Controls
        controls = QHBoxLayout()
        self.play_btn  = QPushButton("Play")
        self.pause_btn = QPushButton("Pause")
        self.slider    = QSlider(Qt.Horizontal)
        controls.addWidget(self.play_btn)
        controls.addWidget(self.pause_btn)
        controls.addWidget(self.slider)
        layout.addLayout(controls)

        # Connect
        self.play_btn.clicked.connect(self.player.play)
        self.pause_btn.clicked.connect(self.player.pause)
        self.player.positionChanged.connect(self.on_position_changed)
        self.player.durationChanged.connect(self.on_duration_changed)
        self.slider.sliderMoved.connect(self.player.setPosition)

    def load(self, path: str):
        self.player.setSource(QUrl.fromLocalFile(path))

    def on_position_changed(self, pos_ms: int):
        self.slider.setValue(pos_ms)

    def on_duration_changed(self, duration_ms: int):
        self.slider.setRange(0, duration_ms)
```

### Key QMediaPlayer Signals

| Signal | Args | Description |
|---|---|---|
| `positionChanged` | `int` (ms) | Current playback position |
| `durationChanged` | `int` (ms) | Total media duration |
| `playbackStateChanged` | `QMediaPlayer.PlaybackState` | Playing / Paused / Stopped |
| `errorOccurred` | `error`, `errorString` | Playback error |
| `mediaStatusChanged` | `QMediaPlayer.MediaStatus` | Loaded / Buffering / EndOfMedia |

---

## File Dialogs

```python
from PySide6.QtWidgets import QFileDialog

# Open a video file
path, _ = QFileDialog.getOpenFileName(
    self,
    "Open Video",
    "",                                              # initial dir
    "Video Files (*.mp4 *.avi *.mkv *.mov);;All Files (*)"
)
if path:
    self.load_video(path)

# Save output file
out_path, _ = QFileDialog.getSaveFileName(
    self,
    "Save As",
    "output.mp4",
    "MP4 Video (*.mp4);;All Files (*)"
)

# Open a directory
folder = QFileDialog.getExistingDirectory(self, "Select Folder")
```

---

## Menu Bar & Toolbar

```python
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QMenuBar, QToolBar

class MainWindow(QMainWindow):
    def _setup_menu(self):
        menu = self.menuBar()

        file_menu = menu.addMenu("&File")
        open_action = QAction("&Open…", self)
        open_action.setShortcut(QKeySequence.Open)        # Ctrl+O
        open_action.triggered.connect(self.open_file)
        file_menu.addAction(open_action)

        file_menu.addSeparator()

        export_action = QAction("&Export…", self)
        export_action.setShortcut(QKeySequence("Ctrl+E"))
        export_action.triggered.connect(self.export)
        file_menu.addAction(export_action)

    def _setup_toolbar(self):
        toolbar = QToolBar("Main Toolbar")
        self.addToolBar(toolbar)
        toolbar.addAction("Play",  self.play)
        toolbar.addAction("Pause", self.pause)
        toolbar.addAction("Export", self.export)
```

---

## Status Bar & Progress

```python
# In QMainWindow
self.statusBar().showMessage("Ready")
self.statusBar().showMessage("Exporting…", 3000)   # 3s timeout

# Persistent widget in status bar
self.progress = QProgressBar()
self.progress.setRange(0, 100)
self.statusBar().addPermanentWidget(self.progress)
```

---

## Common Patterns for a Video Editor

### Disable UI during export

```python
def start_export(self):
    self.export_btn.setEnabled(False)
    self.open_btn.setEnabled(False)
    # ... start QThread worker ...

def on_export_finished(self):
    self.export_btn.setEnabled(True)
    self.open_btn.setEnabled(True)
    self.statusBar().showMessage("Export complete!")
```

### Show error dialog

```python
from PySide6.QtWidgets import QMessageBox

def show_error(self, message: str):
    QMessageBox.critical(self, "Error", message)
```

### Confirm before closing

```python
def closeEvent(self, event):
    reply = QMessageBox.question(
        self, "Quit", "Are you sure you want to quit?",
        QMessageBox.Yes | QMessageBox.No
    )
    if reply == QMessageBox.Yes:
        event.accept()
    else:
        event.ignore()
```

---

## Common Pitfalls

| Issue | Solution |
|---|---|
| UI freezes during ffmpeg | Move ffmpeg to a `QThread` worker |
| Crash when accessing widget from thread | Only update widgets via signals |
| `QMediaPlayer` shows no video | Must set `QVideoWidget` as output _before_ loading media |
| Worker runs on main thread | Call `moveToThread()` before connecting `started` |
| Memory leak with QThread | Connect `finished` to `deleteLater` on both worker and thread |
| `QApplication` must exist first | Instantiate `QApplication` before any `QWidget` |
| PyInstaller missing Qt plugins | Add `--collect-all PySide6` to PyInstaller args |

---

## Module Import Reference

```python
# Widgets
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QPushButton, QLabel, QLineEdit, QSlider, QProgressBar,
    QComboBox, QListWidget, QSplitter, QScrollArea,
    QVBoxLayout, QHBoxLayout, QGridLayout,
    QFileDialog, QMessageBox, QToolBar,
)
# Core (signals, threads, timers, URLs)
from PySide6.QtCore import (
    Qt, QObject, QThread, Signal, Slot,
    QTimer, QUrl, QSize,
)
# GUI (actions, icons, key sequences)
from PySide6.QtGui import QAction, QKeySequence, QIcon, QPixmap

# Multimedia
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
```

---

## References

- **API Reference:** https://doc.qt.io/qtforpython-6/api.html
- **Getting Started:** https://doc.qt.io/qtforpython-6/gettingstarted.html
- **Tutorials:** https://doc.qt.io/qtforpython-6/tutorials/index.html
- **Examples:** https://doc.qt.io/qtforpython-6/examples/index.html
- **QMediaPlayer:** https://doc.qt.io/qtforpython-6/PySide6/QtMultimedia/QMediaPlayer.html
- **QThread:** https://doc.qt.io/qtforpython-6/PySide6/QtCore/QThread.html
- **QFileDialog:** https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QFileDialog.html
- **Signals & Slots:** https://doc.qt.io/qtforpython-6/tutorials/basictutorial/signals_and_slots.html
- **Deployment (PyInstaller):** https://doc.qt.io/qtforpython-6/deployment/index.html
