import os
import tempfile
import unittest
from unittest import mock

_original_cwd = os.getcwd()
with tempfile.TemporaryDirectory() as _import_cwd:
    os.chdir(_import_cwd)
    try:
        from mineai.cache import TranslationCache
        from mineai.engines.base import EngineCallbacks, TranslationEngine
        from mineai.engines.service import TranslationService
        from mineai.text_processing import is_technical_term
    finally:
        os.chdir(_original_cwd)


TARGET_LANG = {
    "api": "ru",
    "name": "Russian",
    "regex": r"[А-Яа-яЁё]",
}


class _Config:
    def __init__(self, fallback_google=False):
        self.fallback_google = fallback_google

    def getboolean(self, section, key):
        if (section, key) == ("GENERAL", "smart_glue"):
            return False
        if (section, key) == ("AI", "fallback_google"):
            return self.fallback_google
        raise AssertionError((section, key))

    def getint(self, _section, _key, fallback=0):
        return fallback


class _MemoryCache:
    def __init__(self):
        self.values = {}
        self.identities = set()
        self.discarded = []

    def get(self, api_code, source):
        key = (api_code, source)
        if key in self.values:
            return self.values[key], False
        if key in self.identities:
            return source, False
        return None, False

    def set(self, api_code, source, translated):
        key = (api_code, source)
        self.identities.discard(key)
        self.values[key] = translated

    def set_identity(self, api_code, source):
        key = (api_code, source)
        self.values.pop(key, None)
        self.identities.add(key)

    def discard(self, api_code, source, *, include_imported=False):
        key = (api_code, source)
        self.values.pop(key, None)
        self.identities.discard(key)
        self.discarded.append((api_code, source, include_imported))

    def save_if_threshold(self):
        pass


class _Engine(TranslationEngine):
    def __init__(self, response_factory):
        self.response_factory = response_factory
        self.calls = []

    def translate_batch(self, items, target_lang, callbacks):
        self.calls.append(dict(items))
        return self.response_factory(items)


class _Service(TranslationService):
    def __init__(self, engine, cache, config):
        super().__init__("ai", cache, config, ai_batch=20)
        self.engine = engine

    def _build_engine(self, context="", prompt_type="mods"):
        return self.engine


def _callbacks(logs, progress=None):
    progress = progress if progress is not None else []
    return EngineCallbacks(
        should_run=lambda: True,
        wait_if_paused=lambda: None,
        on_log=lambda message, tag: logs.append((message, tag)),
        on_status=lambda _message: None,
        on_progress=lambda count: progress.append(count),
    )


class TranslationServiceRegressionTests(unittest.TestCase):
    def test_identical_sources_are_sent_to_engine_once(self):
        engine = _Engine(lambda items: {next(iter(items)): "Список"})
        logs, progress = [], []
        result = _Service(engine, _MemoryCache(), _Config()).translate_dict(
            {"a": "Unordered List", "b": "Unordered List"},
            TARGET_LANG,
            _callbacks(logs, progress),
        )
        self.assertEqual(list(engine.calls[0]), ["a"])
        self.assertEqual(result, {"a": "Список", "b": "Список"})
        self.assertEqual(sum(progress), 2)
        self.assertTrue(any("объединены: 1" in msg for msg, _ in logs))

    def test_short_technical_identity_is_not_retranslated(self):
        cache = _MemoryCache()
        first = _Engine(lambda items: {next(iter(items)): "RF"})
        _Service(first, cache, _Config()).translate_dict(
            {"key": "RF"}, TARGET_LANG, _callbacks([])
        )
        self.assertIn(("ru", "RF"), cache.identities)

        second = _Engine(lambda _items: self.fail("engine must be skipped"))
        logs = []
        result = _Service(second, cache, _Config()).translate_dict(
            {"key": "RF"}, TARGET_LANG, _callbacks(logs)
        )
        self.assertEqual(result, {"key": "RF"})
        self.assertEqual(second.calls, [])
        self.assertTrue(any("Из кэша: 1" in msg for msg, _ in logs))

    def test_invalid_cached_placeholder_is_discarded(self):
        cache = _MemoryCache()
        cache.values[("ru", "Power: %s")] = "Мощность:"
        engine = _Engine(lambda items: {next(iter(items)): "Мощность: %s"})
        logs = []
        result = _Service(engine, cache, _Config()).translate_dict(
            {"key": "Power: %s"}, TARGET_LANG, _callbacks(logs)
        )
        self.assertEqual(result, {"key": "Мощность: %s"})
        self.assertEqual(cache.discarded, [("ru", "Power: %s", False)])
        self.assertTrue(any("кэша отброшена" in msg for msg, _ in logs))

    def test_google_fallback_rejection_is_logged(self):
        source = "Original value"
        google = mock.Mock()
        google.translate_batch.return_value = {"key": source}
        logs = []
        with mock.patch(
            "mineai.engines.service.GoogleEngine", return_value=google
        ):
            result = _Service(
                _Engine(lambda _items: {}),
                _MemoryCache(),
                _Config(fallback_google=True),
            ).translate_dict({"key": source}, TARGET_LANG, _callbacks(logs))
        self.assertEqual(result, {"key": source})
        self.assertTrue(any(
            "Google fallback: ответ совпадает" in msg for msg, _ in logs
        ))
        self.assertTrue(any("не принято 1 строк" in msg for msg, _ in logs))
        self.assertTrue(any("Строка не переведена" in msg for msg, _ in logs))

    def test_russian_candidate_with_cjk_is_rejected(self):
        source = "Villager Egg Drop Chance"
        engine = _Engine(
            lambda items: {next(iter(items)): "Вероятность яйца村民"}
        )
        logs = []
        result = _Service(engine, _MemoryCache(), _Config()).translate_dict(
            {"key": source}, TARGET_LANG, _callbacks(logs)
        )
        self.assertEqual(result, {"key": source})
        self.assertTrue(any("CJK-символы" in msg for msg, _ in logs))

    def test_ignore_terms_are_case_insensitive(self):
        self.assertTrue(is_technical_term(" RF "))
        self.assertTrue(is_technical_term("gui"))
        self.assertFalse(is_technical_term("Iron"))


class TranslationCacheIdentityTests(unittest.TestCase):
    def test_identity_survives_reload_and_normalizes_newlines(self):
        with tempfile.TemporaryDirectory() as directory:
            previous_cwd = os.getcwd()
            os.chdir(directory)
            try:
                path = os.path.join(directory, "ai_cache.json")
                cache = TranslationCache(path)
                cache.set_identity("ru", "RF\r\n/t")
                cache.save()
                self.assertEqual(
                    TranslationCache(path).get("ru", "RF\n/t"),
                    ("RF\n/t", False),
                )
            finally:
                os.chdir(previous_cwd)


if __name__ == "__main__":
    unittest.main()
