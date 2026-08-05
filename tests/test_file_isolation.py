import unittest
from unittest import mock

from mineai.cache import TranslationCache
from mineai.runtime.job import TranslationJob, TranslationOptions
from mineai.runtime.state import JobState


class _Config:
    def get(self, _section, _key):
        return ""

    def getboolean(self, _section, _key):
        return False


def _options(*, output_mode="inplace") -> TranslationOptions:
    return TranslationOptions(
        mc_dir="/modpack",
        language_label="Русский",
        mc_version="1.20.1",
        output_mode=output_mode,
        pack_name="MineAI_Pack",
        engine="google",
        google_mode="single",
        ai_mode="safe",
        ai_batch=20,
        ai_provider="local",
        process_mode="append",
        translate_mods=True,
        translate_books=False,
        translate_quests=False,
    )


class FileIsolationTests(unittest.TestCase):
    @staticmethod
    def _job(state, cache, logs, statuses):
        return TranslationJob(
            _Config(),
            cache,
            cache,
            state,
            on_log=lambda message, _tag: logs.append(message),
            on_status=lambda *args: statuses.append(args),
            on_row=lambda *_args: None,
        )

    def test_failed_file_is_isolated_and_next_file_is_processed(self):
        state = JobState(is_running=True)
        logs = []
        statuses = []
        cache = mock.Mock(spec=TranslationCache)
        job = self._job(state, cache, logs, statuses)
        processed = []

        def process(path, *_args, **_kwargs):
            processed.append(path)
            if path == "broken.json":
                raise RuntimeError("disk failure")

        with (
            mock.patch("mineai.runtime.job.discover_jar_files", return_value=[]),
            mock.patch(
                "mineai.runtime.job.discover_loose_lang_files",
                return_value=["broken.json", "good.json"],
            ),
            mock.patch("mineai.runtime.job.StringEstimator.estimate", return_value=2),
            mock.patch(
                "mineai.runtime.job.LooseJsonProcessor.process",
                side_effect=process,
            ),
        ):
            job.run_translation(_options())

        self.assertEqual(processed, ["broken.json", "good.json"])
        cache.save.assert_called_once_with()
        self.assertTrue(any("broken.json" in message for message in logs))
        self.assertTrue(any("ЗАВЕРШЕНО С ОШИБКАМИ" in message for message in logs))
        self.assertFalse(any("УСПЕШНО ЗАВЕРШЕН" in message for message in logs))
        self.assertEqual(statuses[-1], ("Завершено с ошибками", 1.0))

    def test_pack_writer_is_closed_after_file_failure(self):
        state = JobState(is_running=True)
        logs = []
        statuses = []
        cache = mock.Mock(spec=TranslationCache)
        job = self._job(state, cache, logs, statuses)
        writer = mock.Mock()
        writer.rp_zip_path = "/tmp/rp.zip"
        writer.dp_zip_path = "/tmp/dp.zip"

        with (
            mock.patch("mineai.runtime.job.discover_jar_files", return_value=[]),
            mock.patch(
                "mineai.runtime.job.discover_loose_lang_files",
                return_value=["broken.json"],
            ),
            mock.patch("mineai.runtime.job.StringEstimator.estimate", return_value=1),
            mock.patch("mineai.runtime.job.PackWriter", return_value=writer),
            mock.patch(
                "mineai.runtime.job.LooseJsonProcessor.process",
                side_effect=RuntimeError("write failure"),
            ),
        ):
            job.run_translation(_options(output_mode="resourcepack"))

        writer.close.assert_called_once_with()
        cache.save.assert_called_once_with()
        self.assertEqual(statuses[-1], ("Завершено с ошибками", 1.0))

    def test_cache_save_failure_is_reported_as_critical(self):
        state = JobState(is_running=True)
        logs = []
        statuses = []
        cache = mock.Mock(spec=TranslationCache)
        cache.save.side_effect = OSError("cache locked")
        job = self._job(state, cache, logs, statuses)

        with (
            mock.patch("mineai.runtime.job.discover_jar_files", return_value=[]),
            mock.patch(
                "mineai.runtime.job.discover_loose_lang_files",
                return_value=["good.json"],
            ),
            mock.patch("mineai.runtime.job.StringEstimator.estimate", return_value=1),
            mock.patch("mineai.runtime.job.LooseJsonProcessor.process"),
        ):
            job.run_translation(_options())

        self.assertTrue(any("Не удалось сохранить кэш" in message for message in logs))
        self.assertFalse(any("УСПЕШНО ЗАВЕРШЕН" in message for message in logs))
        self.assertEqual(statuses[-1], ("Ошибка перевода", 1.0))


if __name__ == "__main__":
    unittest.main()
