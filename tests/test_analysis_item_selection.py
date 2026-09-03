import inspect
import os
import tempfile
import unittest
import zipfile
from unittest import mock

from mineai.cache import TranslationCache
from mineai.processors.analyzer import ModpackAnalyzer
from mineai.runtime.job import TranslationJob, TranslationOptions
from mineai.runtime.state import JobState


class _Config:
    def get(self, _section, _key):
        return ""

    def getboolean(self, _section, _key):
        return False


def _target_key(path: str, scope: str) -> str:
    return f"{scope}:{os.path.normcase(os.path.abspath(path))}"


def _options(**overrides) -> TranslationOptions:
    values = {
        "mc_dir": "C:/modpack",
        "language_label": "Русский",
        "mc_version": "1.20.1",
        "output_mode": "inplace",
        "pack_name": "MineAI_Pack",
        "engine": "google",
        "google_mode": "single",
        "ai_mode": "safe",
        "ai_batch": 20,
        "ai_provider": "local",
        "process_mode": "append",
        "translate_mods": True,
        "translate_books": True,
        "translate_quests": False,
    }
    values.update(overrides)
    return TranslationOptions(**values)


class AnalysisSelectionRuntimeTests(unittest.TestCase):
    @staticmethod
    def _job(state, cache):
        return TranslationJob(
            _Config(),
            cache,
            cache,
            state,
            on_log=lambda *_args: None,
            on_status=lambda *_args: None,
            on_row=lambda *_args: None,
        )

    def test_selected_book_scope_skips_interface_in_the_same_jar(self) -> None:
        jar_path = "C:/modpack/mods/example.jar"
        options = _options()
        options.selected_items = frozenset({_target_key(jar_path, "books")})
        state = JobState(is_running=True)
        cache = mock.Mock(spec=TranslationCache)
        job = self._job(state, cache)

        with (
            mock.patch("mineai.runtime.job.discover_jar_files", return_value=[jar_path]),
            mock.patch("mineai.runtime.job.discover_loose_lang_files", return_value=[]),
            mock.patch("mineai.runtime.job.discover_snbt_files", return_value=[]),
            mock.patch("mineai.runtime.job.discover_bq_files", return_value=[]),
            mock.patch("mineai.runtime.job.StringEstimator.estimate", return_value=1) as estimate,
            mock.patch("mineai.runtime.job.JarProcessor") as processor,
        ):
            job.run_translation(options)

        process_call = processor.return_value.process.call_args
        self.assertIsNotNone(process_call)
        self.assertFalse(process_call.kwargs["translate_mods"])
        self.assertTrue(process_call.kwargs["translate_books"])
        self.assertEqual(
            estimate.call_args.kwargs["selected_items"],
            options.selected_items,
        )

    def test_unchecked_quest_file_is_never_processed(self) -> None:
        selected = "C:/modpack/config/ftbquests/quests/chapter/selected.snbt"
        unchecked = "C:/modpack/config/ftbquests/quests/chapter/unchecked.snbt"
        options = _options(
            translate_mods=False,
            translate_books=False,
            translate_quests=True,
        )
        options.selected_items = frozenset({_target_key(selected, "quests")})
        state = JobState(is_running=True)
        cache = mock.Mock(spec=TranslationCache)
        job = self._job(state, cache)

        with (
            mock.patch("mineai.runtime.job.discover_jar_files", return_value=[]),
            mock.patch("mineai.runtime.job.discover_loose_lang_files", return_value=[]),
            mock.patch(
                "mineai.runtime.job.discover_snbt_files",
                return_value=[selected, unchecked],
            ),
            mock.patch("mineai.runtime.job.discover_bq_files", return_value=[]),
            mock.patch("mineai.runtime.job.StringEstimator.estimate", return_value=1),
            mock.patch("mineai.runtime.job.SnbtProcessor") as processor,
        ):
            job.run_translation(options)

        processed = [call.args[0] for call in processor.return_value.process.call_args_list]
        self.assertEqual(processed, [selected])


class AnalyzerSelectionContractTests(unittest.TestCase):
    def test_analysis_emits_separate_mod_and_book_targets(self) -> None:
        parameters = inspect.signature(ModpackAnalyzer.analyze).parameters
        self.assertIn("on_item", parameters)
        if "on_item" not in parameters:
            return

        with tempfile.TemporaryDirectory() as temp_dir:
            mods_dir = os.path.join(temp_dir, "mods")
            os.makedirs(mods_dir)
            jar_path = os.path.join(mods_dir, "example.jar")
            with zipfile.ZipFile(jar_path, "w") as archive:
                archive.writestr(
                    "assets/example/lang/en_us.json",
                    '{"example.title": "Example title"}',
                )
                archive.writestr(
                    "assets/example/patchouli_books/guide/en_us/entries/page.json",
                    '{"name": "Example page"}',
                )

            state = JobState(is_running=True)
            items = []
            ModpackAnalyzer(state).analyze(
                temp_dir,
                target_lang={
                    "file": "ru_ru",
                    "regex": r"[А-Яа-яЁё]",
                },
                translate_mods=True,
                translate_books=True,
                translate_quests=False,
                on_row=lambda *_args: None,
                on_item=items.append,
                on_log=lambda *_args: None,
                on_status=lambda *_args: None,
            )

        self.assertEqual({item.scope for item in items}, {"mods", "books"})
        book_item = next(item for item in items if item.scope == "books")
        self.assertIn("Patchouli", book_item.kind)
        self.assertEqual(
            {item.key for item in items},
            {
                _target_key(jar_path, "mods"),
                _target_key(jar_path, "books"),
            },
        )


if __name__ == "__main__":
    unittest.main()
