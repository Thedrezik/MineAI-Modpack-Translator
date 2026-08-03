import json
import os
import tempfile
import unittest

from mineai.engines.base import EngineCallbacks
from mineai.runtime.state import JobState
from mineai.processors.bq_json import BQProcessor
from mineai.processors.snbt import SnbtProcessor
from mineai.processors.snbt_extract import apply_snbt_translations, extract_snbt_strings


TARGET_LANG = {
    "api": "en",
    "file": "en_gb",
    "name": "English",
    "regex": r"[A-Za-z]",
}


class _Service:
    def __init__(self, state=None):
        self.state = state
        self.calls = []

    def translate_dict(self, strings, _target_lang, _callbacks, **kwargs):
        self.calls.append((strings, kwargs))
        if self.state is not None:
            self.state.stop()
        return {key: f"translated:{value}" for key, value in strings.items()}


def _callbacks(state):
    return EngineCallbacks(
        should_run=state.should_run,
        wait_if_paused=state.wait_if_paused,
        on_log=lambda *_args: None,
        on_status=lambda *_args: None,
    )


class SnbtIntegrityTests(unittest.TestCase):
    def test_identity_translation_preserves_snbt_escaping_and_formatting(self):
        content = (
            '"title": "Line\\nNext"\n'
            'description: ["Say \\"hi\\"", "C\\\\D"]'
        )
        strings = extract_snbt_strings(content)

        result = apply_snbt_translations(content, {value: value for value in strings})

        self.assertEqual(result, content)

    def test_lang_file_uses_minecraft_locale_code(self):
        state = JobState(is_running=True)
        service = _Service()
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "en_us.snbt")
            with open(source, "w", encoding="utf-8") as handle:
                handle.write('title: "Hello world"')

            SnbtProcessor(service, state, _callbacks(state)).process(
                source,
                target_lang=TARGET_LANG,
                mode="force",
            )

            self.assertTrue(os.path.exists(os.path.join(directory, "en_gb.snbt")))
            self.assertFalse(os.path.exists(os.path.join(directory, "en_en.snbt")))


class BetterQuestingIntegrityTests(unittest.TestCase):
    @staticmethod
    def _write_quest(path, text):
        data = {"properties:10": {"betterquesting:10": {"name:8": text}}}
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)

    @staticmethod
    def _quest_name(path):
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)["properties:10"]["betterquesting:10"]["name:8"]

    def test_force_mode_retranslates_existing_target_text(self):
        state = JobState(is_running=True)
        service = _Service()
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "Quest.json")
            self._write_quest(path, "Existing English text")

            BQProcessor(service, state, _callbacks(state)).process(
                path,
                target_lang=TARGET_LANG,
                mode="force",
            )

            self.assertEqual(self._quest_name(path), "translated:Existing English text")

    def test_cancellation_after_translation_does_not_write_file(self):
        state = JobState(is_running=True)
        service = _Service(state)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "Quest.json")
            self._write_quest(path, "Original text")
            with open(path, "rb") as handle:
                original = handle.read()

            BQProcessor(service, state, _callbacks(state)).process(
                path,
                target_lang={**TARGET_LANG, "regex": r"[А-Яа-я]"},
                mode="append",
            )

            with open(path, "rb") as handle:
                self.assertEqual(handle.read(), original)


if __name__ == "__main__":
    unittest.main()
