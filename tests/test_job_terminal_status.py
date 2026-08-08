import unittest
from unittest import mock

from mineai.runtime.job import TranslationJob, TranslationOptions


class TerminalStopStatusTests(unittest.TestCase):
    @staticmethod
    def _options() -> TranslationOptions:
        return TranslationOptions(
            mc_dir="C:/Minecraft",
            language_label="Русский",
            mc_version="1.20.1",
            output_mode="inplace",
            pack_name="MineAI_Pack",
            engine="google",
            google_mode="single",
            ai_mode="safe",
            ai_batch=20,
            ai_provider="local",
            process_mode="append",
            translate_mods=False,
            translate_books=False,
            translate_quests=True,
        )

    @staticmethod
    def _job(*, progress: float):
        config = mock.Mock()
        config.getboolean.return_value = True
        state = mock.Mock()
        state.should_run.return_value = False
        state.line_progress.return_value = progress
        state.get_full_status.return_value = "status"
        cache_std = mock.Mock()
        cache_ai = mock.Mock()
        on_log = mock.Mock()
        on_status = mock.Mock()
        job = TranslationJob(
            config,
            cache_std,
            cache_ai,
            state,
            on_log=on_log,
            on_status=on_status,
            on_row=mock.Mock(),
        )
        return job, state, cache_std, on_log, on_status

    def test_stopped_analysis_preserves_current_progress(self) -> None:
        job, _state, _cache, _log, on_status = self._job(progress=0.37)
        analyzer = mock.Mock()
        analyzer.analyze.return_value = (100, 25)

        with mock.patch("mineai.runtime.job.ModpackAnalyzer", return_value=analyzer):
            job.run_analysis(self._options())

        on_status.assert_called_with("Остановлено", 0.37)
        self.assertNotIn(mock.call("Готово", 1.0), on_status.call_args_list)

    def test_stopped_translation_preserves_current_progress(self) -> None:
        job, state, cache_std, _log, on_status = self._job(progress=0.42)

        with (
            mock.patch("mineai.runtime.job.discover_jar_files", return_value=[]),
            mock.patch(
                "mineai.runtime.job.discover_loose_lang_files",
                return_value=["dummy.json"],
            ),
            mock.patch("mineai.runtime.job.discover_snbt_files", return_value=[]),
            mock.patch("mineai.runtime.job.discover_bq_files", return_value=[]),
            mock.patch("mineai.runtime.job.StringEstimator") as estimator_cls,
            mock.patch("mineai.runtime.job.TranslationService"),
            mock.patch("mineai.runtime.job.JarProcessor"),
            mock.patch("mineai.runtime.job.LooseJsonProcessor"),
            mock.patch("mineai.runtime.job.SnbtProcessor"),
            mock.patch("mineai.runtime.job.BQProcessor"),
        ):
            estimator_cls.return_value.estimate.return_value = 10
            job.run_translation(self._options())

        cache_std.save.assert_called_once_with()
        state.line_progress.assert_called()
        on_status.assert_called_with("Остановлено", 0.42)
        self.assertNotIn(
            mock.call("Все задачи выполнены!", 1.0),
            on_status.call_args_list,
        )


if __name__ == "__main__":
    unittest.main()
