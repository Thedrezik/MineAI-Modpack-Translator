import json
import unittest
from unittest import mock

from mineai.engines.base import EngineCallbacks
from mineai.engines.google import GoogleEngine
from mineai.engines.llm_common import BatchLlmEngine, build_translation_prompt
from mineai.engines.service import TranslationService


TARGET_LANG = {"api": "ru", "name": "Russian"}


class _Cache:
    def __init__(self):
        self.values = {}

    def get(self, api, source):
        return self.values.get((api, source))

    def set(self, api, source, value):
        self.values[(api, source)] = value

    def save_if_threshold(self):
        pass


class _Config:
    def getboolean(self, section, key):
        return False

    def getint(self, section, key, fallback=0):
        return fallback

    def get(self, section, key):
        return ""


CALLBACKS = EngineCallbacks(
    should_run=lambda: True,
    wait_if_paused=lambda: None,
    on_log=lambda *_args: None,
    on_status=lambda *_args: None,
)


class RoutingTests(unittest.TestCase):
    def test_service_forwards_prompt_type_to_engine_factory(self):
        service = TranslationService("ai", _Cache(), _Config())
        engine = BatchLlmEngine(
            call_api=lambda _prompt, _limit: json.dumps({"key": "Перевод"})
        )
        with mock.patch.object(service, "_build_engine", return_value=engine) as factory:
            service.translate_dict(
                {"key": "Original"},
                TARGET_LANG,
                CALLBACKS,
                context="Quest chapter",
                prompt_type="quests",
            )

        factory.assert_called_once_with("Quest chapter", "quests")

    def test_context_mode_adds_context_to_an_editable_mod_prompt(self):
        with mock.patch(
            "mineai.engines.llm_common.load_prompts",
            return_value={
                "mods": "Translate to {lang_name}.",
                "technical": "Return JSON.",
            },
        ):
            prompt = build_translation_prompt(
                {"key": "Value"},
                "Russian",
                mode="context",
                context="Example Mod",
            )

        self.assertIn("Context: Example Mod", prompt)

    def test_failed_google_translation_is_not_cached(self):
        cache = _Cache()
        service = TranslationService("google", cache, _Config())
        engine = GoogleEngine()
        with (
            mock.patch.object(service, "_build_engine", return_value=engine),
            mock.patch.object(engine, "_request", return_value=None),
        ):
            result = service.translate_dict(
                {"key": "Original"},
                TARGET_LANG,
                CALLBACKS,
            )

        self.assertEqual(result, {"key": "Original"})
        self.assertEqual(cache.values, {})


if __name__ == "__main__":
    unittest.main()
