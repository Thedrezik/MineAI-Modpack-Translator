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

    def getint(self, _section, _key, fallback=0):
        return fallback


class _RecoveryConfig(_Config):
    def get(self, section, key):
        if (section, key) == ("AI", "model_path"):
            return "model.gguf"
        return ""


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
        writer.close.return_value = (None, None)

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

    def test_cache_recovery_forces_full_processing_and_layers_both_caches(self):
        state = JobState(is_running=True)
        logs = []
        statuses = []
        cache_std = mock.Mock(spec=TranslationCache)
        cache_ai = mock.Mock(spec=TranslationCache)
        job = TranslationJob(
            _RecoveryConfig(),
            cache_std,
            cache_ai,
            state,
            on_log=lambda message, _tag: logs.append(message),
            on_status=lambda *args: statuses.append(args),
            on_row=lambda *_args: None,
        )
        options = _options()
        options.engine = "ai"
        options.ai_provider = "local"
        options.process_mode = "skip"
        options.cache_recovery_mode = True
        loose_processor = mock.Mock()
        service = mock.Mock()

        with (
            mock.patch("mineai.runtime.job.discover_jar_files", return_value=[]),
            mock.patch(
                "mineai.runtime.job.discover_loose_lang_files",
                return_value=["example.json"],
            ),
            mock.patch("mineai.runtime.job.StringEstimator") as estimator_cls,
            mock.patch(
                "mineai.runtime.job.TranslationService",
                return_value=service,
            ) as service_cls,
            mock.patch("mineai.runtime.job.JarProcessor"),
            mock.patch(
                "mineai.runtime.job.LooseJsonProcessor",
                return_value=loose_processor,
            ),
            mock.patch("mineai.runtime.job.SnbtProcessor"),
            mock.patch("mineai.runtime.job.BQProcessor"),
            mock.patch("mineai.runtime.job.HeraclesProcessor"),
            mock.patch.object(job.ai_launcher, "ensure_running", return_value=True),
        ):
            estimator_cls.return_value.estimate.return_value = 1
            job.run_translation(options)

        self.assertEqual(estimator_cls.return_value.estimate.call_args.kwargs["mode"], "force")
        self.assertEqual(loose_processor.process.call_args.kwargs["mode"], "force")
        self.assertIs(service_cls.call_args.args[1], cache_ai)
        self.assertEqual(
            service_cls.call_args.kwargs["fallback_caches"],
            [("Google-кэш", cache_std)],
        )
        self.assertTrue(service_cls.call_args.kwargs["force_google_fallback"])
        cache_ai.save.assert_called_once_with()
        cache_std.save.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
