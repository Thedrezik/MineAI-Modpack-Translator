import unittest

from mineai_formatkit.core import ValidationError
from mineai_formatkit.patchouli import PatchouliBookJsonAdapter


class PatchouliBookJsonAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = PatchouliBookJsonAdapter()
        self.path = "assets/demo/patchouli_books/guide/en_us/entries/start.json"
        self.source = '''{
  "name": "Getting Started",
  "icon": "demo:guide",
  "category": "demo:intro",
  "pages": [
    {
      "type": "patchouli:text",
      "title": "Welcome",
      "text": "The $(major)Machine/$ uses $(l:demo:power)Power/$.$(br2)Keep 75% charge."
    },
    {
      "type": "demo:custom_page",
      "a.heading": "First Recipe",
      "a.recipe": "demo:first_recipe",
      "array.text": "$(italic)Do not translate markup$()"
    }
  ]
}'''

    def test_matches_and_target_path(self) -> None:
        self.assertTrue(self.adapter.matches(self.path))
        self.assertFalse(self.adapter.matches("assets/demo/models/item/foo.json"))
        self.assertFalse(
            self.adapter.matches("assets/demo/patchouli_books/guide/ru_ru/entries/start.json")
        )
        self.assertEqual(
            self.adapter.target_path(self.path, "ru_ru"),
            "assets/demo/patchouli_books/guide/ru_ru/entries/start.json",
        )

    def test_only_visible_fields_are_exposed(self) -> None:
        plan = self.adapter.prepare(self.path, self.source)
        texts = {unit.text for unit in plan.units}
        self.assertTrue(any("Getting Started" in text for text in texts))
        self.assertTrue(any("Welcome" in text for text in texts))
        self.assertTrue(any("First Recipe" in text for text in texts))
        self.assertFalse(any("demo:first_recipe" in text for text in texts))
        self.assertFalse(any("demo:guide" in text for text in texts))
        self.assertFalse(any("patchouli:text" in text for text in texts))

    def test_identity_is_byte_exact_and_synthetic_is_structure_safe(self) -> None:
        plan = self.adapter.prepare(self.path, self.source)
        identity = self.adapter.apply(plan, {unit.id: unit.text for unit in plan.units})
        self.assertEqual(identity, self.source)
        translated = self.adapter.apply(
            plan, {unit.id: "ТЕСТ " + unit.text for unit in plan.units}
        )
        self.assertEqual(self.adapter.fingerprint(translated), self.adapter.fingerprint(self.source))
        self.assertIn('"a.recipe": "demo:first_recipe"', translated)

    def test_patchouli_markup_must_survive_exactly(self) -> None:
        plan = self.adapter.prepare(self.path, self.source)
        unit = next(unit for unit in plan.units if "Machine" in unit.text)
        self.assertGreaterEqual(len(unit.protected), 5)
        with self.assertRaises(ValidationError):
            self.adapter.apply(plan, {unit.id: "Перевод без обязательных токенов"})

    def test_duplicate_keys_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self.adapter.prepare(
                self.path,
                '{"name":"A","name":"B","icon":"demo:x"}',
            )


if __name__ == "__main__":
    unittest.main()
