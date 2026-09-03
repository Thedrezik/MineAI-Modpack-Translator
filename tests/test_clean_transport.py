"""Beta42 regression tests for the clean LLM transport."""

import json
import unittest

from mineai.engines.base import EngineCallbacks, EngineItem
from mineai.engines.clean_transport import (
    extract_visible_nodes,
    rebuild_masked,
    response_is_clean,
    sanitize_prompt_context,
)
from mineai.engines.llm_common import (
    BatchLlmEngine,
    build_clean_translation_prompt,
    parse_llm_array_response,
)
from mineai.text_processing import mask_protected_fragments


TARGET_LANG = {"api": "ru", "name": "Russian", "regex": r"[А-Яа-яЁё]"}


def _callbacks() -> EngineCallbacks:
    return EngineCallbacks(
        should_run=lambda: True,
        wait_if_paused=lambda: None,
        on_log=lambda _message, _tag: None,
        on_status=lambda _message: None,
    )


class CleanTransportTests(unittest.TestCase):
    def test_only_visible_nodes_are_extracted(self):
        source = (
            "The [Quantum Bridge](ae2:items/bridge.md) uses 2x "
            "$(#BB00BB)Epic$() <ItemLink id=\"ae2:x\" />."
        )
        masked, mapping = mask_protected_fragments(source)
        nodes = extract_visible_nodes(masked)

        self.assertEqual([node.text for node in nodes], ["The", "Quantum Bridge", "uses", "Epic"])
        payload = " ".join(node.text for node in nodes)
        self.assertNotIn("ae2:", payload)
        self.assertNotIn("2x", payload)
        self.assertNotIn("BB00BB", payload)
        self.assertNotIn("ItemLink", payload)
        self.assertTrue(mapping)

    def test_alphanumeric_versions_and_ids_are_not_sent(self):
        masked, _mapping = mask_protected_fragments(
            "Version v1.20.1 uses AE2 and 16x energy."
        )
        self.assertEqual(
            [node.text for node in extract_visible_nodes(masked)],
            ["Version", "uses", "and", "energy."],
        )

    def test_rebuild_uses_original_positions(self):
        source = "$(#BB00BB)Epic$() [Guide](guide.md) 1x"
        masked, mapping = mask_protected_fragments(source)
        nodes = extract_visible_nodes(masked)
        rebuilt = rebuild_masked(masked, nodes, ["Эпический", "руководство"])

        from mineai.text_processing import unmask_translation

        self.assertEqual(
            unmask_translation(rebuilt, mapping),
            "$(#BB00BB)Эпический$() [руководство](guide.md) 1x",
        )

    def test_llm_receives_array_without_locators_or_markers(self):
        prompts: list[str] = []
        source = "The [Guide](ae2:guide.md) costs 2x $(#BB00BB)Epic$()."
        masked, mapping = mask_protected_fragments(source)
        item = EngineItem("json:/pages/0/title", source, masked, mapping)

        def call_api(prompt: str, _limit: int) -> str:
            prompts.append(prompt)
            payload = json.loads(prompt.split("DATA:\n", 1)[1])
            self.assertIsInstance(payload, list)
            self.assertEqual(payload, ["The", "Guide", "costs", "Epic"])
            self.assertNotIn("json:/pages/0/title", prompt.split("DATA:\n", 1)[1])
            self.assertNotIn("[#", prompt.split("DATA:\n", 1)[1])
            self.assertNotIn("ae2:", prompt.split("DATA:\n", 1)[1])
            self.assertNotIn("2x", prompt.split("DATA:\n", 1)[1])
            return json.dumps(["Это", "руководство", "стоит", "Эпик"], ensure_ascii=False)

        engine = BatchLlmEngine(call_api=call_api)
        result = engine.translate_batch({item.key: item}, TARGET_LANG, _callbacks())

        self.assertEqual(
            result[item.key],
            "Это [руководство](ae2:guide.md) стоит 2x $(#BB00BB)Эпик$().",
        )
        self.assertEqual(len(prompts), 1)

    def test_unchanged_standalone_english_article_is_removed(self):
        source = "The &6Empty Burner"
        masked, mapping = mask_protected_fragments(source)
        item = EngineItem("title", source, masked, mapping)

        def call_api(prompt: str, _limit: int) -> str:
            payload = json.loads(prompt.split("DATA:\n", 1)[1])
            self.assertEqual(payload, ["The", "Empty Burner"])
            # Small local models often leave a standalone article unchanged;
            # it must not block the rest of the clean document transport.
            return json.dumps(["The", "Пустая горелка"], ensure_ascii=False)

        engine = BatchLlmEngine(call_api=call_api)
        result = engine.translate_batch({item.key: item}, TARGET_LANG, _callbacks())

        self.assertEqual(result[item.key], "&6Пустая горелка")

    def test_status_effect_sentence_keeps_numeric_fragments_in_one_translation_unit(self):
        source = (
            "Foods originating from Jungle biomes will provide you with "
            "Haste II for 2 minutes."
        )
        masked, mapping = mask_protected_fragments(source)
        item = EngineItem("entry", source, masked, mapping)
        payloads: list[list[str]] = []

        def call_api(prompt: str, _limit: int) -> str:
            payload = json.loads(prompt.split("DATA:\n", 1)[1])
            payloads.append(payload)
            self.assertEqual(len(payload), 1)
            self.assertNotIn("2", payload[0])
            self.assertIn("[#", payload[0])
            return json.dumps(
                ["Еда из биомов джунглей даёт Ускорение [#1#] на [#0#] минуты."],
                ensure_ascii=False,
            )

        engine = BatchLlmEngine(call_api=call_api)
        result = engine.translate_batch({item.key: item}, TARGET_LANG, _callbacks())

        self.assertEqual(
            result[item.key],
            "Еда из биомов джунглей даёт Ускорение II на 2 минуты.",
        )
        self.assertEqual(len(payloads), 1)

    def test_invalid_clean_response_is_rejected(self):
        self.assertFalse(response_is_clean("Перевод [#0#]"))
        self.assertFalse(response_is_clean("Перевод 12x"))
        self.assertFalse(response_is_clean("Перевод https://example.test"))
        self.assertTrue(response_is_clean("Перевод текста"))

    def test_prompt_context_is_sanitized_like_payload(self):
        context = (
            "assets/guide.json | json:/pages/0/title | "
            "https://example.test/2x | Applied Energistics 2"
        )
        self.assertEqual(sanitize_prompt_context(context), "assets guide pages title")
        prompt = build_clean_translation_prompt(
            ["Title"],
            "Russian",
            mode="safe",
            context=context,
            prompt_type="mods",
        )
        self.assertNotIn("json:/pages/0/title", prompt)
        self.assertNotIn("https://example.test/2x", prompt)
        self.assertNotIn("Applied Energistics 2", prompt)

    def test_array_parser_ignores_fence_and_explanation(self):
        self.assertEqual(
            parse_llm_array_response("готово\n```json\n[\"один\", \"два\"]\n```")
            ,
            ["один", "два"],
        )

    def test_array_parser_rejects_repeated_bracket_garbage_after_payload(self):
        with self.assertRaises(ValueError):
            parse_llm_array_response('["один", "два"] ] ] ] ]')


if __name__ == "__main__":
    unittest.main()
