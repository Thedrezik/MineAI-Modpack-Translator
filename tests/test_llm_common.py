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
        from mineai.engines.llm_common import BatchLlmEngine, build_translation_prompt
        from mineai.engines.service import TranslationService
        from mineai.text_processing import mask_protected_fragments
    finally:
        os.chdir(_original_cwd)


TARGET_LANG = {"api": "ru", "name": "Russian"}


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
    for marker in ("Data: ", "Данные: "):
        if marker in prompt:
            return json.loads(prompt.split(marker, 1)[1])
    raise AssertionError("Prompt does not contain a JSON payload marker")


class MemoryCache:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get(self, api_code: str, source: str) -> str | None:
        return self.values.get((api_code, source))

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

    def _build_engine(self, context: str = "") -> BatchLlmEngine:
        return self.engine


class BatchLlmEngineTests(unittest.TestCase):
    def test_safe_prompt_requires_all_numbered_placeholders(self) -> None:
        prompt = build_translation_prompt(
            {"key": "Requires [#0#] and [#1#]"},
            "Russian",
            mode="safe",
            context="",
        )

        self.assertIn("every [#N#] placeholder", prompt)
        self.assertIn("Do not add, remove, duplicate, or rename", prompt)

    def test_context_prompt_requires_all_numbered_placeholders(self) -> None:
        prompt = build_translation_prompt(
            {"key": "Requires [#0#] and [#1#]"},
            "Russian",
            mode="context",
            context="Example Mod",
        )

        self.assertIn("Все маркеры вида [#N#]", prompt)
        self.assertIn("не удаляй, не добавляй, не дублируй", prompt)

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

        def call_api(prompt: str, _max_tokens: int) -> str:
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
        self.assertEqual(set(calls[1]), {"power"})

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
        responses = iter(
            [
                json.dumps({"key": "Значение [#9#]"}, ensure_ascii=False),
                json.dumps({"key": "Значение"}, ensure_ascii=False),
            ]
        )
        engine = BatchLlmEngine(call_api=lambda _prompt, _limit: next(responses))
        items = {"key": EngineItem("key", "Value", "Value")}

        result = engine.translate_batch(items, TARGET_LANG, callbacks())

        self.assertEqual(result["key"], "Значение")

    def test_rejects_a_duplicated_placeholder(self) -> None:
        responses = iter(
            [
                json.dumps({"key": "Значение [#0#] [#0#]"}, ensure_ascii=False),
                json.dumps({"key": "Значение [#0#]"}, ensure_ascii=False),
            ]
        )
        engine = BatchLlmEngine(call_api=lambda _prompt, _limit: next(responses))
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
