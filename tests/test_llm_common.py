import json
import os
import tempfile
import unittest

import requests


# Importing the application creates default settings and dictionary files.
# Keep those import-time side effects outside the repository during tests.
_original_cwd = os.getcwd()
with tempfile.TemporaryDirectory() as _import_cwd:
    os.chdir(_import_cwd)
    try:
        from mineai.engines.base import EngineCallbacks, EngineItem
        from mineai.engines.llm_common import (
            BatchLlmEngine,
            build_translation_prompt,
            dump_ai_error,
            placeholders_match,
            repair_markers,
        )
        from mineai.engines.service import TranslationService
        from mineai.text_processing import mask_protected_fragments
    finally:
        os.chdir(_original_cwd)


TARGET_LANG = {"api": "ru", "name": "Russian", "regex": r"[А-Яа-яЁё]"}


def callbacks(
    *,
    should_run=lambda: True,
    wait_if_paused=lambda: None,
) -> EngineCallbacks:
    return EngineCallbacks(
        should_run=should_run,
        wait_if_paused=wait_if_paused,
        on_log=lambda _message, _tag: None,
        on_status=lambda _message: None,
    )


def prompt_payload(prompt: str) -> dict[str, str]:
    for marker in ("DATA:\n", "Data: ", "Данные: "):
        if marker in prompt:
            return json.loads(prompt.split(marker, 1)[1])
    raise AssertionError("Prompt does not contain a JSON payload marker")


class MemoryCache:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get(self, api_code: str, source: str) -> tuple[str | None, bool]:
        return self.values.get((api_code, source)), False

    def set(self, api_code: str, source: str, translated: str) -> None:
        self.values[(api_code, source)] = translated

    def save_if_threshold(self) -> None:
        pass


class ConfigWithoutSmartGlue:
    def getboolean(self, _section: str, _key: str) -> bool:
        return False


class ServiceWithEngine(TranslationService):
    def __init__(self, cache: MemoryCache, engine: BatchLlmEngine) -> None:
        super().__init__("ai", cache, ConfigWithoutSmartGlue())
        self.engine = engine

    # Добавили prompt_type сюда, чтобы тесты не ругались
    def _build_engine(self, context: str = "", prompt_type: str = "mods") -> BatchLlmEngine:
        return self.engine


class BatchLlmEngineTests(unittest.TestCase):
    def test_finalizer_restores_source_boundary_newline(self) -> None:
        source = "Description\r\n"
        masked, mapping = mask_protected_fragments(source)

        engine = BatchLlmEngine(
            call_api=lambda _prompt, _limit: json.dumps(
                {"entry": masked.replace("Description", "Описание")},
                ensure_ascii=False,
            )
        )
        item = EngineItem("entry", source, masked, mapping)

        result = engine.translate_batch(
            {"entry": item},
            TARGET_LANG,
            callbacks(),
        )

        self.assertEqual(result, {"entry": "Описание\r\n"})

    def test_ai_error_log_marks_failed_attempt_as_non_final(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            previous = os.getcwd()
            os.chdir(directory)
            try:
                dump_ai_error("Source", "Broken", "Markers changed")
                with open("ai_error_log.txt", encoding="utf-8-sig") as stream:
                    content = stream.read()
            finally:
                os.chdir(previous)

        self.assertIn("НЕУДАЧНАЯ ПОПЫТКА ИИ", content)
        self.assertIn("может быть исправлена повтором", content)

    def test_placeholder_order_must_match_source(self) -> None:
        self.assertFalse(
            placeholders_match("[#1#]Перевод[#0#]", "[#0#]Source[#1#]")
        )

    def test_suspicious_duplicate_batch_results_are_retried(self) -> None:
        calls: list[dict[str, str]] = []

        def call_api(prompt: str, _limit: int) -> str:
            payload = prompt_payload(prompt)
            calls.append(payload)
            if len(calls) == 1:
                duplicate = (
                    "Это ошибочно объединённый перевод двух разных длинных строк."
                )
                return json.dumps(
                    {key: duplicate for key in payload}, ensure_ascii=False
                )
            return json.dumps(
                {
                    key: (
                        "Первая строка переведена отдельно и корректно."
                        if key == "first"
                        else "Вторая строка переведена отдельно и корректно."
                    )
                    for key in payload
                },
                ensure_ascii=False,
            )

        engine = BatchLlmEngine(call_api=call_api, retries=1)
        items = {
            "first": EngineItem(
                "first",
                "The first independent sentence describes a crafting machine.",
                "The first independent sentence describes a crafting machine.",
            ),
            "second": EngineItem(
                "second",
                "The second independent sentence explains a wireless terminal.",
                "The second independent sentence explains a wireless terminal.",
            ),
        }

        result = engine.translate_batch(items, TARGET_LANG, callbacks())

        self.assertNotEqual(result["first"], result["second"])
        self.assertEqual(len(calls), 2)

    def test_repairs_added_newline_without_retrying_the_candidate(self) -> None:
        calls: list[dict[str, str]] = []

        def call_api(prompt: str, _max_tokens: int) -> str:
            payload = prompt_payload(prompt)
            calls.append(payload)
            if len(calls) == 1:
                return json.dumps(
                    {
                        "good": "Генератор",
                        "broken": "Верстак инженера\nДополнение",
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {"broken": "Верстак инженера"},
                ensure_ascii=False,
            )

        engine = BatchLlmEngine(call_api=call_api, retries=1)
        items = {
            "good": EngineItem("good", "Generator", "Generator"),
            "broken": EngineItem(
                "broken",
                "Engineer's Crafting Table",
                "Engineer's Crafting Table",
            ),
        }

        result = engine.translate_batch(items, TARGET_LANG, callbacks())

        self.assertEqual(
            result,
            {
                "good": "Генератор",
                "broken": "Верстак инженера Дополнение",
            },
        )
        self.assertEqual(
            [set(payload) for payload in calls],
            [{"good", "broken"}],
        )

    def test_hash_wrapped_template_variable_is_fully_protected(self) -> None:
        masked, mapping = mask_protected_fragments(
            "Mana cost: #mana_cost#"
        )

        self.assertEqual(masked, "Mana cost: [#0#]")
        self.assertEqual(mapping, {"[#0#]": "#mana_cost#"})

    def test_repairs_bare_numbered_marker_without_extra_request(self) -> None:
        calls = 0

        def call_api(_prompt: str, _max_tokens: int) -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                return json.dumps(
                    {"description": "Модуль.#0#Подробнее"},
                    ensure_ascii=False,
                )
            return "Module.[#0#]Details"

        engine = BatchLlmEngine(call_api=call_api, retries=1)
        items = {
            "description": EngineItem(
                "description",
                "Module.$(p)Details",
                "Module.[#0#]Details",
                {"[#0#]": "$(p)"},
            )
        }

        result = engine.translate_batch(items, TARGET_LANG, callbacks())

        self.assertEqual(result, {"description": "Модуль.$(p)Подробнее"})
        self.assertEqual(calls, 1)

    def test_marker_repair_rejects_rewritten_translation_text(self) -> None:
        repaired = repair_markers(
            lambda _prompt, _limit: "Original [#0#] text",
            "Original [#0#] text",
            "Перевод [#9#] текста",
            256,
        )

        self.assertIsNone(repaired)

    def test_retries_partially_untranslated_leading_article(self) -> None:
        calls: list[dict[str, str]] = []

        def call_api(prompt: str, _max_tokens: int) -> str:
            payload = prompt_payload(prompt)
            calls.append(payload)
            if len(calls) == 1:
                return json.dumps(
                    {"description": "The [#0#]Футляр для самоцветов[#1#]"},
                    ensure_ascii=False,
                )
            return json.dumps(
                {"description": "[#0#]Футляр для самоцветов[#1#]"},
                ensure_ascii=False,
            )

        engine = BatchLlmEngine(call_api=call_api, retries=1)
        items = {
            "description": EngineItem(
                "description",
                "The $(9)Gem Case$()",
                "The [#0#]Gem Case[#1#]",
                {"[#0#]": "$(9)", "[#1#]": "$()"},
            )
        }

        result = engine.translate_batch(items, TARGET_LANG, callbacks())

        self.assertEqual(result, {"description": "$(9)Футляр для самоцветов$()"})
        self.assertEqual(
            [set(payload) for payload in calls],
            [{"description"}, {"description"}],
        )

    def test_retries_unchanged_translatable_title_with_stricter_prompt(self) -> None:
        prompts: list[str] = []

        def call_api(prompt: str, _max_tokens: int) -> str:
            prompts.append(prompt)
            payload = prompt_payload(prompt)
            if len(prompts) == 1:
                return json.dumps(payload, ensure_ascii=False)
            return json.dumps(
                {"title": "Дополнения для карманного компьютера"},
                ensure_ascii=False,
            )

        engine = BatchLlmEngine(call_api=call_api, retries=1)
        items = {
            "title": EngineItem(
                "title",
                "Pocket Computer Addons",
                "Pocket Computer Addons",
            )
        }

        result = engine.translate_batch(items, TARGET_LANG, callbacks())

        self.assertEqual(
            result,
            {"title": "Дополнения для карманного компьютера"},
        )
        self.assertEqual(len(prompts), 2)
        self.assertIn("Do not copy ordinary source text unchanged", prompts[1])

    def test_safe_prompt_requires_all_numbered_placeholders(self) -> None:
        prompt = build_translation_prompt(
            {"key": "Requires [#0#] and [#1#]"},
            "Russian",
            mode="safe",
            context="",
        )

        self.assertIn("Preserve ALL [#N#] placeholders exactly", prompt)
        self.assertIn("MARKER WHITELIST", prompt)
        self.assertIn('"key": [#0#] [#1#]', prompt)
        self.assertIn("no skips, no renumbering, no repeats", prompt)

    def test_context_prompt_requires_all_numbered_placeholders(self) -> None:
        prompt = build_translation_prompt(
            {"key": "Requires [#0#] and [#1#]"},
            "Russian",
            mode="context",
            context="Example Mod",
        )

        self.assertIn("Preserve ALL [#N#] placeholders exactly", prompt)
        self.assertIn("MARKER WHITELIST", prompt)
        self.assertIn('"key": [#0#] [#1#]', prompt)
        self.assertIn("no skips, no renumbering, no repeats", prompt)

    def test_russian_book_prompt_contains_minecraft_term_hints(self) -> None:
        prompt = build_translation_prompt(
            {"title": "Copy Paste Gadget"},
            "Russian",
            mode="safe",
            context="Building Gadgets",
            prompt_type="books",
        )

        self.assertIn(
            "Copy Paste Gadget = Гаджет копирования и вставки",
            prompt,
        )
        self.assertIn(
            "Cut Paste Gadget = Гаджет вырезания и вставки",
            prompt,
        )

    def test_retries_only_a_missing_key(self) -> None:
        calls: list[dict[str, str]] = []

        def call_api(prompt: str, _max_tokens: int) -> str:
            payload = prompt_payload(prompt)
            calls.append(payload)
            if len(calls) == 1:
                return json.dumps({"first": "Первый"}, ensure_ascii=False)
            return json.dumps({"second": "Второй"}, ensure_ascii=False)

        engine = BatchLlmEngine(call_api=call_api)
        items = {
            "first": EngineItem("first", "First", "First"),
            "second": EngineItem("second", "Second", "Second"),
        }

        result = engine.translate_batch(items, TARGET_LANG, callbacks())

        self.assertEqual(result, {"first": "Первый", "second": "Второй"})
        self.assertEqual(
            [set(call) for call in calls],
            [{"first", "second"}, {"second"}],
        )

    def test_retries_only_the_value_with_a_lost_placeholder(self) -> None:
        calls: list[dict[str, str]] = []
        repair_calls: list[str] = []

        def call_api(prompt: str, _max_tokens: int) -> str:
            if "BROKEN TRANSLATION:" in prompt:
                repair_calls.append(prompt)
                # Модель снова вернула JSON вместо текста — гвард обязан отклонить.
                return json.dumps(
                    {"power": "Требуется [#0#] RF/t"}, ensure_ascii=False
                )
            payload = prompt_payload(prompt)
            calls.append(payload)
            if len(calls) == 1:
                return json.dumps(
                    {"power": "Требуется энергия", "title": "Генератор"},
                    ensure_ascii=False,
                )
            return json.dumps(
                {"power": "Требуется [#0#] RF/t"},
                ensure_ascii=False,
            )

        engine = BatchLlmEngine(call_api=call_api)
        items = {
            "power": EngineItem(
                "power",
                "Requires %s RF/t",
                "Requires [#0#] RF/t",
                {"[#0#]": "%s"},
            ),
            "title": EngineItem("title", "Generator", "Generator"),
        }
        result = engine.translate_batch(items, TARGET_LANG, callbacks())
        self.assertEqual(result["power"], "Требуется %s RF/t")
        self.assertEqual(result["title"], "Генератор")
        self.assertEqual(len(repair_calls), 1)
        self.assertEqual(set(calls[1]), {"power"})

    def test_marker_repair_rescues_a_good_translation(self) -> None:
        def call_api(prompt: str, _max_tokens: int) -> str:
            if "BROKEN TRANSLATION:" in prompt:
                return "Требуется [#0#] RF/t"
            return json.dumps({"power": "Требуется энергия"}, ensure_ascii=False)

        engine = BatchLlmEngine(call_api=call_api)
        items = {
            "power": EngineItem(
                "power",
                "Requires %s RF/t",
                "Requires [#0#] RF/t",
                {"[#0#]": "%s"},
            ),
        }
        result = engine.translate_batch(items, TARGET_LANG, callbacks())
        self.assertEqual(result["power"], "Требуется %s RF/t")

    def test_rejects_all_non_string_json_values(self) -> None:
        invalid_values = [None, 42, True, ["Перевод"], {"text": "Перевод"}]

        for invalid_value in invalid_values:
            with self.subTest(invalid_value=invalid_value):
                responses = iter(
                    [
                        json.dumps({"key": invalid_value}, ensure_ascii=False),
                        json.dumps({"key": "Перевод"}, ensure_ascii=False),
                    ]
                )
                engine = BatchLlmEngine(
                    call_api=lambda _prompt, _limit: next(responses)
                )
                items = {"key": EngineItem("key", "Translation", "Translation")}

                result = engine.translate_batch(items, TARGET_LANG, callbacks())

                self.assertEqual(result["key"], "Перевод")

    def test_discards_an_unexpected_key_without_retry(self) -> None:
        calls = 0

        def call_api(_prompt: str, _limit: int) -> str:
            nonlocal calls
            calls += 1
            return json.dumps(
                {"key": "Перевод", "explanation": "Готово"},
                ensure_ascii=False,
            )

        engine = BatchLlmEngine(call_api=call_api)
        items = {"key": EngineItem("key", "Translation", "Translation")}

        result = engine.translate_batch(items, TARGET_LANG, callbacks())

        self.assertEqual(result, {"key": "Перевод"})
        self.assertEqual(calls, 1)

    def test_omits_value_after_two_invalid_responses(self) -> None:
        engine = BatchLlmEngine(
            call_api=lambda _prompt, _limit: json.dumps({"key": None})
        )
        items = {"key": EngineItem("key", "Original", "Original")}

        result = engine.translate_batch(items, TARGET_LANG, callbacks())

        self.assertNotIn("key", result)

    def test_rejects_an_added_placeholder(self) -> None:
        api_calls: list[str] = []

        def call_api(prompt: str, _limit: int) -> str:
            api_calls.append(prompt)
            if "BROKEN TRANSLATION:" in prompt:
                return json.dumps({"key": "Значение"}, ensure_ascii=False)
            normal_calls = [p for p in api_calls if "BROKEN TRANSLATION:" not in p]
            if len(normal_calls) == 1:
                return json.dumps({"key": "Значение [#9#]"}, ensure_ascii=False)
            return json.dumps({"key": "Значение"}, ensure_ascii=False)

        engine = BatchLlmEngine(call_api=call_api)
        items = {"key": EngineItem("key", "Value", "Value")}

        result = engine.translate_batch(items, TARGET_LANG, callbacks())

        self.assertEqual(result["key"], "Значение")
        self.assertEqual(sum("BROKEN TRANSLATION:" in p for p in api_calls), 1)
        self.assertEqual(sum("BROKEN TRANSLATION:" not in p for p in api_calls), 2)

    def test_rejects_a_duplicated_placeholder(self) -> None:
        api_calls: list[str] = []

        def call_api(prompt: str, _limit: int) -> str:
            api_calls.append(prompt)
            if "BROKEN TRANSLATION:" in prompt:
                return json.dumps({"key": "Значение [#0#]"}, ensure_ascii=False)
            normal_calls = [p for p in api_calls if "BROKEN TRANSLATION:" not in p]
            if len(normal_calls) == 1:
                return json.dumps(
                    {"key": "Значение [#0#] [#0#]"}, ensure_ascii=False
                )
            return json.dumps({"key": "Значение [#0#]"}, ensure_ascii=False)

        engine = BatchLlmEngine(call_api=call_api)
        items = {
            "key": EngineItem(
                "key",
                "Value %s",
                "Value [#0#]",
                {"[#0#]": "%s"},
            )
        }

        result = engine.translate_batch(items, TARGET_LANG, callbacks())

        self.assertEqual(result["key"], "Значение %s")
        self.assertEqual(sum("BROKEN TRANSLATION:" in p for p in api_calls), 1)
        self.assertEqual(sum("BROKEN TRANSLATION:" not in p for p in api_calls), 2)

    def test_accepts_spaced_placeholder_syntax_used_by_unmasking(self) -> None:
        engine = BatchLlmEngine(
            call_api=lambda _prompt, _limit: json.dumps(
                {"key": "Значение [ # 0 # ]"},
                ensure_ascii=False,
            )
        )
        items = {
            "key": EngineItem(
                "key",
                "Value %s",
                "Value [#0#]",
                {"[#0#]": "%s"},
            )
        }

        result = engine.translate_batch(items, TARGET_LANG, callbacks())

        self.assertEqual(result["key"], "Значение %s")

    def test_preserves_a_literal_numbered_placeholder(self) -> None:
        engine = BatchLlmEngine(
            call_api=lambda _prompt, _limit: json.dumps(
                {"key": "Литерал [#7#]"},
                ensure_ascii=False,
            )
        )
        items = {"key": EngineItem("key", "Literal [#7#]", "Literal [#7#]")}

        result = engine.translate_batch(items, TARGET_LANG, callbacks())

        self.assertEqual(result["key"], "Литерал [#7#]")

    def test_masking_avoids_collision_with_a_literal_placeholder(self) -> None:
        source = "Literal [#0#] and %s"
        masked, mapping = mask_protected_fragments(source)
        self.assertEqual(masked, "Literal [#0#] and [#1#]")
        self.assertEqual(mapping, {"[#1#]": "%s"})

        engine = BatchLlmEngine(
            call_api=lambda _prompt, _limit: json.dumps(
                {"key": "Литерал [#0#] и [#1#]"},
                ensure_ascii=False,
            )
        )
        items = {"key": EngineItem("key", source, masked, mapping)}

        result = engine.translate_batch(items, TARGET_LANG, callbacks())

        self.assertEqual(result["key"], "Литерал [#0#] и %s")

    def test_real_mask_unmask_pipeline_preserves_protected_fragments(self) -> None:
        source = "Power §a%s§r\n[docs](guide.md)"
        masked, mapping = mask_protected_fragments(source)
        translated_masked = masked.replace("Power", "Мощность").replace(
            "docs", "справка"
        )
        engine = BatchLlmEngine(
            call_api=lambda _prompt, _limit: json.dumps(
                {"key": translated_masked},
                ensure_ascii=False,
            )
        )
        items = {"key": EngineItem("key", source, masked, mapping)}

        result = engine.translate_batch(items, TARGET_LANG, callbacks())

        self.assertEqual(result["key"], "Мощность §a%s§r\n[справка](guide.md)")

    def test_invalid_json_retries_the_whole_chunk(self) -> None:
        calls: list[set[str]] = []

        def call_api(prompt: str, _limit: int) -> str:
            calls.append(set(prompt_payload(prompt)))
            if len(calls) == 1:
                return "not-json"
            return json.dumps(
                {"first": "Первый", "second": "Второй"},
                ensure_ascii=False,
            )

        engine = BatchLlmEngine(call_api=call_api)
        items = {
            "first": EngineItem("first", "First", "First"),
            "second": EngineItem("second", "Second", "Second"),
        }

        result = engine.translate_batch(items, TARGET_LANG, callbacks())

        self.assertEqual(result, {"first": "Первый", "second": "Второй"})
        self.assertEqual(calls, [{"first", "second"}, {"first", "second"}])

    def test_network_error_retries_the_whole_chunk(self) -> None:
        calls = 0

        def call_api(_prompt: str, _limit: int) -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise requests.ConnectionError("offline")
            return json.dumps({"key": "Перевод"}, ensure_ascii=False)

        engine = BatchLlmEngine(call_api=call_api)
        items = {"key": EngineItem("key", "Original", "Original")}

        result = engine.translate_batch(items, TARGET_LANG, callbacks())

        self.assertEqual(result, {"key": "Перевод"})
        self.assertEqual(calls, 2)

    def test_partial_invalid_response_caches_only_the_valid_translation(self) -> None:
        calls = 0

        def call_api(_prompt: str, _limit: int) -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                return json.dumps(
                    {"good": "Хорошо", "bad": None},
                    ensure_ascii=False,
                )
            return json.dumps({"bad": None}, ensure_ascii=False)

        engine = BatchLlmEngine(call_api=call_api)
        cache = MemoryCache()
        service = ServiceWithEngine(cache, engine)

        result = service.translate_dict(
            {"good": "Good", "bad": "Bad"},
            TARGET_LANG,
            callbacks(),
        )

        self.assertEqual(result, {"good": "Хорошо", "bad": "Bad"})
        self.assertEqual(cache.values, {("ru", "Good"): "Хорошо"})

    def test_stop_while_paused_prevents_the_initial_request(self) -> None:
        running = True
        calls = 0

        def should_run() -> bool:
            return running

        def wait_if_paused() -> None:
            nonlocal running
            running = False

        def call_api(_prompt: str, _limit: int) -> str:
            nonlocal calls
            calls += 1
            return json.dumps({"key": "Перевод"}, ensure_ascii=False)

        engine = BatchLlmEngine(call_api=call_api)
        items = {"key": EngineItem("key", "Original", "Original")}

        result = engine.translate_batch(
            items,
            TARGET_LANG,
            callbacks(should_run=should_run, wait_if_paused=wait_if_paused),
        )

        self.assertEqual(result, {})
        self.assertEqual(calls, 0)

    def test_stop_while_paused_prevents_a_retry_request(self) -> None:
        running = True
        wait_calls = 0
        api_calls = 0

        def should_run() -> bool:
            return running

        def wait_if_paused() -> None:
            nonlocal running, wait_calls
            wait_calls += 1
            if wait_calls == 2:
                running = False

        def call_api(_prompt: str, _limit: int) -> str:
            nonlocal api_calls
            api_calls += 1
            return json.dumps({"key": None})

        engine = BatchLlmEngine(call_api=call_api)
        items = {"key": EngineItem("key", "Original", "Original")}

        result = engine.translate_batch(
            items,
            TARGET_LANG,
            callbacks(should_run=should_run, wait_if_paused=wait_if_paused),
        )

        self.assertEqual(result, {})
        self.assertEqual(api_calls, 1)


if __name__ == "__main__":
    unittest.main()
