import unittest

from mineai.processors.snbt_extract import apply_snbt_translations, extract_snbt_strings
from mineai.text_processing import mask_protected_fragments, unmask_translation


class RegexSafetyTests(unittest.TestCase):
    def test_formatkit_anchors_are_hidden_from_translation_engine(self) -> None:
        source = "The ⟦FK0000⟧ terminal"

        masked, mapping = mask_protected_fragments(source)

        self.assertNotIn("⟦FK0000⟧", masked)
        self.assertIn("⟦FK0000⟧", mapping.values())
        self.assertEqual(unmask_translation(masked, mapping), source)

    def test_protected_ignore_terms_are_case_insensitive(self) -> None:
        masked, mapping = mask_protected_fragments("Open GUI and gui settings")

        self.assertNotIn("GUI", masked)
        self.assertNotIn("gui", masked.casefold())
        self.assertIn("GUI", mapping.values())
        self.assertIn("gui", mapping.values())

    def test_generated_markers_are_not_reparsed_as_markdown_syntax(self) -> None:
        sources = (
            "**Hold a Wrench to see the missing blocks and the errors!** "
            "You can also hold a Hatch to know where it can go.",
            "Labeler while holding **Shift**(default, keybinding configurable).",
            r"Use \**italics*\* or \_\_bold\_\_ as literal examples.",
            "***Applied Mekanistics***",
        )

        for source in sources:
            with self.subTest(source=source):
                masked, mapping = mask_protected_fragments(source)
                self.assertEqual(unmask_translation(masked, mapping), source)
                self.assertFalse(any("[#" in value for value in mapping.values()))

        _masked, mapping = mask_protected_fragments(sources[0])
        self.assertFalse(any(value.startswith("![") for value in mapping.values()))

    def test_json_text_component_exposes_only_display_text_to_ai(self) -> None:
        source = '{"text":"Purple Chalk","color":"#9D7AD0"}'

        masked, mapping = mask_protected_fragments(source)

        self.assertIn("Purple Chalk", masked)
        self.assertNotIn('"text"', masked)
        self.assertNotIn("#9D7AD0", masked)
        translated = unmask_translation(
            masked.replace("Purple Chalk", "Фиолетовый мел"),
            mapping,
        )
        self.assertEqual(
            translated,
            '{"text":"Фиолетовый мел","color":"#9D7AD0"}',
        )

    def test_escaped_json_text_component_preserves_exact_structure(self) -> None:
        source = r'{\"text\":\"Basic Fluid Tank\",\"color\":\"#5EFCB6\"}'

        masked, mapping = mask_protected_fragments(source)

        self.assertIn("Basic Fluid Tank", masked)
        self.assertNotIn("#5EFCB6", masked)
        translated = unmask_translation(
            masked.replace("Basic Fluid Tank", "Базовый жидкостный бак"),
            mapping,
        )
        self.assertEqual(
            translated,
            r'{\"text\":\"Базовый жидкостный бак\",\"color\":\"#5EFCB6\"}',
        )

    def test_format_regex_round_trip_preserves_special_fragments(self) -> None:
        source = "Value {name} ![icon](guide.md) and %1$s"
        masked, mapping = mask_protected_fragments(source)
        self.assertEqual(unmask_translation(masked, mapping), source)
        self.assertEqual(len(mapping), 4)

    def test_patchouli_colors_are_masked_as_complete_format_codes(self) -> None:
        source = (
            "$(#BB00BB)Epic$() items and $(#ED7014)Mythic$() items"
        )

        masked, mapping = mask_protected_fragments(source)

        self.assertNotIn("BB00BB", masked)
        self.assertNotIn("ED7014", masked)
        self.assertIn("$(#BB00BB)", mapping.values())
        self.assertIn("$(#ED7014)", mapping.values())
        self.assertIn("$()", mapping.values())
        self.assertNotIn("BB00BB", mapping.values())
        self.assertNotIn("ED7014", mapping.values())
        self.assertFalse(any("[#" in fragment for fragment in mapping.values()))

    def test_unmask_translation_keeps_russian_text_and_resolves_nested_marker(self) -> None:
        mapping = {
            "[#0#]": "ED7014",
            "[#1#]": "$(#[#0#])",
        }

        translated = unmask_translation(
            "[#1#]Мифические$() предметы были выкованы великой силой.",
            mapping,
        )

        self.assertEqual(
            translated,
            "$(#ED7014)Мифические$() предметы были выкованы великой силой.",
        )

    def test_snbt_regex_handles_quoted_keys_and_escaped_quotes(self) -> None:
        content = '{"title": "A \\"quoted\\" title", description: ["First line", "Second line"]}'
        strings = extract_snbt_strings(content)
        translated = apply_snbt_translations(content, {
            'A \\"quoted\\" title': 'Перевод \\"цитата\\"',
            "First line": "Первая строка",
        })
        self.assertIn('A \\"quoted\\" title', strings)
        self.assertIn("First line", strings)
        self.assertIn('Перевод \\"цитата\\"', translated)
        self.assertIn('"Первая строка"', translated)


if __name__ == "__main__":
    unittest.main()
