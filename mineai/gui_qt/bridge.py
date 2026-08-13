"""Thread-safe signal bridge between the existing runtime and Qt widgets."""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal


class RuntimeSignals(QObject):
    log = pyqtSignal(str, str)
    status = pyqtSignal(str, object)
    row = pyqtSignal(str, str, str, int, int, int)
    analysis_item = pyqtSignal(object)
    worker_finished = pyqtSignal(str)
    worker_failed = pyqtSignal(str, str)


class MigrationSignals(QObject):
    finished = pyqtSignal(int, object, str)


class LmStudioSignals(QObject):
    finished = pyqtSignal(bool, object, str)
