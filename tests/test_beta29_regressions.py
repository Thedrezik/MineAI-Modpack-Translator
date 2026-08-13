import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import zipfile

from mineai import __version__
from mineai.cache import TranslationCache
from mineai.engines.base import EngineCallbacks
from mineai.engines.service import TranslationService
from mineai.json_utils import apply_translations_by_path, key_to_path, path_to_key
from mineai.processors.analyzer import ModpackAnalyzer
from mineai.processors.estimator import StringEstimator
from mineai.processors.selection import collect_book_json_selection
from mineai.processors.snbt import SnbtProcessor
from mineai.runtime.state import JobState
from mineai.text_processing import is_technical_term


RUSSIAN = {
    "file": "ru_ru",
    "api": "ru",
    "name": "Russian",
    "regex": r"[А-Яа-яЁё]",
}
SPANISH = {
    "file": "es_es",
    "api": "es",
    "name": "Spanish",
    "regex": r"[áéíóúüñÁÉÍÓÚÜÑ]",
}


def _state() -> JobState:
    state = JobState()
    state.start()
    return state


def _callbacks(logs=None) -> EngineCallbacks:
    logs = logs if logs is not None else []
    return EngineCallbacks(
        should_run=lambda: True,
        wait_if_paused=lambda: None,
        on_log=lambda message, _tag: logs.append(message),
        on_status=lambda _message: None,
        on_progress=lambda _count: None,
    )


class _TranslationServiceStub:
    def __init__(self) -> None:
        self.calls = []

    def translate_dict(self, strings, _target_lang, _callbacks, **_kwargs):
        self.calls.append(dict(strings))
        return {key: f"Перевод: {value}" for key, value in strings.items()}


class StructuredDocumentRegressionTests(unittest.TestCase):
    def test_release_version_is_beta37(self) -> None:
        self.assertEqual(__version__, "10.0.0 - BETAv38")

    def test_common_structured_document_module_exists(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec("mineai.formats.document"))

    def test_append_repairs_existing_book_page_that_is_still_english(self) -> None:
        source = {
            "pages": [
                {
                    "text": (
                        "This machine accepts items and exports the result."
                    )
                }
            ]
        }
        existing = {
            "pages": [
                {
                    "text": (
                        "This machine accepts ingredients and exports results."
                    )
                }
            ]
        }

        _source, preserved, pending = collect_book_json_selection(
            source,
            existing,
            "append",
            RUSSIAN,
        )

        self.assertEqual(
            pending,
            {"pages/0/text": source["pages"][0]["text"]},
        )
        self.assertNotIn("pages/0/text", preserved)

    def test_json_path_round_trip_preserves_slashes_numeric_keys_and_indices(self) -> None:
        path = ("pages/intro", "01", 0, "title")
        encoded = path_to_key(path)

        self.assertEqual(key_to_path(encoded), path)

        data = {"pages/intro": {"01": [{"title": "Original title"}]}}
        apply_translations_by_path(data, {encoded: "Переведённый заголовок"})
        self.assertEqual(
            data["pages/intro"]["01"][0]["title"],
            "Переведённый заголовок",
        )


class SnbtRegressionTests(unittest.TestCase):
    def test_append_merges_existing_translation_into_new_source_structure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "en_us.snbt"
            target = root / "ru_ru.snbt"
            source.write_text(
                '{title:"Old title",subtitle:"Brand new subtitle"}',
                encoding="utf-8",
            )
            target.write_text('{title:"Старый заголовок"}', encoding="utf-8")

            state = _state()
            estimator = StringEstimator(state)
            self.assertEqual(
                estimator._estimate_snbt(
                    str(source),
                    "append",
                    RUSSIAN["regex"],
                    RUSSIAN["file"],
                ),
                1,
            )

            service = _TranslationServiceStub()
            written = SnbtProcessor(service, state, _callbacks()).process(
                str(source),
                target_lang=RUSSIAN,
                mode="append",
            )

            self.assertEqual(written, str(target))
            self.assertEqual(len(service.calls), 1)
            output = target.read_text(encoding="utf-8")
            self.assertIn('title:"Старый заголовок"', output)
            self.assertIn('subtitle:"Перевод: Brand new subtitle"', output)

    def test_analyzer_reads_existing_target_locale(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "en_us.snbt"
            target = root / "ru_ru.snbt"
            source.write_text('{title:"Original title"}', encoding="utf-8")
            target.write_text('{title:"Готовый перевод"}', encoding="utf-8")

            rows = []
            analyzed = ModpackAnalyzer(_state())._analyze_snbt(
                str(source),
                RUSSIAN["regex"],
                lambda *row: rows.append(row),
            )

            self.assertEqual(analyzed, (1, 1))
            self.assertEqual(rows[0][3:5], (1, 1))

    def test_same_latin_snbt_estimator_matches_processor_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "en_us.snbt"
            target = root / "es_es.snbt"
            source.write_text('{title:"Original title"}', encoding="utf-8")
            target.write_text('{title:"Titulo traducido"}', encoding="utf-8")

            estimated = StringEstimator(_state()).estimate(
                [],
                [],
                [str(source)],
                [],
                target_lang=SPANISH,
                mode="append",
                translate_mods=False,
                translate_books=False,
                translate_quests=True,
                smart_glue=False,
            )

            self.assertEqual(estimated, 0)


class JsonBookAnalyzerRegressionTests(unittest.TestCase):
    def test_analyzer_and_estimator_share_technical_term_filter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            jar_path = Path(temp_dir) / "book.jar"
            with zipfile.ZipFile(jar_path, "w") as archive:
                archive.writestr(
                    "assets/demo/patchouli_books/manual/en_us/entries/page.json",
                    json.dumps(
                        {
                            "title": "GUI",
                            "pages": [
                                {
                                    "type": "patchouli:text",
                                    "text": "Translate this text",
                                }
                            ],
                        }
                    ),
                )

            analyzer = ModpackAnalyzer(_state())
            analyzed = analyzer._analyze_jar(
                str(jar_path),
                "ru_ru.json",
                RUSSIAN["regex"],
                False,
                True,
                lambda *_row: None,
                "Demo",
            )
            estimated = StringEstimator(_state())._estimate_jar(
                str(jar_path),
                "ru_ru.json",
                RUSSIAN,
                "force",
                False,
                True,
                False,
            )

            self.assertEqual(analyzed, (1, 0))
            self.assertEqual(estimated, 1)


class SameLatinBqRegressionTests(unittest.TestCase):
    def test_same_latin_bq_estimator_uses_backup_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "quest.json"
            backup = Path(str(path) + ".bak")
            original = {
                "properties:10": {
                    "betterquesting:10": {
                        "name:8": "Steam Age",
                        "desc:8": "Old description",
                    }
                }
            }
            translated = {
                "properties:10": {
                    "betterquesting:10": {
                        "name:8": "Edad del vapor",
                        "desc:8": "Descripcion anterior",
                    }
                }
            }
            backup.write_text(json.dumps(original), encoding="utf-8")
            path.write_text(json.dumps(translated), encoding="utf-8")

            estimated = StringEstimator(_state()).estimate(
                [],
                [],
                [],
                [str(path)],
                target_lang=SPANISH,
                mode="append",
                translate_mods=False,
                translate_books=False,
                translate_quests=True,
                smart_glue=False,
            )

            self.assertEqual(estimated, 0)


class ShortFragmentRegressionTests(unittest.TestCase):
    def test_orphan_suffix_and_quantity_are_technical(self) -> None:
        self.assertTrue(is_technical_term("'s"))
        self.assertTrue(is_technical_term("1x"))

    def test_meaningful_short_names_remain_translatable(self) -> None:
        self.assertFalse(is_technical_term("VoidShimmer Goo"))
        self.assertFalse(is_technical_term("Steam Age"))


class _ServiceConfig:
    @staticmethod
    def getboolean(section, key):
        if (section, key) == ("GENERAL", "smart_glue"):
            return False
        return False

    @staticmethod
    def getint(_section, _key, fallback=0):
        return fallback or 3


class _IdentityThenTranslationEngine:
    def __init__(self) -> None:
        self.calls = 0

    def translate_batch(self, items, _target_lang, _callbacks):
        self.calls += 1
        if self.calls == 1:
            return {key: item.original for key, item in items.items()}
        return {
            key: "Эпоха пара" if item.original == "Steam Age" else "Мерцающая слизь Пустоты"
            for key, item in items.items()
        }


class _RetryingTranslationService(TranslationService):
    def __init__(self, cache) -> None:
        super().__init__("ai", cache, _ServiceConfig(), ai_batch=10)
        self.test_engine = _IdentityThenTranslationEngine()

    def _build_engine(self, context="", prompt_type="mods"):
        del context, prompt_type
        return self.test_engine


class _GoogleRetryService(_RetryingTranslationService):
    def __init__(self, cache) -> None:
        super().__init__(cache)
        self.engine_name = "google"


class _StrictGoogleEngine:
    modes = []

    def __init__(self, workers=5, mode="single") -> None:
        del workers
        self.modes.append(mode)

    def translate_batch(self, items, _target_lang, _callbacks):
        return {key: "Эпоха пара" for key in items}


class IdentityRetryRegressionTests(unittest.TestCase):
    def test_google_identity_retry_switches_to_single_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = _GoogleRetryService(
                TranslationCache(str(Path(temp_dir) / "cache.json"))
            )
            _StrictGoogleEngine.modes.clear()
            with mock.patch(
                "mineai.engines.service.GoogleEngine",
                _StrictGoogleEngine,
            ):
                result = service.translate_dict(
                    {"title": "Steam Age"},
                    RUSSIAN,
                    _callbacks(),
                )

            self.assertEqual(_StrictGoogleEngine.modes, ["single"])
            self.assertEqual(result["title"], "Эпоха пара")

    def test_technical_fragments_never_reach_translation_engine(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = _RetryingTranslationService(
                TranslationCache(str(Path(temp_dir) / "ai_cache.json"))
            )
            result = service.translate_dict(
                {"suffix": "'s", "quantity": "1x"},
                RUSSIAN,
                _callbacks(),
            )

            self.assertEqual(service.test_engine.calls, 0)
            self.assertEqual(result, {"suffix": "'s", "quantity": "1x"})

    def test_meaningful_identity_response_is_retried_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = _RetryingTranslationService(
                TranslationCache(str(Path(temp_dir) / "ai_cache.json"))
            )
            result = service.translate_dict(
                {"title": "Steam Age", "item": "VoidShimmer Goo"},
                RUSSIAN,
                _callbacks(),
                context="Book titles",
                prompt_type="books",
            )

            self.assertEqual(service.test_engine.calls, 2)
            self.assertEqual(result["title"], "Эпоха пара")
            self.assertEqual(result["item"], "Мерцающая слизь Пустоты")


if __name__ == "__main__":
    unittest.main()
