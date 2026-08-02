import json
import os
import tempfile
import unittest


# Initialize packages in the same order as the application while keeping
# settings/dictionary import-time side effects outside the repository.
_original_cwd = os.getcwd()
with tempfile.TemporaryDirectory() as _import_cwd:
    os.chdir(_import_cwd)
    try:
        from mineai.engines.base import EngineCallbacks
        from mineai.runtime.job import TranslationJob as _TranslationJob
        from mineai.processors.loose_json import LooseJsonProcessor
        from mineai.processors.snbt import SnbtProcessor
        from mineai.runtime.state import JobState
    finally:
        os.chdir(_original_cwd)


TARGET_LANG = {
    "api": "ru",
    "file": "ru_ru",
    "name": "Russian",
    "regex": r"[А-Яа-яЁё]",
}


def callbacks(state: JobState) -> EngineCallbacks:
    return EngineCallbacks(
        should_run=state.should_run,
        wait_if_paused=state.wait_if_paused,
        on_log=lambda _message, _tag: None,
        on_status=lambda _message: None,
    )


class StopDuringTranslationService:
    def __init__(self, state: JobState) -> None:
        self.state = state

    def translate_dict(
        self,
        strings: dict[str, str],
        _target_lang: dict,
        _callbacks: EngineCallbacks,
        *,
        context: str = "",
    ) -> dict[str, str]:
        del context
        self.state.stop()
        return {key: f"Перевод: {value}" for key, value in strings.items()}


class CancelledProcessorWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = JobState(is_running=True)
        self.service = StopDuringTranslationService(self.state)
        self.callbacks = callbacks(self.state)

    def test_loose_json_does_not_create_output_after_stop(self) -> None:
        with tempfile.TemporaryDirectory() as mc_dir:
            lang_dir = os.path.join(mc_dir, "kubejs", "assets", "test", "lang")
            os.makedirs(lang_dir)
            source_path = os.path.join(lang_dir, "en_us.json")
            target_path = os.path.join(lang_dir, "ru_ru.json")
            with open(source_path, "w", encoding="utf-8") as handle:
                json.dump({"item.test": "Test item"}, handle)

            processor = LooseJsonProcessor(
                self.service,
                self.state,
                self.callbacks,
            )
            processor.process(
                source_path,
                mc_dir,
                target_lang=TARGET_LANG,
                mode="force",
                output_mode="inplace",
                pack_writer=None,
            )

            self.assertFalse(os.path.exists(target_path))
            self.assertEqual(self.state.translated_strings, 0)

    def test_snbt_keeps_source_unchanged_after_stop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source_path = os.path.join(directory, "chapter.snbt")
            original = 'title: "Quest title"\n'
            with open(source_path, "w", encoding="utf-8") as handle:
                handle.write(original)

            processor = SnbtProcessor(
                self.service,
                self.state,
                self.callbacks,
            )
            processor.process(
                source_path,
                target_lang=TARGET_LANG,
                mode="append",
            )

            with open(source_path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), original)
            self.assertEqual(self.state.translated_strings, 0)


if __name__ == "__main__":
    unittest.main()
