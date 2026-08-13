import unittest

from mineai_formatkit import (
    AdapterCapabilities,
    DiagnosticSeverity,
    FormatKit,
    FormatRegistry,
    ValidationError,
)


SAMPLES = (
    (
        "guideme-markdown",
        "assets/ae2/ae2guide/start.md",
        "# Welcome\nUse an <ItemLink id=\"ae2:interface\" /> to store items.\n",
    ),
    (
        "guideme-data-driven-markdown",
        "assets/demo/guides/demo/guide/start.md",
        "# Welcome\nRead the guide.\n",
    ),
    (
        "collapsible-groups-lang-json",
        "assets/collapsible_groups/group_lang/en_us.json",
        '{"collapsible_groups.group.demo":"Demo Group"}',
    ),
    (
        "collapsible-groups-config-lang-json",
        "config/collapsiblegroups/lang/en_us.json",
        '{"collapsible_groups.group.demo":"Demo Group"}',
    ),
    (
        "jaopca-config-lang-json",
        "config/jaopca/lang/en_us.json",
        '{"material.jaopca.demo":"Demo Material"}',
    ),
    (
        "crash-assistant-localization",
        "crash_assistant_localization/en_us.json",
        '{"demo.message":"Open $LOG_FILENAME$ now"}',
    ),
    (
        "minecraft-lang-json",
        "assets/demo/lang/en_us.json",
        '{"demo.title":"Example","demo.count":"Count %s"}',
    ),
    (
        "minecraft-advancement-text",
        "data/demo/advancement/root.json",
        '{"display":{"title":"Hello World","description":"Read the guide"}}',
    ),
    (
        "minecraft-text-components",
        "data/demo/loot_table/chest.json",
        '{"functions":[{"function":"minecraft:set_name","name":"Reward Name"}]}',
    ),
    (
        "ftb-quests-lang",
        "config/ftbquests/quests/lang/en_us.snbt",
        '{\n\tchapter.001.title: "Welcome"\n}',
    ),
    (
        "ftb-quests-chapter-text",
        "config/ftbquests/quests/chapters/start.snbt",
        '{\n\tquests: [{\n\t\tfeedback_message: "Quest Complete"\n\t}]\n}',
    ),
    (
        "patchouli-book-json",
        "assets/demo/patchouli_books/guide/en_us/entries/start.json",
        '{"name":"Getting Started","pages":[{"type":"patchouli:text","text":"Hello $(br2)World"}]}',
    ),
    (
        "oracle-index-mdx",
        "oracle_index/books/demo/.content/start.mdx",
        "---\ntitle: Getting Started\n---\n\n# Welcome\nRead the guide.\n",
    ),
    (
        "oracle-index-meta-json",
        "oracle_index/books/demo/.content/_meta.json",
        '{"start.mdx":"Getting Started"}',
    ),
    (
        "immersive-engineering-manual",
        "assets/demo/manual/en_us/start.txt",
        "Demo Manual\nRead the guide.\n",
    ),
)


class DefaultRegistryTests(unittest.TestCase):
    def test_default_registry_contains_stable_builtin_adapter_set(self) -> None:
        registry = FormatRegistry.default()
        names = [registry.capabilities_for(adapter).name for adapter in registry.adapters]
        self.assertEqual(names, [sample[0] for sample in SAMPLES])
        self.assertEqual(len(names), len(set(names)))

    def test_legacy_detect_api_remains_compatible(self) -> None:
        registry = FormatRegistry.default()
        adapter = registry.detect("assets/demo/lang/en_us.json")
        self.assertIsNotNone(adapter)
        self.assertEqual(adapter.name, "minecraft-lang-json")

    def test_detect_result_exposes_capabilities_without_host_format_knowledge(self) -> None:
        registry = FormatRegistry.default()
        result = registry.detect_result(
            "assets/demo/patchouli_books/guide/en_us/entries/start.json"
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.capabilities.format_name, "Patchouli book JSON")
        self.assertTrue(result.capabilities.supports_target_path)
        self.assertTrue(result.capabilities.container_independent)

    def test_custom_registry_registration_still_works_without_capabilities(self) -> None:
        class DemoAdapter:
            name = "demo"

            def matches(self, path: str) -> bool:
                return path.endswith(".demo")

            def prepare(self, path: str, source_text: str):
                raise NotImplementedError

            def apply(self, plan, translations):
                raise NotImplementedError

        registry = FormatRegistry()
        adapter = DemoAdapter()
        registry.register(adapter)
        result = registry.detect_result("thing.demo")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertIs(result.adapter, adapter)
        self.assertEqual(result.capabilities, AdapterCapabilities("demo", "custom"))


class BuiltinAdapterContractTests(unittest.TestCase):
    def test_every_builtin_adapter_passes_basic_embedding_contract(self) -> None:
        registry = FormatRegistry.default()

        for expected_name, path, source in SAMPLES:
            with self.subTest(adapter=expected_name):
                detection = registry.detect_result(path)
                self.assertIsNotNone(detection)
                assert detection is not None
                self.assertEqual(detection.capabilities.name, expected_name)

                plan = detection.adapter.prepare(path, source)
                self.assertGreater(len(plan.units), 0)
                self.assertEqual(len(plan.by_id()), len(plan.units))

                identity = detection.adapter.apply(
                    plan, {unit.id: unit.text for unit in plan.units}
                )
                self.assertEqual(identity, source)

                if detection.capabilities.supports_target_path:
                    target_path = detection.adapter.target_path(path, "ru_ru")
                    self.assertIsInstance(target_path, str)
                    self.assertNotEqual(target_path, "")

                with self.assertRaises(ValidationError):
                    detection.adapter.apply(plan, {"unknown:unit": "test"})


class FormatKitFacadeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.kit = FormatKit.default()

    def test_analyze_returns_plan_target_path_and_apply(self) -> None:
        source = '{"demo.title":"Example"}'
        analysis = self.kit.analyze(
            "assets/demo/lang/en_us.json",
            source,
            target_locale="ru_ru",
        )
        self.assertTrue(analysis.supported)
        self.assertTrue(analysis.ready)
        self.assertEqual(analysis.adapter_name, "minecraft-lang-json")
        self.assertEqual(analysis.target_path, "assets/demo/lang/ru_ru.json")
        self.assertEqual(len(analysis.units), 1)
        output = self.kit.apply(analysis, {analysis.units[0].id: "Пример"})
        self.assertIn("Пример", output)

    def test_unsupported_path_is_a_normal_analysis_result(self) -> None:
        analysis = self.kit.analyze("assets/demo/models/item/test.json", '{"x":1}')
        self.assertFalse(analysis.supported)
        self.assertFalse(analysis.ready)
        self.assertEqual(analysis.diagnostics[0].severity, DiagnosticSeverity.INFO)
        self.assertEqual(analysis.diagnostics[0].code, "unsupported_format")

    def test_malformed_supported_file_fails_closed_as_diagnostic(self) -> None:
        analysis = self.kit.analyze(
            "assets/demo/lang/en_us.json",
            '{"same":"one","same":"two"}',
        )
        self.assertTrue(analysis.supported)
        self.assertFalse(analysis.ready)
        self.assertEqual(analysis.diagnostics[0].severity, DiagnosticSeverity.ERROR)
        self.assertEqual(analysis.diagnostics[0].code, "prepare_failed")
        with self.assertRaises(ValidationError):
            self.kit.apply(analysis, {})

    def test_structured_locale_values_are_reported_without_recursive_translation(self) -> None:
        source = '{"simple":"Text","structured":{"text":"Visible"},"list":["A"]}'
        analysis = self.kit.analyze("assets/demo/lang/en_us.json", source)
        self.assertTrue(analysis.ready)
        self.assertEqual([unit.text for unit in analysis.units], ["Text"])
        warnings = [d for d in analysis.diagnostics if d.severity == DiagnosticSeverity.WARNING]
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0].code, "structured_locale_values_unsupported")
        self.assertIn("structured", warnings[0].message)
        self.assertIn("list", warnings[0].message)


if __name__ == "__main__":
    unittest.main()
