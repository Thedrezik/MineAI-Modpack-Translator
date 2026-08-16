import json
import unittest

from mineai.constants import LANGUAGES
from mineai.formatkit_books_bridge import build_book_output, plan_book_work


RU = LANGUAGES["Русский"]


class AtomicSemanticFallbackV341Tests(unittest.TestCase):
    path = (
        "assets/apotheosis/patchouli_books/apoth_chronicle/en_us/"
        "entries/adventure/affix_loot/affixes.json"
    )

    def _work(self):
        source = json.dumps(
            {
                "pages": [
                    {
                        "type": "patchouli:text",
                        "text": "An $(6)Affix$() is the driving force behind Affix Items.",
                    }
                ]
            },
            separators=(",", ":"),
        )
        work = plan_book_work(
            self.path,
            source,
            "ru_ru",
            RU["regex"],
            None,
            "force",
        )
        self.assertIsNotNone(work)
        return work

    def test_rejected_parent_does_not_mix_translated_child_into_source_fallback(self):
        work = self._work()
        child = next(
            unit
            for unit in work.source_plan.units
            if unit.kind == "patchouli-semantic-child"
        )

        # The child succeeded, but the parent is intentionally absent from the
        # result to model a validator rejection. The entire semantic fragment
        # must fall back to canonical source instead of mixing languages.
        output = json.loads(build_book_output(work, {child.id: "Прикрепление"}))
        text = output["pages"][0]["text"]

        self.assertEqual(
            text,
            "An $(6)Affix$() is the driving force behind Affix Items.",
        )
        self.assertNotIn("$(6)Прикрепление$()", text)


if __name__ == "__main__":
    unittest.main()
