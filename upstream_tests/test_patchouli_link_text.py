import unittest

from mineai_formatkit import PatchouliBookJsonAdapter


class PatchouliLiteralLinkTextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = PatchouliBookJsonAdapter()
        self.path = "assets/demo/patchouli_books/guide/en_us/entries/links.json"

    def test_literal_link_text_is_translatable_but_translation_keys_are_not(self) -> None:
        source = '''{
  "name": "Links",
  "pages": [
    {
      "type": "patchouli:link",
      "url": "https://example.test/api",
      "link_text": "API documentation"
    },
    {
      "type": "patchouli:link",
      "url": "https://example.test/fix",
      "link_text": "Click to rectify"
    },
    {
      "type": "patchouli:link",
      "url": "https://example.test/patreon",
      "link_text": "ars_nouveau.patreon_text"
    },
    {
      "type": "patchouli:link",
      "url": "https://example.test/video",
      "link_text": "booklet.actuallyadditions.chapter.video_guide.booty.button"
    }
  ]
}'''
        plan = self.adapter.prepare(self.path, source)
        texts = {unit.text for unit in plan.units}
        self.assertIn("API documentation", texts)
        self.assertIn("Click to rectify", texts)
        self.assertNotIn("ars_nouveau.patreon_text", texts)
        self.assertNotIn("booklet.actuallyadditions.chapter.video_guide.booty.button", texts)

        identity = self.adapter.apply(plan, {unit.id: unit.text for unit in plan.units})
        self.assertEqual(identity, source)

        translated = {
            unit.id: (
                "Документация API" if unit.text == "API documentation"
                else "Нажмите для исправления" if unit.text == "Click to rectify"
                else unit.text
            )
            for unit in plan.units
        }
        output = self.adapter.apply(plan, translated)
        self.assertIn('"link_text": "Документация API"', output)
        self.assertIn('"link_text": "Нажмите для исправления"', output)
        self.assertIn('"link_text": "ars_nouveau.patreon_text"', output)
        self.assertIn(
            '"link_text": "booklet.actuallyadditions.chapter.video_guide.booty.button"',
            output,
        )
        self.assertEqual(self.adapter.fingerprint(output), self.adapter.fingerprint(source))

    def test_single_token_link_text_stays_conservatively_unsupported(self) -> None:
        source = '{"name":"Links","pages":[{"type":"patchouli:link","link_text":"Documentation"}]}'
        plan = self.adapter.prepare(self.path, source)
        self.assertNotIn("Documentation", {unit.text for unit in plan.units})


if __name__ == "__main__":
    unittest.main()
