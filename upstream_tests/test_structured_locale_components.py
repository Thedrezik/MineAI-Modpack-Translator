import json
import unittest

from mineai_formatkit import LocaleMergePlanner, MinecraftLangJsonAdapter, ValidationError


class StructuredLocaleComponentTests(unittest.TestCase):
    SOURCE = (
        '{\n'
        '  "normal": "Normal text",\n'
        '  "component": [{"extra":[{"strikethrough":true,"text":"bottle"}," bundle..."],'
        '"text":"If I could save time in a "}],\n'
        '  "unsupported": {"label": "Do not guess this"}\n'
        '}'
    )

    def test_strict_component_value_exposes_only_visible_text_leaves(self) -> None:
        adapter = MinecraftLangJsonAdapter()
        plan = adapter.prepare("assets/demo/lang/en_us.json", self.SOURCE)
        self.assertEqual(len(plan.units), 4)
        structured = [unit for unit in plan.units if unit.context == "component"]
        self.assertEqual(len(structured), 3)
        self.assertEqual(
            set(plan.metadata["unsupported_non_string_keys"]),
            {"unsupported"},
        )
        self.assertEqual(plan.metadata["structured_component_keys"], ("component",))
        self.assertEqual(adapter.apply(plan, {unit.id: unit.text for unit in plan.units}), self.SOURCE)

        translated = {
            unit.id: (
                unit.text.replace("Normal text", "Обычный текст")
                .replace("bottle", "бутылку")
                .replace(" bundle", " набор")
                .replace("If I could save time in a", "Если бы я мог сохранить время в")
            )
            for unit in plan.units
        }
        output = adapter.apply(plan, translated)
        parsed = json.loads(output)
        self.assertEqual(parsed["component"][0]["text"], "Если бы я мог сохранить время в ")
        self.assertEqual(parsed["component"][0]["extra"][0]["text"], "бутылку")
        self.assertEqual(parsed["component"][0]["extra"][0]["strikethrough"], True)
        self.assertEqual(parsed["component"][0]["extra"][1], " набор...")
        self.assertEqual(parsed["unsupported"], {"label": "Do not guess this"})

    def test_unknown_structured_schema_remains_unsupported(self) -> None:
        source = '{"value":{"text":"Visible?","color":"red"}}'
        plan = MinecraftLangJsonAdapter().prepare("assets/demo/lang/en_us.json", source)
        self.assertEqual(plan.units, ())
        self.assertEqual(plan.metadata["unsupported_non_string_keys"], ("value",))

    def test_legacy_simple_object_and_list_remain_unsupported(self) -> None:
        source = '{"simple":"Text","structured":{"text":"Visible"},"list":["A"]}'
        plan = MinecraftLangJsonAdapter().prepare("assets/demo/lang/en_us.json", source)
        self.assertEqual([unit.text for unit in plan.units], ["Text"])
        self.assertEqual(
            set(plan.metadata["unsupported_non_string_keys"]),
            {"structured", "list"},
        )
        self.assertEqual(plan.metadata["structured_component_keys"], ())

    def test_structure_changes_fail_closed(self) -> None:
        adapter = MinecraftLangJsonAdapter()
        plan = adapter.prepare("assets/demo/lang/en_us.json", self.SOURCE)
        unit = next(unit for unit in plan.units if unit.context == "component")
        bad_source = plan.source_text.replace('"strikethrough":true', '"strikethrough":false')
        with self.assertRaises(ValidationError):
            adapter.validate(plan.source_text, bad_source)

    def test_append_reuses_only_same_structured_shape(self) -> None:
        source = (
            '{"component":[{"extra":[{"strikethrough":true,"text":"bottle"}," bundle..."],'
            '"text":"If I could save time in a "}]}'
        )
        target = (
            '{"component":[{"extra":[{"strikethrough":true,"text":"бутылку"}," набор..."],'
            '"text":"Если бы я мог сохранить время в "}]}'
        )
        planner = LocaleMergePlanner()
        plan = planner.plan(
            "assets/demo/lang/en_us.json", source, "ru_ru", target_text=target, mode="append"
        )
        self.assertEqual(plan.pending_ids, ())
        self.assertEqual(len(plan.existing_values), 3)
        output = planner.build(plan, {})
        self.assertEqual(json.loads(output), json.loads(target))
        self.assertEqual(planner.adapter.fingerprint(output), planner.adapter.fingerprint(source))

    def test_append_rejects_target_structure_drift(self) -> None:
        source = '{"component":[{"extra":[{"strikethrough":true,"text":"bottle"}],"text":"Save time"}]}'
        target = '{"component":[{"extra":[{"strikethrough":false,"text":"бутылка"}],"text":"Экономьте время"}]}'
        plan = LocaleMergePlanner().plan(
            "assets/demo/lang/en_us.json", source, "ru_ru", target_text=target, mode="append"
        )
        self.assertEqual(plan.invalid_existing_keys, ("component",))
        self.assertEqual(len(plan.pending_ids), 2)
        self.assertEqual(plan.existing_values, {})


if __name__ == "__main__":
    unittest.main()
