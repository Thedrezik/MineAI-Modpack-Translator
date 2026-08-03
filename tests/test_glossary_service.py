import tempfile
import unittest
from pathlib import Path

from mineai.cache import TranslationCache
from mineai.engines.base import EngineCallbacks, TranslationEngine
from mineai.engines.service import TranslationService
from mineai.glossary import GlossaryEntry, SmartGlossary
from mineai.text_processing import unmask_translation


class FakeConfig:
    def getboolean(self, _section: str, _key: str) -> bool:
        return False


class EchoTranslationEngine(TranslationEngine):
    def translate_batch(self, items, _target_lang, _callbacks):
        return {
            key: unmask_translation(f"Перевод: {item.masked}", item.mapping)
            for key, item in items.items()
        }


class GlossaryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.callbacks = EngineCallbacks(
            should_run=lambda: True,
            wait_if_paused=lambda: None,
            on_log=lambda _message, _tag: None,
            on_status=lambda _message: None,
        )
        self.target = {"api": "ru", "name": "Russian"}

    def test_exact_bypasses_engine_and_phrase_is_sent_masked(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            glossary = SmartGlossary(
                entries=[
                    GlossaryEntry(
                        "Pin",
                        "Закрепить",
                        scope=("ftbquests",),
                        apply="exact",
                    ),
                    GlossaryEntry(
                        "Mechanical Press",
                        "Механический пресс",
                        scope=("create",),
                        apply="phrase",
                    ),
                ]
            )
            service = TranslationService(
                "google",
                TranslationCache(str(Path(temp) / "cache.json")),
                FakeConfig(),
                glossary=glossary,
            )
            service._build_engine = (
                lambda _context="", _prompt_type="mods": EchoTranslationEngine()
            )

            exact = service.translate_dict(
                {"a": "Pin"},
                self.target,
                self.callbacks,
                scope="ftbquests",
            )
            phrase = service.translate_dict(
                {"b": "Use Mechanical Press"},
                self.target,
                self.callbacks,
                scope="create",
            )

            self.assertEqual({"a": "Закрепить"}, exact)
            self.assertEqual(
                {"b": "Перевод: Use Механический пресс"},
                phrase,
            )

    def test_disabled_glossary_uses_legacy_cache_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            cache = TranslationCache(str(Path(temp) / "cache.json"))
            cache.set("ru", "Pin", "Из старого кэша")
            service = TranslationService("google", cache, FakeConfig())
            service._build_engine = (
                lambda _context="", _prompt_type="mods": EchoTranslationEngine()
            )

            result = service.translate_dict(
                {"a": "Pin"},
                self.target,
                self.callbacks,
                scope="ftbquests",
            )

            self.assertEqual({"a": "Из старого кэша"}, result)


if __name__ == "__main__":
    unittest.main()
