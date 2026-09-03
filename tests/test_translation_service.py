import os
import json
import tempfile
import unittest
from unittest import mock

_original_cwd = os.getcwd()
with tempfile.TemporaryDirectory() as _import_cwd:
    os.chdir(_import_cwd)
    try:
        from mineai.cache import TranslationCache
        from mineai.engines.base import EngineCallbacks, TranslationEngine
        from mineai.engines.google import GoogleEngine
        from mineai.engines.service import TranslationService
        from mineai.text_processing import is_technical_term, polish_translation
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

    def save(self):
        pass


class _Engine(TranslationEngine):
    def __init__(self, response_factory):
        self.response_factory = response_factory
        self.calls = []

    def translate_batch(self, items, target_lang, callbacks):
        self.calls.append(dict(items))
        return self.response_factory(items)


class _Service(TranslationService):
    def __init__(
        self,
        engine,
        cache,
        config,
        *,
        engine_name="ai",
        fallback_caches=None,
        force_google_fallback=False,
    ):
        super().__init__(
            engine_name,
            cache,
            config,
            ai_batch=20,
            fallback_caches=fallback_caches,
            force_google_fallback=force_google_fallback,
        )
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
    def test_duplicate_formatkit_units_are_sent_once_and_reused(self):
        engine = _Engine(
            lambda items: {
                next(iter(items)): "Раздвижные двери",
            }
        )
        sources = {
            "json:/pages/0/title": "Sliding Doors",
            "json:/pages/1/title": "Sliding Doors",
        }

        result = _Service(
            engine,
            _MemoryCache(),
            _Config(),
        ).translate_dict(
            sources,
            TARGET_LANG,
            _callbacks([]),
        )

        self.assertEqual(
            result,
            {
                "json:/pages/0/title": "Раздвижные двери",
                "json:/pages/1/title": "Раздвижные двери",
            },
        )
        self.assertEqual(len(engine.calls), 1)
        self.assertEqual(set(engine.calls[0]), {"json:/pages/0/title"})

    def test_numeric_and_numeric_unit_values_never_reach_any_engine(self):
        for engine_name in ("ai", "google"):
            with self.subTest(engine=engine_name):
                engine = _Engine(
                    lambda _items: self.fail("numeric values must be restored locally")
                )
                sources = {
                    "integer": "123",
                    "grouped": "3,000",
                    "decimal": "-12.5%",
                    "memory": "1 MB",
                    "fluid": "520mB",
                }

                result = _Service(
                    engine,
                    _MemoryCache(),
                    _Config(),
                    engine_name=engine_name,
                ).translate_dict(
                    sources,
                    TARGET_LANG,
                    _callbacks([]),
                )

                self.assertEqual(result, sources)
                self.assertEqual(engine.calls, [])

    def test_numbers_inside_prose_are_masked_and_restored_exactly(self):
        source = "Runs for 3,000 ticks at 80% efficiency."

        def translate(items):
            item = next(iter(items.values()))
            self.assertNotIn("3,000", item.masked)
            self.assertNotIn("80%", item.masked)
            return {
                item.key: item.masked.replace("Runs for", "Работает")
                .replace("ticks at", "тиков с")
                .replace("efficiency", "эффективностью")
            }

        result = _Service(
            _Engine(translate),
            _MemoryCache(),
            _Config(),
        ).translate_dict(
            {"description": source},
            TARGET_LANG,
            _callbacks([]),
        )

        self.assertIn("3,000", result["description"])
        self.assertIn("80%", result["description"])

    def test_translation_cannot_invent_an_additional_number(self):
        source = "Runs at 80% load."
        engine = _Engine(
            lambda items: {
                next(iter(items)): "Работает при 80% нагрузке в 9000 циклов."
            }
        )

        result = _Service(
            engine,
            _MemoryCache(),
            _Config(),
        ).translate_dict(
            {"description": source},
            TARGET_LANG,
            _callbacks([]),
        )

        self.assertEqual(result, {"description": source})

    def test_literal_numbered_marker_does_not_break_markdown_validation(self):
        source = "Read [Guide](guide.md) and keep [#7#]."
        translated = "Читайте [Руководство](guide.md) и сохраните [#7#]."
        engine = _Engine(lambda items: {next(iter(items)): translated})

        result = _Service(
            engine,
            _MemoryCache(),
            _Config(),
        ).translate_dict(
            {"description": source},
            TARGET_LANG,
            _callbacks([]),
        )

        self.assertEqual(result, {"description": translated})

    def test_cache_recovery_uses_valid_google_cache_after_invalid_ai_cache(self):
        source = "Power: %s"
        ai_cache = _MemoryCache()
        google_cache = _MemoryCache()
        ai_cache.values[("ru", source)] = "Мощность:"
        google_cache.values[("ru", source)] = "Мощность: %s"
        engine = _Engine(lambda _items: self.fail("local AI must be skipped"))
        logs = []

        result = _Service(
            engine,
            ai_cache,
            _Config(),
            fallback_caches=[("Google-кэш", google_cache)],
        ).translate_dict(
            {"key": source},
            TARGET_LANG,
            _callbacks(logs),
        )

        self.assertEqual(result, {"key": "Мощность: %s"})
        self.assertEqual(ai_cache.discarded, [("ru", source, False)])
        self.assertEqual(ai_cache.values[("ru", source)], "Мощность: %s")
        self.assertEqual(engine.calls, [])
        self.assertTrue(any("Google-кэш" in message for message, _ in logs))

    def test_cache_recovery_forces_google_fallback_after_local_ai_failure(self):
        source = "Herbalist Bench"
        google = mock.Mock()
        google.translate_batch.return_value = {"key": "Стол травника"}

        with mock.patch(
            "mineai.engines.service.GoogleEngine",
            return_value=google,
        ):
            result = _Service(
                _Engine(lambda _items: {}),
                _MemoryCache(),
                _Config(fallback_google=False),
                force_google_fallback=True,
            ).translate_dict(
                {"key": source},
                TARGET_LANG,
                _callbacks([]),
            )

        self.assertEqual(result, {"key": "Стол травника"})
        google.translate_batch.assert_called_once()

    def test_modonomicon_color_tokens_are_atomic_for_ai_and_google(self):
        source = "[#] (8B0000)Blood Wood[#] () is powerful."
        translated = "[#] (8B0000)Кровавая древесина[#] () очень прочна."

        for engine_name in ("ai", "google"):
            with self.subTest(engine=engine_name):
                def translate(items):
                    item = next(iter(items.values()))
                    self.assertNotIn("8B0000", item.masked)
                    self.assertNotIn("[#]", item.masked)
                    return {item.key: translated}

                result = _Service(
                    _Engine(translate),
                    _MemoryCache(),
                    _Config(),
                    engine_name=engine_name,
                ).translate_dict(
                    {"entry": source},
                    TARGET_LANG,
                    _callbacks([]),
                    prompt_type="books",
                )

                self.assertEqual(result, {"entry": translated})

    def test_google_finalizer_restores_source_boundary_newline(self):
        source = "Description\r\n"
        masked, mapping = __import__(
            "mineai.text_processing",
            fromlist=["mask_protected_fragments"],
        ).mask_protected_fragments(source)
        item = __import__(
            "mineai.engines.base",
            fromlist=["EngineItem"],
        ).EngineItem("entry", source, masked, mapping)
        raw = masked.replace("Description", "Описание")

        result = GoogleEngine(workers=1, mode="single")._finalize(raw, item)

        self.assertEqual(result, "Описание\r\n")

    def test_article_only_technical_label_is_valid_for_all_engines(self):
        for engine_name in ("ai", "google"):
            with self.subTest(engine=engine_name):
                engine = _Engine(lambda items: {next(iter(items)): "UI"})
                result = _Service(
                    engine,
                    _MemoryCache(),
                    _Config(),
                    engine_name=engine_name,
                ).translate_dict(
                    {"label": "The UI"},
                    TARGET_LANG,
                    _callbacks([]),
                    prompt_type="books",
                )

                self.assertEqual(result, {"label": "UI"})

    def test_russian_article_and_punctuation_fragment_may_lose_all_letters(self):
        for engine_name in ("ai", "google"):
            with self.subTest(engine=engine_name):
                engine = _Engine(lambda items: {next(iter(items)): "."})
                result = _Service(
                    engine,
                    _MemoryCache(),
                    _Config(),
                    engine_name=engine_name,
                ).translate_dict(
                    {"fragment": ". The"},
                    TARGET_LANG,
                    _callbacks([]),
                    prompt_type="books",
                )

                self.assertEqual(result, {"fragment": "."})

    def test_russian_article_rule_cannot_erase_entire_value(self):
        engine = _Engine(lambda items: {next(iter(items)): ""})

        result = _Service(engine, _MemoryCache(), _Config()).translate_dict(
            {"fragment": "The"},
            TARGET_LANG,
            _callbacks([]),
            prompt_type="books",
        )

        self.assertEqual(result, {"fragment": "The"})

    def test_formatted_translation_never_sends_markup_or_markers_to_engine(self):
        source = (
            "$(#BB00BB)Epic$() items and "
            '<ItemLink id="ae2:controller" /> are useful.'
        )

        def translate(items):
            exposed = " ".join(item.original for item in items.values())
            self.assertNotIn("$(#", exposed)
            self.assertNotIn("ItemLink", exposed)
            self.assertNotIn("[#", exposed)
            return {
                key: (
                    item.original.replace("Epic", "Эпические")
                    .replace("items and", "предметы и")
                    .replace("are useful", "полезны")
                )
                for key, item in items.items()
            }

        engine = _Engine(translate)
        result = _Service(engine, _MemoryCache(), _Config()).translate_formatted_dict(
            {"page": source},
            TARGET_LANG,
            _callbacks([]),
            context="demo/page.md",
            prompt_type="books",
        )

        self.assertIn("$(#BB00BB)", result["page"])
        self.assertIn('<ItemLink id="ae2:controller" />', result["page"])
        self.assertNotIn("[#", result["page"])

    def test_formatted_translation_sends_only_visible_nodes_around_markup(self):
        source = "The $(item) Augmenting Table is used for upgrades."

        def translate(items):
            self.assertEqual(len(items), 2)
            exposed = "".join(item.original for item in items.values())
            self.assertNotIn("$(item)", exposed)
            self.assertNotIn("⟦FK", exposed)
            return {
                key: (
                    "Стол"
                    if item.original.strip() == "The"
                    else "используется для улучшений."
                )
                for key, item in items.items()
            }

        engine = _Engine(translate)
        result = _Service(engine, _MemoryCache(), _Config()).translate_formatted_dict(
            {"page": source},
            TARGET_LANG,
            _callbacks([]),
            context="demo/page.json",
            prompt_type="books",
        )

        self.assertEqual(
            result["page"],
            "Стол $(item) используется для улучшений.",
        )

    def test_formatted_validation_does_not_join_text_across_anchors(self):
        source = "The value is zero.$(p)This addon changes the default."

        def translate(items):
            return {
                item.key: item.original.replace(
                    "The value is zero.",
                    "Значение равно нулю.",
                ).replace(
                    "This addon changes the default.",
                    "Этот аддон изменяет значение по умолчанию.",
                )
                for item in items.values()
            }

        result = _Service(
            _Engine(translate),
            _MemoryCache(),
            _Config(),
        ).translate_formatted_dict(
            {"page": source},
            TARGET_LANG,
            _callbacks([]),
            context="demo/page.json",
            prompt_type="books",
        )

        self.assertEqual(
            result["page"],
            "Значение равно нулю.$(p)Этот аддон изменяет значение по умолчанию.",
        )

    def test_failed_long_formatted_node_is_retried_in_smaller_segments(self):
        sentence = "This sentence explains the machine clearly. "
        source = (sentence * 20).rstrip()

        def translate(items):
            if not all("::segment::" in key for key in items):
                return {}
            return {
                key: item.original.replace(
                    "This sentence explains the machine clearly.",
                    "Это предложение понятно объясняет работу машины.",
                )
                for key, item in items.items()
            }

        result = _Service(
            _Engine(translate),
            _MemoryCache(),
            _Config(),
        ).translate_formatted_dict(
            {"page": source},
            TARGET_LANG,
            _callbacks([]),
            context="demo/large_page.json",
            prompt_type="books",
        )

        self.assertEqual(
            result["page"],
            ("Это предложение понятно объясняет работу машины. " * 20).rstrip(),
        )

    def test_formatted_cache_is_scoped_by_page_and_node(self):
        cache = _MemoryCache()
        engine = _Engine(
            lambda items: {
                key: "Перевод " + item.original
                for key, item in items.items()
            }
        )
        service = _Service(engine, cache, _Config())

        service.translate_formatted_dict(
            {"page-a": "Open [Guide](a.md).", "page-b": "Open [Guide](b.md)."},
            TARGET_LANG,
            _callbacks([]),
            context="demo",
            prompt_type="books",
        )

        scoped_sources = [source for language, source in cache.values]
        self.assertTrue(all(language == "ru" for language, _ in cache.values))
        self.assertEqual(len(scoped_sources), len(set(scoped_sources)))
        self.assertTrue(any("page-a" in source for source in scoped_sources))
        self.assertTrue(any("page-b" in source for source in scoped_sources))

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

    def test_success_log_keeps_full_source_and_translation_text(self):
        source = (
            "A deliberately long source sentence that exceeds forty characters "
            "and must remain complete in the application journal"
        )
        translated = (
            "Это намеренно длинная переведённая строка длиннее сорока символов, "
            "которая должна полностью отображаться в журнале приложения"
        )
        engine = _Engine(lambda items: {next(iter(items)): translated})
        logs = []
        result = _Service(engine, _MemoryCache(), _Config()).translate_dict(
            {"key": source},
            TARGET_LANG,
            _callbacks(logs),
        )

        self.assertEqual(result, {"key": translated})
        self.assertIn((f" > {source} -> {translated}", "dim"), logs)

    def test_rejection_log_keeps_full_source_and_candidate_text(self):
        source = "A deliberately invalid long source " * 8
        candidate = "still untranslated invalid candidate " * 8
        logs = []
        _Service(
            _Engine(lambda items: {next(iter(items)): candidate}),
            _MemoryCache(),
            _Config(),
        ).translate_dict(
            {"key": source},
            TARGET_LANG,
            _callbacks(logs),
        )

        rejection = next(message for message, _tag in logs if "Отклонён" in message)
        self.assertIn(repr(source), rejection)
        self.assertIn(repr(candidate), rejection)

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

    def test_cached_translation_with_extra_newline_is_discarded_and_retranslated(self):
        cache = _MemoryCache()
        cache.values[("ru", "Engineer's Crafting Table")] = (
            "Верстак инженера\nДополнение"
        )
        engine = _Engine(lambda items: {next(iter(items)): "Верстак инженера"})

        result = _Service(engine, cache, _Config()).translate_dict(
            {"key": "Engineer's Crafting Table"},
            TARGET_LANG,
            _callbacks([]),
        )

        self.assertEqual(result, {"key": "Верстак инженера"})
        self.assertEqual(
            cache.discarded,
            [("ru", "Engineer's Crafting Table", False)],
        )
        self.assertEqual(len(engine.calls), 1)

    def test_repaired_ai_cache_entry_is_persisted_immediately(self):
        source = (
            "The grid fills itself when the items are ready, meaning you do "
            "not have to keep checking if the items are available."
        )
        incomplete = (
            "Сетка заполняется автоматически, meaning you do not have to "
            "keep checking if the items are available."
        )
        fixed = (
            "Сетка заполняется автоматически, когда предметы готовы, поэтому "
            "не нужно постоянно проверять их наличие."
        )

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "ai_cache.json")
            cache = TranslationCache(path)
            cache.set("ru", source, incomplete)
            cache.save()
            engine = _Engine(lambda items: {next(iter(items)): fixed})

            result = _Service(engine, cache, _Config()).translate_dict(
                {"key": source},
                TARGET_LANG,
                _callbacks([]),
            )

            self.assertEqual(result, {"key": fixed})
            self.assertEqual(
                TranslationCache(path).get("ru", source),
                (fixed, False),
            )

    def test_cached_translation_with_wrong_inline_markdown_is_retranslated(self):
        source = "find the recipe again, and click move *again*."
        cache = _MemoryCache()
        cache.values[("ru", source)] = (
            "С **ae2helpers** вы просто запрашиваете крафт."
        )
        engine = _Engine(
            lambda items: {
                next(iter(items)): "Найдите рецепт и нажмите *снова*."
            }
        )

        result = _Service(engine, cache, _Config()).translate_dict(
            {"key": source},
            TARGET_LANG,
            _callbacks([]),
        )

        self.assertEqual(
            result,
            {"key": "Найдите рецепт и нажмите *снова*."},
        )
        self.assertEqual(cache.discarded, [("ru", source, False)])
        self.assertEqual(len(engine.calls), 1)

    def test_reordered_patchouli_codes_are_rejected(self):
        source = "$(#BB00BB)Epic$() items use $(#5555FF)Simple$() tables."
        broken = "$(#5555FF)Эпические$() предметы используют $(#BB00BB)простые$() столы."
        fixed = "$(#BB00BB)Эпические$() предметы используют $(#5555FF)простые$() столы."
        cache = _MemoryCache()
        cache.values[("ru", source)] = broken
        engine = _Engine(lambda items: {next(iter(items)): fixed})

        result = _Service(engine, cache, _Config()).translate_dict(
            {"key": source}, TARGET_LANG, _callbacks([])
        )

        self.assertEqual(result, {"key": fixed})
        self.assertEqual(cache.discarded, [("ru", source, False)])

    def test_invented_patchouli_code_is_rejected(self):
        source = "The refinery produces two millibuckets (2mB) per tick."
        broken = "Переработчик производит два миллибакета $(2mB) за тик."
        engine = _Engine(lambda items: {next(iter(items)): broken})

        result = _Service(engine, _MemoryCache(), _Config()).translate_dict(
            {"key": source}, TARGET_LANG, _callbacks([])
        )

        self.assertEqual(result, {"key": source})

    def test_extreme_cached_length_mismatch_is_retranslated(self):
        source = (
            "When fully upgraded, the machine exports items to every connected "
            "inventory automatically."
        )
        cache = _MemoryCache()
        cache.values[("ru", source)] = "Экспорт"
        engine = _Engine(
            lambda items: {
                next(iter(items)): (
                    "После полного улучшения машина автоматически экспортирует "
                    "предметы во все подключённые хранилища."
                )
            }
        )

        result = _Service(engine, cache, _Config()).translate_dict(
            {"key": source}, TARGET_LANG, _callbacks([])
        )

        self.assertNotEqual(result["key"], "Экспорт")
        self.assertEqual(cache.discarded, [("ru", source, False)])

    def test_protected_only_item_quantity_can_remain_identical(self):
        source = '1x <ItemLink id="energy_acceptor" />'
        cache = _MemoryCache()
        engine = _Engine(lambda items: {next(iter(items)): source})

        result = _Service(engine, cache, _Config()).translate_dict(
            {"key": source}, TARGET_LANG, _callbacks([])
        )

        self.assertEqual(result, {"key": source})
        self.assertIn(("ru", source), cache.identities)

    def test_direct_google_rejects_reordered_protected_link_fragments(self):
        source = "Read [Guide](guide.md)."
        broken = "](guide.md)Читайте руководство[."
        engine = _Engine(lambda items: {next(iter(items)): broken})

        result = _Service(
            engine,
            _MemoryCache(),
            _Config(),
            engine_name="google",
        ).translate_dict({"key": source}, TARGET_LANG, _callbacks([]))

        self.assertEqual(result, {"key": source})


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

    def test_format_validator_rejection_uses_google_fallback(self):
        source = "Text with protected anchors"
        primary = _Engine(
            lambda items: {next(iter(items)): "Русский, но сломанный"}
        )
        google = mock.Mock()
        google.translate_batch.return_value = {
            "key": "Корректный русский перевод"
        }

        with mock.patch(
            "mineai.engines.service.GoogleEngine",
            return_value=google,
        ):
            result = _Service(
                primary,
                _MemoryCache(),
                _Config(fallback_google=True),
            ).translate_dict(
                {"key": source},
                TARGET_LANG,
                _callbacks([]),
                candidate_validators={
                    "key": lambda candidate: (
                        "структура изменена"
                        if candidate == "Русский, но сломанный"
                        else None
                    )
                },
            )

        self.assertEqual(result, {"key": "Корректный русский перевод"})
        google.translate_batch.assert_called_once()

    def test_residual_english_word_gets_strict_retry_before_google(self):
        source = "A Blaze Burner is useful"

        class ResidualEngine(TranslationEngine):
            def __init__(self) -> None:
                self.calls = 0

            def translate_batch(self, items, target_lang, callbacks):
                self.calls += 1
                key = next(iter(items))
                return {
                    key: (
                        "Горелка Blaze полезна"
                        if self.calls == 1
                        else "Полезная горелка"
                    )
                }

        engine = ResidualEngine()
        result = _Service(
            engine,
            _MemoryCache(),
            _Config(fallback_google=False),
        ).translate_dict(
            {"key": source},
            TARGET_LANG,
            _callbacks([]),
        )

        self.assertEqual(result, {"key": "Полезная горелка"})
        self.assertEqual(engine.calls, 2)

    def test_format_validator_rejection_gets_strict_local_retry(self):
        calls = 0

        def translate(items):
            nonlocal calls
            calls += 1
            return {
                next(iter(items)): (
                    "Русский, но сломанный"
                    if calls == 1
                    else "Корректный русский перевод"
                )
            }

        result = _Service(
            _Engine(translate),
            _MemoryCache(),
            _Config(fallback_google=False),
        ).translate_dict(
            {"key": "Text with protected anchors"},
            TARGET_LANG,
            _callbacks([]),
            candidate_validators={
                "key": lambda candidate: (
                    "FormatKit: структура изменена"
                    if candidate == "Русский, но сломанный"
                    else None
                )
            },
        )

        self.assertEqual(result, {"key": "Корректный русский перевод"})
        self.assertEqual(calls, 2)

    def test_format_validator_rejection_gets_strict_google_retry(self):
        source = "Text with protected anchors"
        primary = _Engine(
            lambda items: {next(iter(items)): "Русский, но сломанный"}
        )
        retry = mock.Mock()
        retry.translate_batch.return_value = {
            "key": "Корректный русский перевод"
        }

        with mock.patch(
            "mineai.engines.service.GoogleEngine",
            return_value=retry,
        ):
            result = _Service(
                primary,
                _MemoryCache(),
                _Config(fallback_google=False),
                engine_name="google",
            ).translate_dict(
                {"key": source},
                TARGET_LANG,
                _callbacks([]),
                candidate_validators={
                    "key": lambda candidate: (
                        "FormatKit: структура изменена"
                        if candidate == "Русский, но сломанный"
                        else None
                    )
                },
            )

        self.assertEqual(result, {"key": "Корректный русский перевод"})
        retry.translate_batch.assert_called_once()

    def test_anchor_rich_formatkit_blocks_use_small_batches_for_all_engines(self):
        strings = {
            f"key-{index}": f"Source {index} ⟦FK0000⟧ description"
            for index in range(12)
        }
        validators = {key: lambda _candidate: None for key in strings}

        for engine_name in ("ai", "google"):
            with self.subTest(engine=engine_name):
                engine = _Engine(
                    lambda items: {
                        key: item.original.replace("Source", "Источник").replace(
                            "description",
                            "описание",
                        )
                        for key, item in items.items()
                    }
                )
                result = _Service(
                    engine,
                    _MemoryCache(),
                    _Config(),
                    engine_name=engine_name,
                ).translate_dict(
                    strings,
                    TARGET_LANG,
                    _callbacks([]),
                    candidate_validators=validators,
                )

                self.assertEqual(len(result), len(strings))
                self.assertLessEqual(max(map(len, engine.calls)), 5)

    def test_failed_anchor_block_is_retranslated_by_visible_segments(self):
        source = "First sentence.⟦FK0000⟧Second sentence."
        expected = "Первое предложение.⟦FK0000⟧Второе предложение."

        def translate(items):
            if all("::segment::" in key for key in items):
                values = {
                    "First sentence.": "Первое предложение.",
                    "Second sentence.": "Второе предложение.",
                }
                return {
                    key: values[item.original]
                    for key, item in items.items()
                }
            return {next(iter(items)): "Перевод без структурного якоря"}

        result = _Service(
            _Engine(translate),
            _MemoryCache(),
            _Config(fallback_google=False),
        ).translate_dict(
            {"key": source},
            TARGET_LANG,
            _callbacks([]),
            candidate_validators={
                "key": lambda candidate: (
                    None if candidate == expected else "FormatKit: структура"
                )
            },
        )

        self.assertEqual(result, {"key": expected})

    def test_scientific_binomial_link_label_is_intentional_identity(self):
        source = "⟦FK0000⟧Xylocopa aerata]"
        engine = _Engine(lambda _items: self.fail("engine must be skipped"))

        result = _Service(engine, _MemoryCache(), _Config()).translate_dict(
            {"key": source},
            TARGET_LANG,
            _callbacks([]),
        )

        self.assertEqual(result, {"key": source})
        self.assertEqual(engine.calls, [])

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

    def test_sentence_word_with_trailing_period_is_not_a_technical_term(self):
        self.assertFalse(is_technical_term("dimensions."))
        self.assertFalse(is_technical_term("energy."))
        self.assertTrue(is_technical_term("guide.md"))

    def test_code_identifiers_and_compound_technical_terms_are_ignored(self):
        for value in (
            "getAreaTypes()",
            "setCount()",
            "#header",
            "#tier#",
            "#mana_cost#",
            "NBT UI",
            "XNet",
            "CuBee",
        ):
            with self.subTest(value=value):
                self.assertTrue(is_technical_term(value))

        self.assertFalse(is_technical_term("Pocket Computer Addons"))

    def test_polish_translation_preserves_angle_tag_with_style_like_prefix(self):
        self.assertEqual(
            polish_translation("Список топлива <&list>"),
            "Список топлива <&list>",
        )


class TranslationCacheIdentityTests(unittest.TestCase):
    def test_any_ai_cache_version_preserves_valid_entries_during_upgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "ai_cache.json")
            with open(path, "w", encoding="utf-8") as stream:
                json.dump(
                    {
                        "__mineai_ai_cache_validation_version__": "1",
                        "ru_Description": "Описание",
                        "ru_Power: %s": "Мощность:",
                    },
                    stream,
                    ensure_ascii=False,
                )

            cache = TranslationCache(path)

            self.assertEqual(
                cache.get("ru", "Description"),
                ("Описание", False),
            )
            self.assertTrue(os.path.exists(path + ".pre-auto-repair"))

    def test_old_ai_cache_keeps_individually_valid_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "ai_cache.json")
            with open(path, "w", encoding="utf-8") as stream:
                json.dump(
                    {
                        "__mineai_ai_cache_validation_version__": "26",
                        "ru_Exactly one": "не более одного",
                    },
                    stream,
                    ensure_ascii=False,
                )

            cache = TranslationCache(path)

            self.assertEqual(
                cache.get("ru", "Exactly one"),
                ("не более одного", False),
            )
            self.assertTrue(os.path.exists(path + ".pre-auto-repair"))

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

    def test_scoped_identity_returns_only_the_original_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "ai_cache.json")
            cache = TranslationCache(path)
            scoped_source = "␞book|page|part-1␟1 MB"
            cache.set_identity("ru", scoped_source)

            self.assertEqual(
                cache.get("ru", scoped_source),
                ("1 MB", False),
            )


if __name__ == "__main__":
    unittest.main()
