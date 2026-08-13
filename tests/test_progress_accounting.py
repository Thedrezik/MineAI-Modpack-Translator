import json
import os
import tempfile
import unittest
import zipfile

from mineai.engines.base import EngineCallbacks
from mineai.processors.bq_json import BQProcessor
from mineai.processors.jar import JarProcessor
from mineai.processors.loose_json import LooseJsonProcessor
from mineai.processors.snbt import SnbtProcessor
from mineai.runtime.state import JobState


TARGET_LANG = {
    "api": "ru",
    "file": "ru_ru",
    "name": "Russian",
    "regex": r"[А-Яа-яЁё]",
}


class _Config:
    def getboolean(self, _section, _key):
        return False


class _ProgressService:
    def __init__(self):
        self.config = _Config()

    def translate_dict(self, strings, _target_lang, callbacks, **_kwargs):
        if callbacks.on_progress:
            callbacks.on_progress(len(strings))
        return {key: "Перевод" for key in strings}


def _callbacks(state: JobState) -> EngineCallbacks:
    return EngineCallbacks(
        should_run=state.should_run,
        wait_if_paused=state.wait_if_paused,
        on_log=lambda *_args: None,
        on_status=lambda *_args: None,
        on_progress=state.increment_translated,
    )


class ProgressAccountingTests(unittest.TestCase):
    def test_loose_json_counts_each_translation_once(self):
        state = JobState(is_running=True)
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "en_us.json")
            with open(source, "w", encoding="utf-8") as handle:
                json.dump({"one": "Hello", "two": "World"}, handle)

            LooseJsonProcessor(
                _ProgressService(), state, _callbacks(state)
            ).process(
                source,
                directory,
                target_lang=TARGET_LANG,
                mode="force",
                output_mode="inplace",
                pack_writer=None,
            )

        self.assertEqual(state.snapshot().translated_strings, 2)

    def test_snbt_counts_each_translation_once(self):
        state = JobState(is_running=True)
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "en_us.snbt")
            with open(source, "w", encoding="utf-8") as handle:
                handle.write('title: "Hello"\ndescription: "World"')

            SnbtProcessor(_ProgressService(), state, _callbacks(state)).process(
                source,
                target_lang=TARGET_LANG,
                mode="force",
            )

        self.assertEqual(state.snapshot().translated_strings, 2)

    def test_betterquesting_counts_each_translation_once(self):
        state = JobState(is_running=True)
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "Quest.json")
            payload = {
                "properties:10": {
                    "betterquesting:10": {
                        "name:8": "Hello",
                        "desc:8": "World",
                    }
                }
            }
            with open(source, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)

            BQProcessor(_ProgressService(), state, _callbacks(state)).process(
                source,
                target_lang=TARGET_LANG,
                mode="force",
            )

        self.assertEqual(state.snapshot().translated_strings, 2)

    def test_jar_locale_counts_each_translation_once(self):
        state = JobState(is_running=True)
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "example.jar")
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr(
                    "assets/example/lang/en_us.json",
                    json.dumps({"one": "Hello", "two": "World"}),
                )

            JarProcessor(_ProgressService(), state, _callbacks(state)).process(
                source,
                target_lang=TARGET_LANG,
                mode="force",
                output_mode="inplace",
                translate_mods=True,
                translate_books=False,
                pack_writer=None,
            )

        self.assertEqual(state.snapshot().translated_strings, 2)


if __name__ == "__main__":
    unittest.main()
