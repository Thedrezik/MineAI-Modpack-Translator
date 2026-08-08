"""Experimental PyQt6 presentation layer for MineAI Translator.

Imports stay lazy so the existing CustomTkinter application and unit tests do
not require the optional Qt dependency unless this UI is explicitly launched.
"""


def run() -> int:
    from mineai.gui_qt.main_window import run as _run
    return _run()


def __getattr__(name: str):
    if name == "TranslatorQtWindow":
        from mineai.gui_qt.main_window import TranslatorQtWindow
        return TranslatorQtWindow
    raise AttributeError(name)


__all__ = ["TranslatorQtWindow", "run"]
