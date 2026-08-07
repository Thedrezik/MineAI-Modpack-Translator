import os
import queue
import sys
import tempfile
import threading
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
        try:
            os.chdir(_import_dir)
            from mineai.gui import app as gui_app
        finally:
            os.chdir(_original_cwd)
finally:
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
        state = SimpleNamespace(is_running=False, is_paused=False)

        def start() -> None:
            state.is_running = True
            state.is_paused = False

        def finish() -> None:
            state.is_running = False
            state.is_paused = False

        def toggle_pause() -> bool:
            if not state.is_running:
                return False
            state.is_paused = not state.is_paused
            return state.is_paused

        state.start = mock.Mock(side_effect=start)
        state.finish = mock.Mock(side_effect=finish)
        state.stop = mock.Mock(side_effect=finish)
        state.toggle_pause = mock.Mock(side_effect=toggle_pause)
        app.job_state = state
        app._ui_thread_id = threading.get_ident()
        app._ui_queue = queue.Queue()
        app._lock_ui = mock.Mock()
        app._clear_log = mock.Mock()
        app._translation_options = mock.Mock(return_value=object())
        app.btn_settings = mock.Mock()
        app.btn_analyze = mock.Mock()
        app.btn_start = mock.Mock()
        app.btn_pause = mock.Mock()
        app.btn_stop = mock.Mock()
        app.log = mock.Mock()
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
        active_job.stop.assert_called_once_with()
        app.job_state.stop.assert_not_called()
        self.assertTrue(created_threads[0].started)

        created_threads[0].target()

        active_job.run_translation.assert_called_once_with(
            app._translation_options.return_value
        )
        self.assertIsNone(app._job)
        app.job_state.finish.assert_called_once_with()
        app._lock_ui.assert_called_with(False)

    def test_analysis_exception_is_logged_and_worker_is_cleaned_up(self) -> None:
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

        created_threads[0].target()

        self.assertIsNone(app._job)
        app.job_state.finish.assert_called_once_with()
        app._lock_ui.assert_called_with(False)
        app.set_status.assert_called_with("❌ Ошибка анализа", None)
        self.assertTrue(
            any("analysis failed" in call.args[0] for call in app.log.call_args_list)
        )

    def test_translation_exception_is_logged_and_worker_is_cleaned_up(self) -> None:
        app = self._bare_app()
        active_job = mock.Mock()
        active_job.run_translation.side_effect = RuntimeError("translation failed")
        app._job = active_job
        options = object()

        app._run_translation_thread(options)

        self.assertIsNone(app._job)
        app.job_state.finish.assert_called_once_with()
        app._lock_ui.assert_called_with(False)
        app.set_status.assert_called_with("❌ Ошибка перевода", None)
        self.assertTrue(
            any("translation failed" in call.args[0] for call in app.log.call_args_list)
        )

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

    def test_callback_failure_does_not_stop_queue_draining(self) -> None:
        app = object.__new__(gui_app.TranslatorApp)
        app._ui_queue = queue.Queue()
        app.after = mock.Mock()
        app.log = mock.Mock()
        completed = mock.Mock()
        app._ui_queue.put((mock.Mock(side_effect=RuntimeError("callback failed")), ()))
        app._ui_queue.put((completed, ("done",)))

        gui_app.TranslatorApp._drain_ui_queue(app)

        completed.assert_called_once_with("done")
        app.after.assert_called_once_with(50, app._drain_ui_queue)
        self.assertTrue(
            any("callback failed" in call.args[0] for call in app.log.call_args_list)
        )

    def test_lock_ui_also_locks_settings_button(self) -> None:
        app = self._bare_app()
        app._lock_ui = gui_app.TranslatorApp._lock_ui.__get__(
            app,
            gui_app.TranslatorApp,
        )

        app._lock_ui(True)
        app._lock_ui(False)

        self.assertEqual(
            app.btn_settings.configure.call_args_list,
            [mock.call(state="disabled"), mock.call(state="normal")],
        )

    def test_open_log_file_uses_cross_platform_opener(self) -> None:
        app = self._bare_app()
        with tempfile.TemporaryDirectory() as temp_dir:
            previous_cwd = os.getcwd()
            os.chdir(temp_dir)
            try:
                with open("mineai_log.txt", "w", encoding="utf-8") as log_file:
                    log_file.write("test")
                with (
                    mock.patch.object(gui_app.sys, "platform", "linux"),
                    mock.patch.object(gui_app.subprocess, "Popen") as popen,
                ):
                    app._open_log_file()
            finally:
                os.chdir(previous_cwd)

        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0][0], "xdg-open")
        app.log.assert_not_called()

    def test_migration_window_polls_completion_on_ui_thread(self) -> None:
        migration_module = sys.modules[gui_app.MigrationWindow.__module__]
        window = object.__new__(gui_app.MigrationWindow)
        window.ent_zip = SimpleNamespace(get=lambda: "/tmp/translations.zip")
        window.btn_run = mock.Mock()
        window.var_cache = SimpleNamespace(get=lambda: "ai")
        window.mc_dir = "/tmp/modpack"
        window.lang_api_code = "ru"
        window.log_callback = mock.Mock()
        window.cache_ai = mock.Mock()
        window.cache_std = mock.Mock()
        window.after = mock.Mock()
        window.destroy = mock.Mock()
        created_threads = []

        def make_thread(*, target, daemon):
            thread = _DeferredThread(target=target, daemon=daemon)
            created_threads.append(thread)
            return thread

        with (
            mock.patch.object(migration_module.os.path, "exists", return_value=True),
            mock.patch.object(migration_module, "run_migration", return_value=0),
            mock.patch.object(migration_module.threading, "Thread", side_effect=make_thread),
        ):
            window._run()

        self.assertTrue(created_threads[0].started)
        self.assertFalse(created_threads[0].daemon)
        window.destroy.assert_not_called()
        self.assertEqual(window.after.call_count, 1)
        delay, poll_finished = window.after.call_args.args
        self.assertEqual(delay, 50)

        created_threads[0].target()

        # The worker only flips a threading.Event; it must not touch Tk.
        self.assertEqual(window.after.call_count, 1)
        window.destroy.assert_not_called()

        poll_finished()
        window.destroy.assert_called_once_with()
        self.assertEqual(window.after.call_count, 1)


if __name__ == "__main__":
    unittest.main()
