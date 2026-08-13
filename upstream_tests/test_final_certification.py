from __future__ import annotations

import re
import unittest
from pathlib import Path

import mineai_formatkit as kit_module
from mineai_formatkit import FormatKit, FormatRegistry


class FinalPublicApiCertificationTests(unittest.TestCase):
    def test_public_exports_are_unique_and_resolvable(self) -> None:
        exports = tuple(kit_module.__all__)
        self.assertEqual(len(exports), len(set(exports)))
        for name in exports:
            with self.subTest(export=name):
                self.assertTrue(hasattr(kit_module, name), name)

        required = {
            "FormatKit",
            "FormatRegistry",
            "FormatAnalysis",
            "AdapterCapabilities",
            "DetectedFormat",
            "Diagnostic",
            "DiagnosticSeverity",
            "TranslationPlan",
            "TranslationUnit",
            "ValidationError",
            "LocaleMergePlanner",
            "FtbQuestsLocaleMergePlanner",
            "JarContainer",
        }
        self.assertTrue(required.issubset(set(exports)))

    def test_every_default_adapter_is_publicly_exported(self) -> None:
        registry = FormatRegistry.default()
        exports = set(kit_module.__all__)
        for adapter in registry.adapters:
            with self.subTest(adapter=adapter.name):
                self.assertIn(type(adapter).__name__, exports)
                self.assertIs(getattr(kit_module, type(adapter).__name__), type(adapter))


class FinalRegistryCapabilityCertificationTests(unittest.TestCase):
    def test_default_registry_capabilities_are_consistent(self) -> None:
        registry = FormatRegistry.default()
        self.assertEqual(len(registry.adapters), 15)

        names: list[str] = []
        merge_capable: set[str] = set()
        for adapter in registry.adapters:
            capabilities = registry.capabilities_for(adapter)
            names.append(capabilities.name)

            with self.subTest(adapter=capabilities.name):
                self.assertEqual(capabilities.name, adapter.name)
                self.assertTrue(capabilities.structural_validation)
                self.assertTrue(capabilities.container_independent)
                self.assertTrue(capabilities.format_name)
                self.assertTrue(capabilities.canonical_source)

                if capabilities.supports_target_path:
                    self.assertTrue(callable(getattr(adapter, "target_path", None)))
                if capabilities.supports_existing_target_merge:
                    merge_capable.add(capabilities.name)

        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(
            merge_capable,
            {
                "minecraft-lang-json",
                "ftb-quests-lang",
                "collapsible-groups-config-lang-json",
                "jaopca-config-lang-json",
            },
        )


class ReadmeExampleSmokeTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1] / "docs" / "upstream-formatkit"

    def _run_embedding_example(self, filename: str) -> None:
        markdown = (self.ROOT / filename).read_text(encoding="utf-8")
        blocks = re.findall(r"```python\s*\n(.*?)```", markdown, flags=re.DOTALL)
        self.assertGreaterEqual(len(blocks), 1, f"No Python example found in {filename}")

        namespace = {
            "source_text": '{"demo.title":"Example"}',
            "translate_externally": lambda text: f"translated::{text}",
        }
        exec(compile(blocks[0], filename, "exec"), namespace, namespace)

        analysis = namespace["analysis"]
        target_text = namespace["target_text"]
        target_path = namespace["target_path"]
        self.assertTrue(analysis.supported)
        self.assertTrue(analysis.ready)
        self.assertEqual(analysis.adapter_name, "minecraft-lang-json")
        self.assertEqual(target_path, "assets/demo/lang/ru_ru.json")
        self.assertIn("translated::Example", target_text)

    def test_english_readme_embedding_example(self) -> None:
        self._run_embedding_example("README.md")

    def test_russian_readme_embedding_example(self) -> None:
        self._run_embedding_example("README_RU.md")


if __name__ == "__main__":
    unittest.main()
