import sys
import types
import unittest
from unittest import mock

_customtkinter = types.ModuleType("customtkinter")
_customtkinter.CTkToplevel = object
_customtkinter.CTkEntry = object
_tkinter = types.ModuleType("tkinter")
_tkinter.filedialog = types.SimpleNamespace()
_tkinter.messagebox = types.SimpleNamespace()
_previous = {name: sys.modules.get(name) for name in ("customtkinter", "tkinter")}
sys.modules["customtkinter"] = _customtkinter
sys.modules["tkinter"] = _tkinter
try:
    from mineai.gui import settings as gui_settings
finally:
    for name, previous in _previous.items():
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous


class PromptEditorStateTests(unittest.TestCase):
    @staticmethod
    def _editor(dirty: bool = True):
        editor = object.__new__(gui_settings.PromptEditorWindow)
        editor._dirty = dirty
        editor.destroy = mock.Mock()
        editor._save = mock.Mock()
        return editor

    def test_clean_close_destroys_immediately(self) -> None:
        editor = self._editor(dirty=False)
        editor._request_close()
        editor.destroy.assert_called_once_with()
        editor._save.assert_not_called()

    def test_dirty_close_cancel_keeps_editor_open(self) -> None:
        editor = self._editor()
        with mock.patch.object(gui_settings.messagebox, "askyesnocancel", return_value=None, create=True):
            editor._request_close()
        editor.destroy.assert_not_called()
        editor._save.assert_not_called()

    def test_dirty_close_save_persists_before_destroy(self) -> None:
        editor = self._editor()
        with mock.patch.object(gui_settings.messagebox, "askyesnocancel", return_value=True, create=True):
            editor._request_close()
        editor._save.assert_called_once_with(close=False)
        editor.destroy.assert_called_once_with()

    def test_dirty_close_discard_destroys_without_save(self) -> None:
        editor = self._editor()
        with mock.patch.object(gui_settings.messagebox, "askyesnocancel", return_value=False, create=True):
            editor._request_close()
        editor._save.assert_not_called()
        editor.destroy.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
