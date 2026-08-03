import os
import queue
import sys
import tempfile
import types
import unittest
from types import SimpleNamespace
from unittest import mock


# Keep these lifecycle tests headless and independent of the optional GUI
# dependency. Only the base classes are needed while importing the module.
_original_cwd = os.getcwd()
_customtkinter = types.ModuleType("customtkinter")
_customtkinter.CTk = object
_customtkinter.CTkToplevel = object
_tkinter = types.ModuleType("tkinter")
_tkinter.TclError = RuntimeError
_tkinter.filedialog = types.SimpleNamespace()
_tkinter.messagebox = types.SimpleNamespace()
_gui_settings = types.ModuleType("mineai.gui.settings")
_gui_settings.SettingsWindow = object
_previous_modules = {
    name: sys.modules.get(name)
    for name in ("customtkinter", "tkinter", "mineai.gui.settings")
}
sys.modules["customtkinter"] = _customtkinter
sys.modules["tkinter"] = _tkinter
sys.modules["mineai.gui.settings"] = _gui_settings
try:
    with tempfile.TemporaryDirectory() as _import_dir:
        os.chdir(_import_dir)
        from mineai.gui import app as gui_app
finally:
    os.chdir(_original_cwd)
    for name, previous in _previous_modules.items():
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous


class _DeferredThread:
    def __init__(self, *, target, daemon):
        self.target = target
        self.daemon = daemon
        self.started = False

    def start(self) -> None:
        self.started = True


class TranslatorAppJobLifecycleTests(unittest.TestCase):
    @staticmethod
    def _bare_app():
        app = object.__new__(gui_app.TranslatorApp)
        app._job = None
        app.job_state = SimpleNamespace(
            is_running=False,
            is_paused=False,
            stop=mock.Mock(),
        )
        app._lock_ui = mock.Mock()
        app._clear_log = mock.Mock()
        app._translation_options = mock.Mock(return_value=object())
        app.btn_pause = mock.Mock()
        app.btn_stop = mock.Mock()
        app.set_status = mock.Mock()
        return app

    def test_stop_targets_the_retained_translation_job(self) -> None:
        app = self._bare_app()
        active_job = mock.Mock()
        app._job_instance = mock.Mock(return_value=active_job)
        app.var_engine = SimpleNamespace(get=lambda: "google")

        config = mock.Mock()
        config.get.return_value = "/tmp/modpack"
        created_threads = []

        def make_thread(*, target, daemon):
            thread = _DeferredThread(target=target, daemon=daemon)
            created_threads.append(thread)
            return thread

        with (
            mock.patch.object(gui_app, "settings", config),
            mock.patch.object(gui_app.threading, "Thread", side_effect=make_thread),
        ):
            app._start_translation()
            app._stop()

        self.assertIs(app._job, active_job)
        app._job_instance.assert_called_once_with()
        active_job.stop.assert_called_once_with()
        app.job_state.stop.assert_not_called()
        self.assertEqual(len(created_threads), 1)
        self.assertTrue(created_threads[0].started)

        created_threads[0].target()

        active_job.run_translation.assert_called_once_with(
            app._translation_options.return_value
        )
        self.assertIsNone(app._job)
        self.assertFalse(app.job_state.is_running)
        app._lock_ui.assert_called_with(False)

    def test_analysis_retains_job_and_clears_it_after_failure(self) -> None:
        app = self._bare_app()
        active_job = mock.Mock()
        active_job.run_analysis.side_effect = RuntimeError("analysis failed")
        app._job_instance = mock.Mock(return_value=active_job)
        created_threads = []

        def make_thread(*, target, daemon):
            thread = _DeferredThread(target=target, daemon=daemon)
            created_threads.append(thread)
            return thread

        with mock.patch.object(
            gui_app.threading,
            "Thread",
            side_effect=make_thread,
        ):
            app._start_analysis()

        self.assertIs(app._job, active_job)
        app._job_instance.assert_called_once_with()
        self.assertEqual(len(created_threads), 1)
        self.assertTrue(created_threads[0].started)

        with self.assertRaisesRegex(RuntimeError, "analysis failed"):
            created_threads[0].target()

        self.assertIsNone(app._job)
        self.assertFalse(app.job_state.is_running)
        app._lock_ui.assert_called_with(False)

    def test_stop_falls_back_to_shared_state_without_active_job(self) -> None:
        app = self._bare_app()

        app._stop()

        app.job_state.stop.assert_called_once_with()
        app.btn_stop.configure.assert_called_once_with(state="disabled")
        app.btn_pause.configure.assert_called_once_with(state="disabled")

    def test_worker_ui_update_is_queued_without_calling_tk(self) -> None:
        app = object.__new__(gui_app.TranslatorApp)
        app._ui_thread_id = -1
        app._ui_queue = queue.Queue()
        app.after = mock.Mock()

        gui_app.TranslatorApp.set_status(app, "Working", 0.5)

        callback, args = app._ui_queue.get_nowait()
        self.assertEqual(callback, app.set_status)
        self.assertEqual(args, ("Working", 0.5))
        app.after.assert_not_called()


if __name__ == "__main__":
    unittest.main()
