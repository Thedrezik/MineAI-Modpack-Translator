import unittest

from mineai.processors.snbt_extract import apply_snbt_translations, extract_snbt_strings
from mineai.text_processing import mask_protected_fragments, unmask_translation


class RegexSafetyTests(unittest.TestCase):
    def test_format_regex_round_trip_preserves_special_fragments(self) -> None:
        source = "Value {name} ![icon](guide.md) and %1$s"
        masked, mapping = mask_protected_fragments(source)
        self.assertEqual(unmask_translation(masked, mapping), source)
        self.assertEqual(len(mapping), 4)

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
