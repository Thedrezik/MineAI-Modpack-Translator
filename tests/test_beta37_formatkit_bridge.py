import unittest

from formatkit import FormatRegistry


class Beta37FormatKitBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = FormatRegistry.default()

    def test_guideme_keeps_beta36_units_and_adds_upstream_validation(self) -> None:
        source = "Use the [Network Tool](tools.md).\n"

        plan = self.registry.plan(
            "assets/ae2/ae2guide/getting_started.md",
            source,
            "ru_ru",
        )

        self.assertEqual(plan.adapter_id, "guideme-v2")
        self.assertEqual(
            plan.validation_layers,
            ("formatkit-beta36", "mineai-formatkit"),
        )
        self.assertEqual(
            plan.target_path,
            "assets/ae2/ae2guide/_ru_ru/getting_started.md",
        )
        unit = plan.units[0]
        candidate = unit.payload.replace("Use the", "Используйте").replace(
            "Network Tool",
            "Сетевой инструмент",
        )
        result = plan.apply({unit.id: candidate})
        self.assertEqual(
            result.text,
            "Используйте [Сетевой инструмент](tools.md).\n",
        )

    def test_existing_upstream_target_is_reused_from_canonical_source(self) -> None:
        source_path = "assets/ae2/ae2guide/getting_started.md"
        target_path = "assets/ae2/ae2guide/_ru_ru/getting_started.md"
        source = "Use the [Network Tool](tools.md).\n"
        target = "Используйте [Сетевой инструмент](tools.md).\n"
        source_plan = self.registry.plan(source_path, source, "ru_ru")
        target_plan = self.registry.plan(
            target_path,
            target,
            "ru_ru",
            target_path_hint=target_path,
        )

        merged, pending = source_plan.merge_existing(target_plan, r"[А-Яа-яЁё]")
        result = merged.apply({})

        self.assertEqual(pending, frozenset())
        self.assertEqual(result.text, target)

    def test_guideme_uses_legacy_plan_when_sdk_does_not_cover_image_alt(self) -> None:
        source = "![A Cell With 1 Type](../assets/cell.png)\n"

        plan = self.registry.plan(
            "assets/ae2/ae2guide/cells.md",
            source,
            "ru_ru",
        )

        self.assertEqual(plan.adapter_id, "guideme-v2")
        self.assertFalse(hasattr(plan, "validation_layers"))
        unit = plan.units[0]
        candidate = unit.payload.replace(
            "A Cell With 1 Type",
            "Ячейка с одним типом",
        )
        result = plan.apply({unit.id: candidate})
        self.assertEqual(
            result.text,
            "![Ячейка с одним типом](../assets/cell.png)\n",
        )

    def test_upstream_locale_json_is_available_through_host_registry(self) -> None:
        source = '{\n  "item.demo.name": "Network Tool %s"\n}\n'

        plan = self.registry.plan(
            "assets/demo/lang/en_us.json",
            source,
            "ru_ru",
        )

        self.assertEqual(plan.adapter_id, "minecraft-lang-json")
        self.assertEqual(plan.target_path, "assets/demo/lang/ru_ru.json")
        unit = plan.units[0]
        result = plan.apply({unit.id: "Сетевой инструмент [#0#]"})
        self.assertIn('"Сетевой инструмент %s"', result.text)

    def test_beta36_only_adapter_remains_available_as_fallback(self) -> None:
        source = '{"title":"Welcome","body":"Read this guide."}'

        plan = self.registry.plan(
            "data/demo/modonomicon/books/guide/en_us/intro.json",
            source,
            "ru_ru",
        )

        self.assertEqual(plan.adapter_id, "modonomicon-json-v1")


if __name__ == "__main__":
    unittest.main()
