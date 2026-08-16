import unittest
from unittest import mock

from mineai.runtime.job import TranslationJob, TranslationOptions
from mineai.runtime.state import JobState


class FailedStringTerminalStatusV341Tests(unittest.TestCase):
    def test_rejected_string_metrics_cannot_finish_as_success(self):
        config = mock.Mock()
        config.getboolean.return_value = True
        state = JobState(is_running=True)
        cache = mock.Mock()
        logs = []
        statuses = []
        job = TranslationJob(
            config,
            cache,
            cache,
            state,
            on_log=lambda message, _tag: logs.append(message),
            on_status=lambda *args: statuses.append(args),
            on_row=mock.Mock(),
        )
        options = TranslationOptions(
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

        def mark_rejected_line(*_args, **_kwargs):
            state.increment_translated()
            state.mark_failed()

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
            mock.patch(
                "mineai.runtime.job.LooseJsonProcessor.process",
                side_effect=mark_rejected_line,
            ),
            mock.patch("mineai.runtime.job.SnbtProcessor"),
            mock.patch("mineai.runtime.job.BQProcessor"),
        ):
            estimator_cls.return_value.estimate.return_value = 1
            job.run_translation(options)

        self.assertTrue(any("ошибок строк — 1" in message for message in logs))
        self.assertFalse(any("УСПЕШНО ЗАВЕРШЕН" in message for message in logs))
        self.assertEqual(statuses[-1], ("Завершено с ошибками", 1.0))


if __name__ == "__main__":
    unittest.main()
