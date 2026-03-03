"""Entry point — Khmer AI Video Dubber."""
import logging
import sys
import os
from logging.handlers import RotatingFileHandler

# Make sure the workspace root is on sys.path so `app` is importable
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Ensure CUDA libraries are discoverable by PyTorch before any torch import.
# On Arch/Manjaro the CUDA toolkit installs to /opt/cuda which is not always
# in LD_LIBRARY_PATH by default, causing "libcublas.so not found" errors.
_CUDA_LIB_DIRS = [
    "/opt/cuda/lib64",
    "/opt/cuda/targets/x86_64-linux/lib",
    "/usr/local/cuda/lib64",
]
_existing_cuda_dirs = [p for p in _CUDA_LIB_DIRS if os.path.isdir(p)]
if _existing_cuda_dirs:
    _current_ld = os.environ.get("LD_LIBRARY_PATH", "")
    _current_parts = set(_current_ld.split(":")) if _current_ld else set()
    _to_add = [p for p in _existing_cuda_dirs if p not in _current_parts]
    if _to_add:
        os.environ["LD_LIBRARY_PATH"] = ":".join(_to_add) + (":" + _current_ld if _current_ld else "")

# ---- log file next to the executable (or workspace root in dev) ----
_LOG_FILE = os.path.join(ROOT, "app.log")

_handlers: list[logging.Handler] = [
    logging.StreamHandler(sys.stdout),
    # Rotate at 5 MB, keep 3 backups → prevents the log from growing unbounded
    RotatingFileHandler(_LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"),
]

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=_handlers,
)

# Attach the Qt signal handler so the log viewer dialog can receive records.
from app.widgets.log_viewer import get_qt_log_handler as _get_qt_log_handler  # noqa: E402
logging.getLogger().addHandler(_get_qt_log_handler())

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from app.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Khmer AI Video Dubber")
    app.setOrganizationName("kh-dubber")

    # Set application-wide icon (window + taskbar)
    logo_path = os.path.join(ROOT, "app", "images", "logo.jpg")
    if os.path.exists(logo_path):
        app.setWindowIcon(QIcon(logo_path))

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
