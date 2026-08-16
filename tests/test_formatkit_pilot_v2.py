import json
import unittest

from mineai.constants import LANGUAGES
from mineai.engines.base import EngineCallbacks, EngineItem
from mineai.engines.llm_v2 import (
    BatchLlmEngine,
    build_translation_prompt,
    marker_validation_error,
)
from mineai.formatkit_bridge import plan_locale_work
from mineai.text_processing import mask_protected_fragments


RU = LANGUAGES["Русский"]


def callbacks() -> EngineCallbacks:
    return EngineCallbacks(
        should_run=lambda: True,
        wait_if_paused=lambda: None,
        on_log=lambda _message, _tag: None,
        on_status=lambda _message: None,
    )


def prompt_payload(prompt: str) -> dict[str, str]:
    return json.loads(prompt.split("DATA:\n", 1)[1])


class PilotV2LlmSafetyTests(unittest.TestCase):
    def test_manifest_uses_real_source_order(self) -> None:
        prompt = build_translation_prompt(
            {"key": "Put [#9#] in [#13#][#10#] then [#11#]"},
            "Russian",
            mode="safe",
            context="",
        )
        self.assertIn('"key": [#9#] [#13#] [#10#] [#11#]', prompt)
        self.assertIn("SOURCE ORDER", prompt)

    def test_same_marker_counts_in_wrong_order_are_rejected(self) -> None:
        source = "The [#0#]Drill[#1#] uses [#2#]CF[#3#]"
        reordered = "Бур [#0#][#1#] использует [#3#]CF[#2#]"
        self.assertEqual(
            marker_validation_error(reordered, source),
            "Изменён порядок маркеров [#N#]",
        )

    def test_real_actually_additions_markup_reordering_is_rejected(self) -> None:
        source = "The <item>Farmer<r> can <imp>plant and harvest<r> crops."
        masked, _mapping = mask_protected_fragments(source)
        broken = (
            masked.replace("[#1#]", "__M1__")
            .replace("[#2#]", "[#1#]")
            .replace("__M1__", "[#2#]")
        )
        self.assertEqual(
            marker_validation_error(broken, masked),
            "Изменён порядок маркеров [#N#]",
        )

    def test_bare_hash_marker_hallucination_is_rejected_and_retried(self) -> None:
        calls: list[str] = []

        def call_api(prompt: str, _limit: int) -> str:
            calls.append(prompt)
            if "BROKEN TRANSLATION:" in prompt:
                return json.dumps({"key": "Размещение наверху"}, ensure_ascii=False)
            normal = [p for p in calls if "BROKEN TRANSLATION:" not in p]
            if len(normal) == 1:
                return json.dumps({"key": "#0#Размещение наверху"}, ensure_ascii=False)
            return json.dumps({"key": "Размещение наверху"}, ensure_ascii=False)

        engine = BatchLlmEngine(call_api=call_api)
        items = {"key": EngineItem("key", "Place on top", "Place on top")}
        result = engine.translate_batch(items, RU, callbacks())
        self.assertEqual(result["key"], "Размещение наверху")
        self.assertEqual(sum("BROKEN TRANSLATION:" in p for p in calls), 1)

    def test_eight_marker_string_is_sent_alone(self) -> None:
        calls: list[set[str]] = []
        source = "A " + " ".join(f"[#{index}#]" for index in range(8))

        def call_api(prompt: str, _limit: int) -> str:
            payload = prompt_payload(prompt)
            calls.append(set(payload))
            if "complex" in payload:
                return json.dumps(
                    {"complex": source.replace("A ", "Текст ", 1)},
                    ensure_ascii=False,
                )
            return json.dumps({"simple": "Просто"}, ensure_ascii=False)

        engine = BatchLlmEngine(call_api=call_api)
        result = engine.translate_batch(
            {
                "complex": EngineItem("complex", source, source),
                "simple": EngineItem("simple", "Simple", "Simple"),
            },
            RU,
            callbacks(),
        )
        self.assertIn("complex", result)
        self.assertEqual(result["simple"], "Просто")
        self.assertEqual(calls, [{"complex"}, {"simple"}])


class PilotV2TechnicalFilterTests(unittest.TestCase):
    def test_obvious_runtime_technical_values_do_not_enter_llm_pending(self) -> None:
        source = json.dumps(
            {
                "shift": "[SHIFT]",
                "coords": "9.17 N 19.89 E",
                "uuid": "UUID: %s",
                "formula": "+%s (%d)",
                "lower_true": "true",
                "visible": "Requires %s power",
                "visible_true": "True",
            }
        )
        work = plan_locale_work(
            "assets/demo/lang/en_us.json",
            source,
            "ru_ru",
            None,
            "force",
        )
        assert work is not None
        self.assertEqual(work.total_translatable, 2)
        self.assertEqual(set(work.pending), {"key:visible", "key:visible_true"})


if __name__ == "__main__":
    unittest.main()
