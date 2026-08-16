import json
import unittest

from mineai.constants import LANGUAGES
from mineai.formatkit_books_bridge import plan_book_work
from mineai.formatkit_profile import MineAiModonomiconBookJsonAdapter


RU = LANGUAGES["Русский"]
PATH = (
    "data/geneticsresequenced/modonomicon/books/guide/entries/negative_genes/"
    "geneticsresequenced/poison.json"
)


class ModonomiconDescriptionIdV341Tests(unittest.TestCase):
    def test_genetics_slash_description_ids_are_never_translation_units(self):
        source = json.dumps(
            {
                "description": "book.geneticsresequenced.guide.negative_genes.geneticsresequenced/poison.description",
                "name": "book.geneticsresequenced.guide.negative_genes.geneticsresequenced/poison.name",
                "pages": [
                    {
                        "type": "modonomicon:text",
                        "text": "book.geneticsresequenced.guide.negative_genes.geneticsresequenced/poison.page_0.text",
                        "title": "book.geneticsresequenced.guide.negative_genes.geneticsresequenced/poison.page_0.title",
                    },
                    {
                        "type": "modonomicon:text",
                        "text": "book.geneticsresequenced.guide.negative_genes.geneticsresequenced/poison.page_1.text",
                        "title": "book.geneticsresequenced.guide.negative_genes.geneticsresequenced/poison.page_1.title",
                    },
                ],
            },
            separators=(",", ":"),
        )
        adapter = MineAiModonomiconBookJsonAdapter()
        plan = adapter.prepare(PATH, source)
        self.assertEqual(plan.units, ())
        self.assertEqual(adapter.apply(plan, {}), source)

        work = plan_book_work(PATH, source, "ru_ru", RU["regex"], None, "force")
        self.assertIsNotNone(work)
        assert work is not None
        self.assertEqual(work.pending, {})
        self.assertEqual(work.preserved, {})
        self.assertEqual(work.passthrough, {})

    def test_literal_modonomicon_prose_still_translates(self):
        source = json.dumps(
            {
                "description": "Poison weakens the target over time.",
                "name": "Poison Gene",
                "pages": [
                    {
                        "type": "modonomicon:text",
                        "text": "This is visible player-facing prose.",
                        "title": "Poison",
                    }
                ],
            },
            separators=(",", ":"),
        )
        plan = MineAiModonomiconBookJsonAdapter().prepare(PATH, source)
        values = {unit.text for unit in plan.units}
        self.assertEqual(
            values,
            {
                "Poison weakens the target over time.",
                "Poison Gene",
                "This is visible player-facing prose.",
                "Poison",
            },
        )

    def test_prose_with_spaces_is_not_misclassified_as_description_id(self):
        source = json.dumps(
            {
                "description": "Book. Genetics is fascinating to study.",
                "pages": [],
            },
            separators=(",", ":"),
        )
        plan = MineAiModonomiconBookJsonAdapter().prepare(PATH, source)
        self.assertEqual([unit.text for unit in plan.units], ["Book. Genetics is fascinating to study."])


if __name__ == "__main__":
    unittest.main()
