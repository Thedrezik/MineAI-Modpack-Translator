import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from mineai.runtime.qt_runtime import configure_qt_runtime


class QtRuntimeTests(unittest.TestCase):
    def test_configure_qt_runtime_prioritizes_bundled_qt_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            qt_bin = Path(directory) / "PyQt6" / "Qt6" / "bin"
            qt_bin.mkdir(parents=True)
            original_path = os.environ.get("PATH", "")

            try:
                with mock.patch.dict(os.environ, {"PATH": "external-qt"}, clear=False):
                    with mock.patch(
                        "mineai.runtime.qt_runtime.os.add_dll_directory",
                        return_value=object(),
                        create=True,
                    ) as add_dll_directory:
                        with mock.patch(
                            "mineai.runtime.qt_runtime.ctypes.WinDLL",
                            create=True,
                        ) as win_dll:
                            with mock.patch(
                                "mineai.runtime.qt_runtime.sys.platform", "win32"
                            ):
                                configure_qt_runtime(directory)

                        add_dll_directory.assert_called_once_with(str(qt_bin))
                        win_dll.assert_called_once_with("kernel32", use_last_error=True)
                        self.assertEqual(
                            os.environ["PATH"].split(os.pathsep)[0], str(qt_bin)
                        )
            finally:
                os.environ["PATH"] = original_path

    def test_release_specs_install_the_qt_runtime_hook(self):
        project_root = Path(__file__).resolve().parents[1]
        for version in ("41", "42", "43", "44", "45"):
            spec = (project_root / f"MineAI_Translator_Beta{version}.spec").read_text(
                encoding="utf-8-sig"
            )
            self.assertIn("runtime_hooks=['qt_runtime_hook.py']", spec)

    def test_qt_gui_prepares_runtime_before_loading_window(self):
        import mineai.gui_qt as gui_qt

        fake_main_window = types.ModuleType("mineai.gui_qt.main_window")
        fake_main_window.run = mock.Mock(return_value=0)
        with mock.patch.object(gui_qt, "_prepare_qt_runtime") as prepare:
            with mock.patch.dict(
                sys.modules, {"mineai.gui_qt.main_window": fake_main_window}
            ):
                self.assertEqual(gui_qt.run(), 0)

        prepare.assert_called_once_with()
        fake_main_window.run.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
