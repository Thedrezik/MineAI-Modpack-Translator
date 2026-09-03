"""PyInstaller hook executed before the PyQt6 presentation layer loads."""

import sys

from mineai.runtime.qt_runtime import configure_qt_runtime


configure_qt_runtime(getattr(sys, "_MEIPASS", ""))
