"""Application lifecycle helpers kept separate from the GUI layout."""

import ctypes
import sys


def install_graceful_close(app) -> None:
    """Route window close through TranslationJob.stop() and wait for cleanup.

    The translation worker is intentionally left responsible for cache saving and
    PackWriter.close()/abort(). The Tk window stays alive until ``app._job`` is
    cleared by the worker's existing ``finally`` block, so closing the window
    cannot bypass partial-archive cleanup.
    """
    closing = False

    def finish_close_when_idle() -> None:
        if getattr(app, "_job", None) is not None:
            app.after(50, finish_close_when_idle)
            return
        app.destroy()

    def on_close() -> None:
        nonlocal closing
        if closing:
            return
        closing = True

        active_job = getattr(app, "_job", None)
        if active_job is not None:
            active_job.stop()
        else:
            app.job_state.stop()

        try:
            app.set_status("🛑 Завершение работы...", None)
        except Exception:
            pass
        finish_close_when_idle()

    app.protocol("WM_DELETE_WINDOW", on_close)


def run() -> None:
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "MineAI.Translator"
            )
        except Exception:
            pass

    from mineai.gui.app import TranslatorApp

    app = TranslatorApp()
    install_graceful_close(app)
    app.mainloop()
