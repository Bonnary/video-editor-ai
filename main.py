"""Entry point — Khmer AI Video Dubber."""
import logging
import sys
import os

# Make sure the workspace root is on sys.path so `app` is importable
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ---- log file next to the executable (or workspace root in dev) ----
_LOG_FILE = os.path.join(ROOT, "app.log")

_handlers: list[logging.Handler] = [
    logging.StreamHandler(sys.stdout),
    logging.FileHandler(_LOG_FILE, encoding="utf-8"),
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
