from __future__ import annotations

import json
import os
import unittest
import zipfile

from mineai_formatkit.core import ValidationError
from mineai_formatkit.minecraft_lang import MinecraftLangJsonAdapter


RECHISELED_JAR_ENV = "MINEAI_FORMATKIT_RECHISELED_JAR"
EXPECTED_RECHISELED_SHA256 = "7bf14cf8a4bfdc4b6c990126a75da29fd2bb7559d1c05b71e29c8fd5ae044435"


class MinecraftLangJsonAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = MinecraftLangJsonAdapter()

    def test_matches_only_source_locale_json(self) -> None:
        self.assertTrue(self.adapter.matches("assets/rechiseled/lang/en_us.json"))
        self.assertFalse(self.adapter.matches("assets/rechiseled/lang/ru_ru.json"))
        self.assertFalse(self.adapter.matches("assets/rechiseled/models/block/foo.json"))

    def test_target_path_replaces_only_locale_name(self) -> None:
        self.assertEqual(
            self.adapter.target_path("assets/rechiseled/lang/en_us.json", "ru_ru"),
            "assets/rechiseled/lang/ru_ru.json",
        )

    def test_extracts_values_without_exposing_json_structure(self) -> None:
        source = '{\n  "mod.title": "Example Mod",\n  "mod.tooltip": "Use %s here"\n}\n'
        plan = self.adapter.prepare("assets/example/lang/en_us.json", source)
        self.assertEqual([u.context for u in plan.units], ["mod.title", "mod.tooltip"])
        self.assertEqual(plan.units[0].text, "Example Mod")
        self.assertEqual(plan.units[1].text, "Use [#0#] here")

    def test_identity_round_trip_is_byte_exact_even_with_escapes(self) -> None:
        source = '{\n  "escaped": "A\\u0020B\\nC",\n  "slash": "a\\/b"\n}\n'
        plan = self.adapter.prepare("assets/example/lang/en_us.json", source)
        output = self.adapter.apply(plan, {unit.id: unit.text for unit in plan.units})
        self.assertEqual(output, source)

    def test_translation_changes_only_value_token(self) -> None:
        source = '{\n  "first": "Hello",\n  "second": "World"\n}\n'
        plan = self.adapter.prepare("assets/example/lang/en_us.json", source)
        output = self.adapter.apply(plan, {"key:first": "Привет"})
        self.assertEqual(output, '{\n  "first": "Привет",\n  "second": "World"\n}\n')
        self.assertEqual(self.adapter.fingerprint(output), self.adapter.fingerprint(source))

    def test_quotes_are_serialized_as_valid_json(self) -> None:
        source = '{"quote":"Hello"}'
        plan = self.adapter.prepare("assets/example/lang/en_us.json", source)
        output = self.adapter.apply(plan, {"key:quote": 'Он сказал "Привет"'})
        self.assertEqual(json.loads(output)["quote"], 'Он сказал "Привет"')

    def test_placeholder_loss_is_rejected(self) -> None:
        source = '{"count":"%s items"}'
        plan = self.adapter.prepare("assets/example/lang/en_us.json", source)
        with self.assertRaises(ValidationError):
            self.adapter.apply(plan, {"key:count": "предметов"})

    def test_added_placeholder_is_rejected(self) -> None:
        source = '{"name":"Name"}'
        plan = self.adapter.prepare("assets/example/lang/en_us.json", source)
        with self.assertRaises(ValidationError):
            self.adapter.apply(plan, {"key:name": "Имя [#0#]"})

    def test_literal_percent_followed_by_word_is_not_a_format_placeholder(self) -> None:
        source = '{"title":"Bee Movie But It\'s 300% Larger"}'
        plan = self.adapter.prepare("assets/example/lang/en_us.json", source)
        self.assertEqual(plan.units[0].text, "Bee Movie But It's 300% Larger")
        self.assertEqual(plan.units[0].protected, ())

    def test_valid_java_format_specifiers_are_protected(self) -> None:
        source = '{"fmt":"Name: %1$s | Count: %d | Ratio: %.2f | %% | %n"}'
        plan = self.adapter.prepare("assets/example/lang/en_us.json", source)
        unit = plan.units[0]
        self.assertEqual(
            [fragment.value for fragment in unit.protected],
            ["%1$s", "%d", "%.2f", "%%", "%n"],
        )

    def test_duplicate_keys_are_rejected_before_translation(self) -> None:
        source = '{"same":"one","same":"two"}'
        with self.assertRaises(ValidationError):
            self.adapter.prepare("assets/example/lang/en_us.json", source)

    def test_non_string_values_are_preserved_and_not_extracted(self) -> None:
        source = '{"name":"Name","number":42,"flag":true}'
        plan = self.adapter.prepare("assets/example/lang/en_us.json", source)
        self.assertEqual([unit.id for unit in plan.units], ["key:name"])
        output = self.adapter.apply(plan, {"key:name": "Имя"})
        self.assertEqual(json.loads(output), {"name": "Имя", "number": 42, "flag": True})

    def test_non_string_values_are_reported_for_diagnostics(self) -> None:
        source = '{"simple":"Text","structured":{"text":"Visible"},"list":["A"]}'
        plan = self.adapter.prepare("assets/demo/lang/en_us.json", source)
        self.assertEqual(
            plan.metadata["unsupported_non_string_keys"],
            ("structured", "list"),
        )

    def test_natural_percent_phrase_is_not_a_printf_placeholder(self) -> None:
        source = '{"demo.chance":"There is a 75% chance to succeed"}'
        plan = self.adapter.prepare("assets/demo/lang/en_us.json", source)
        self.assertEqual(len(plan.units), 1)
        self.assertIn("75% chance", plan.units[0].text)
        self.assertFalse(
            any("% c" in fragment.value for fragment in plan.units[0].protected)
        )


@unittest.skipUnless(os.environ.get(RECHISELED_JAR_ENV), "optional Rechiseled corpus not configured")
class RechiseledFullCorpusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = MinecraftLangJsonAdapter()
        self.jar_path = os.environ[RECHISELED_JAR_ENV]

    def _source(self) -> str:
        with zipfile.ZipFile(self.jar_path) as archive:
            return archive.read("assets/rechiseled/lang/en_us.json").decode("utf-8")

    def test_rechiseled_locale_identity_round_trip_is_byte_exact(self) -> None:
        source = self._source()
        plan = self.adapter.prepare("assets/rechiseled/lang/en_us.json", source)
        self.assertEqual(len(plan.units), 3656)
        output = self.adapter.apply(plan, {unit.id: unit.text for unit in plan.units})
        self.assertEqual(output, source)

    def test_rechiseled_all_values_accept_synthetic_translation_without_structure_drift(self) -> None:
        source = self._source()
        plan = self.adapter.prepare("assets/rechiseled/lang/en_us.json", source)
        output = self.adapter.apply(
            plan,
            {unit.id: "RU " + unit.text for unit in plan.units},
        )
        self.assertEqual(self.adapter.fingerprint(output), self.adapter.fingerprint(source))
        self.assertEqual(len(json.loads(output)), 3656)


if __name__ == "__main__":
    unittest.main()
