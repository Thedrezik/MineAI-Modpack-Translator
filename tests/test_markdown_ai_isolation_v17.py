import tempfile
import unittest

from mineai.cache import TranslationCache
from mineai.engines.base import EngineCallbacks
from mineai.engines.service import TranslationService
from mineai.processors.formatkit_books_pilot_v341 import (
    _MARKDOWN_CACHE_SCOPE,
    _markdown_translation_service,
)


TARGET_LANG = {
    "file": "ru_ru",
    "api": "ru",
    "name": "Russian",
    "regex": r"[А-Яа-яЁё]",
}


class _Config:
    def getboolean(self, section: str, key: str, default=False) -> bool:
        return False


class _RecordingEngine:
    def __init__(self, translated_by_source: dict[str, str]) -> None:
        self.translated_by_source = translated_by_source
        self.calls: list[tuple[str, ...]] = []

    def translate_batch(self, items, _target_lang, _callbacks):
        self.calls.append(tuple(items.keys()))
        return {
            key: self.translated_by_source[item.original]
            for key, item in items.items()
        }


class _RecordingService(TranslationService):
    def __init__(self, cache, engine) -> None:
        super().__init__(
            "kobold",
            cache,
            _Config(),
            ai_batch=20,
            ai_provider="local",
        )
        self._recording_engine = engine

    def _build_engine(self, context: str = "", prompt_type: str = "mods"):
        return self._recording_engine


def _callbacks() -> EngineCallbacks:
    return EngineCallbacks(
        should_run=lambda: True,
        wait_if_paused=lambda: None,
        on_log=lambda *_args: None,
        on_status=lambda *_args: None,
        on_progress=lambda *_args: None,
    )


class MarkdownAiIsolationV17Tests(unittest.TestCase):
    def test_markdown_uses_singleton_ai_batches_and_ignores_legacy_cache(self) -> None:
        source_a = "pressing the hotkey, (N by default)."
        source_b = "In this screen you can add or remove upgrades."
        translated_a = "Нажмите горячую клавишу (по умолчанию N)."
        translated_b = "На этом экране можно добавлять или удалять улучшения."

        with tempfile.TemporaryDirectory() as temp_dir:
            cache = TranslationCache(f"{temp_dir}/ai_cache.json")
            # These are structurally valid but semantically shifted neighbour values,
            # matching the failure class observed in the paused full-modpack run.
            cache.set("ru", source_a, "На этом экране можно добавлять улучшения.")
            cache.set("ru", source_b, "Карта подзарядки ME")

            engine = _RecordingEngine(
                {
                    source_a: translated_a,
                    source_b: translated_b,
                }
            )
            service = _RecordingService(cache, engine)
            guarded = _markdown_translation_service(service)

            self.assertIsNot(guarded, service)
            self.assertEqual(service.ai_batch, 20)
            self.assertEqual(guarded.ai_batch, 1)

            result = guarded.translate_dict(
                {"10": source_a, "11": source_b},
                TARGET_LANG,
                _callbacks(),
                context="Advanced AE",
            )

            self.assertEqual(
                result,
                {"10": translated_a, "11": translated_b},
            )
            self.assertEqual(engine.calls, [("10",), ("11",)])

            # Old unscoped Markdown values are quarantined instead of reused.
            self.assertIsNone(cache.get("ru", source_a)[0])
            self.assertIsNone(cache.get("ru", source_b)[0])
            self.assertEqual(
                cache.get("ru", _MARKDOWN_CACHE_SCOPE + source_a)[0],
                translated_a,
            )
            self.assertEqual(
                cache.get("ru", _MARKDOWN_CACHE_SCOPE + source_b)[0],
                translated_b,
            )

            # A second pass reuses only the new scoped values and makes no AI call.
            second = guarded.translate_dict(
                {"10": source_a, "11": source_b},
                TARGET_LANG,
                _callbacks(),
                context="Advanced AE",
            )
            self.assertEqual(second, result)
            self.assertEqual(engine.calls, [("10",), ("11",)])


if __name__ == "__main__":
    unittest.main()
