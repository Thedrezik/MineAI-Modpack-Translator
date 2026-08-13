import unittest

from formatkit import FormatRegistry
from mineai.language_validation import translation_needs_repair
from mineai.processors.jar import JarProcessor


class GuideMeAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = FormatRegistry.default()

    def test_guideme_uses_underscored_locale_and_preserves_scene(self) -> None:
        source = (
            "---\n"
            "navigation:\n"
            "  title: Getting Started\n"
            "---\n\n"
            "# Getting Started\n\n"
            '<GameScene zoom="4">\n'
            '  <ImportStructure src="assets/assemblies/meteor_interior.snbt" />\n'
            "</GameScene>\n\n"
            'The <ItemLink id="ae2:chest" /> is useful.\n'
        )

        plan = self.registry.plan(
            "assets/ae2/ae2guide/getting-started.md",
            source,
            "ru_ru",
        )

        self.assertEqual(
            plan.target_path,
            "assets/ae2/ae2guide/_ru_ru/getting-started.md",
        )
        self.assertTrue(all("ImportStructure" not in u.payload for u in plan.units))
        body = next(u for u in plan.units if "useful" in u.payload)
        anchor = next(a.token for a in body.anchors)
        result = plan.apply({body.id: f"{anchor} полезен."})
        self.assertIn('<ItemLink id="ae2:chest" /> полезен.', result.text)
        self.assertIn("meteor_interior.snbt", result.text)

    def test_markdown_table_cells_are_translation_units(self) -> None:
        source = (
            "| Setting | Description |\n"
            "| --- | --- |\n"
            "| Default | Standard channel mode |\n"
        )
        plan = self.registry.plan(
            "assets/ae2/ae2guide/channels.md",
            source,
            "ru_ru",
        )
        payloads = {unit.payload for unit in plan.units}

        self.assertIn("Setting", payloads)
        self.assertIn("Description", payloads)
        self.assertIn("Standard channel mode", payloads)
        translations = {
            unit.id: {
                "Setting": "Настройка",
                "Description": "Описание",
                "Default": "По умолчанию",
                "Standard channel mode": "Стандартный режим каналов",
            }[unit.payload]
            for unit in plan.units
        }
        result = plan.apply(translations)
        self.assertEqual(
            result.text,
            "| Настройка | Описание |\n"
            "| --- | --- |\n"
            "| По умолчанию | Стандартный режим каналов |\n",
        )

    def test_wrapped_paragraph_is_one_contextual_unit(self) -> None:
        source = (
            "Spatial cells can store regions across\n"
            "dimensions.\n"
        )
        plan = self.registry.plan(
            "assets/ae2/ae2guide/spatial.md",
            source,
            "ru_ru",
        )

        self.assertEqual(len(plan.units), 1)
        self.assertIn("Spatial cells", plan.units[0].payload)
        self.assertIn("dimensions.", plan.units[0].payload)
        self.assertIn("FK", plan.units[0].payload)

    def test_wrapped_list_continuation_is_one_contextual_unit(self) -> None:
        source = (
            "- No empty spaces may be filled with "
            '<ItemLink id="advanced_ae:quantum_storage" />\n'
            "for no additional benefits;\n"
            '- Exactly one <ItemLink id="ae2:controller" />;\n'
        )

        plan = self.registry.plan(
            "assets/advanced_ae/ae2guide/quantum_computer.md",
            source,
            "ru_ru",
        )

        self.assertEqual(len(plan.units), 2)
        self.assertIn("No empty spaces", plan.units[0].payload)
        self.assertIn("for no additional benefits", plan.units[0].payload)
        self.assertNotIn("Exactly one", plan.units[0].payload)

    def test_article_may_disappear_around_protected_item_link(self) -> None:
        source = 'The <ItemLink id="ae2:controller" /> stores energy.\n'
        plan = self.registry.plan(
            "assets/ae2/ae2guide/controller.md",
            source,
            "ru_ru",
        )
        unit = plan.units[0]
        candidate = unit.payload.replace("The ", "").replace(
            " stores energy.",
            " хранит энергию.",
        )
        target_lang = {
            "file": "ru_ru",
            "api": "ru",
            "regex": r"[А-Яа-яЁё]",
        }

        reason = plan.candidate_error(
            unit.id,
            candidate,
            lambda original, translated: (
                "incomplete visible text"
                if translation_needs_repair(original, translated, target_lang)
                else None
            ),
        )

        self.assertIsNone(reason)

    def test_article_may_disappear_before_standalone_technical_label(self) -> None:
        plan = self.registry.plan(
            "assets/ae2/ae2guide/ui.md",
            "The UI\n",
            "ru_ru",
        )
        unit = plan.units[0]
        target_lang = {
            "file": "ru_ru",
            "api": "ru",
            "regex": r"[А-Яа-яЁё]",
        }

        reason = JarProcessor._formatkit_reason(
            plan,
            unit.id,
            "UI",
            target_lang,
        )

        self.assertIsNone(reason)

    def test_untranslated_markdown_link_label_is_still_rejected(self) -> None:
        source = "Read the [Quantum Bridge](quantum_bridge.md) guide.\n"
        plan = self.registry.plan(
            "assets/ae2/ae2guide/quantum_bridge.md",
            source,
            "ru_ru",
        )
        unit = plan.units[0]
        candidate = unit.payload.replace("Read the ", "Прочитайте ").replace(
            " guide.",
            " руководство.",
        )
        target_lang = {
            "file": "ru_ru",
            "api": "ru",
            "regex": r"[А-Яа-яЁё]",
        }

        reason = plan.candidate_error(
            unit.id,
            candidate,
            lambda original, translated: (
                "incomplete visible text"
                if translation_needs_repair(original, translated, target_lang)
                else None
            ),
        )

        self.assertIsNotNone(reason)

    def test_wrapped_links_and_tags_remain_protected(self) -> None:
        source = (
            "Read [Pattern\nProviders](ae2:pattern_provider.md) and use "
            '<ItemLink\nid="ae2:memory_card" />.\n'
        )
        plan = self.registry.plan(
            "assets/mae2/ae2guide/page.md",
            source,
            "ru_ru",
        )

        self.assertEqual(plan.apply({}).text, source)
        payload = plan.units[0].payload
        self.assertIn("Pattern", payload)
        self.assertIn("Providers", payload)
        self.assertNotIn("pattern_provider.md", payload)
        self.assertNotIn("ItemLink", payload)

    def test_fenced_code_block_is_never_a_translation_unit(self) -> None:
        source = "Before text.\n\n```js\nconst label = 'Do not translate';\n```\n"
        plan = self.registry.plan(
            "assets/ae2/ae2guide/code.md",
            source,
            "ru_ru",
        )

        payloads = "\n".join(unit.payload for unit in plan.units)
        self.assertIn("Before text", payloads)
        self.assertNotIn("Do not translate", payloads)
        self.assertEqual(plan.apply({}).text, source)


if __name__ == "__main__":
    unittest.main()
