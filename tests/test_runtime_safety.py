import os
import subprocess
import sys
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


class RuntimeSafetyTests(unittest.TestCase):
    def test_processor_submodule_import_does_not_depend_on_import_order(self):
        command = [sys.executable, "-c", "import mineai.processors.snbt_extract"]
        completed = subprocess.run(
            command,
            cwd=os.path.dirname(os.path.dirname(__file__)),
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_translation_exception_is_not_reported_as_success(self):
        state = JobState(is_running=True)
        logs = []
        statuses = []
        cache = mock.Mock(spec=TranslationCache)
        job = TranslationJob(
            _Config(),
            cache,
            cache,
            state,
            on_log=lambda message, _tag: logs.append(message),
            on_status=lambda *args: statuses.append(args),
            on_row=lambda *_args: None,
        )
        options = TranslationOptions(
            mc_dir="/modpack",
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
            translate_mods=True,
            translate_books=False,
            translate_quests=False,
        )

        with (
            mock.patch("mineai.runtime.job.discover_jar_files", return_value=[]),
            mock.patch(
                "mineai.runtime.job.discover_loose_lang_files",
                return_value=["broken.json"],
            ),
            mock.patch("mineai.runtime.job.StringEstimator.estimate", return_value=1),
            mock.patch(
                "mineai.runtime.job.LooseJsonProcessor.process",
                side_effect=RuntimeError("disk failure"),
            ),
        ):
            job.run_translation(options)

        self.assertTrue(any("КРИТИЧЕСКАЯ ОШИБКА" in message for message in logs))
        self.assertFalse(any("УСПЕШНО ЗАВЕРШЕН" in message for message in logs))
        self.assertEqual(statuses[-1], ("Ошибка перевода", 1.0))


if __name__ == "__main__":
    unittest.main()
