import json
import unittest

from mineai.constants import LANGUAGES
from mineai.formatkit_books_bridge import plan_book_work, validate_book_candidate


RU = LANGUAGES["Русский"]


class ApotheosisSemanticCacheRegressionV341Tests(unittest.TestCase):
    def test_cached_synonym_cannot_hide_extra_label_before_semantic_anchor(self):
        path = (
            "assets/apotheosis/patchouli_books/apoth_chronicle/en_us/entries/"
            "adventure/affix_loot/affixes.json"
        )
        source = json.dumps(
            {
                "pages": [
                    {
                        "type": "patchouli:text",
                        "text": (
                            "An $(6)Affix$() is the driving force behind Affix Items. "
                            "Each affix is very similar to an enchantment, providing a "
                            "specific bonus to the item it is attached to.$(p)This book "
                            "will not list all the affixes, as that is for you to figure "
                            "out, but it will help explain them."
                        ),
                    }
                ]
            },
            separators=(",", ":"),
        )
        work = plan_book_work(path, source, "ru_ru", RU["regex"], None, "force")
        assert work is not None
        child = next(
            unit
            for unit in work.source_plan.units
            if unit.kind == "patchouli-semantic-child"
        )
        parent = next(
            unit
            for unit in work.source_plan.units
            if unit.kind == "patchouli-text"
        )

        # Exact conflict observed in the real acceptance cache: the child had a
        # synonym while the parent independently invented another Affix label.
        bad_cached_parent = (
            "Аффикс [#0#] — это движущая сила предметов с аффиксами. "
            "Каждый аффикс очень похож на зачарование, предоставляя определённый "
            "бонус предмету, к которому он прикреплён.[#1#]В этой книге не "
            "перечислены все аффиксы, так как это нужно выяснить вам, но она "
            "поможет их объяснить."
        )
        ok, reason = validate_book_candidate(
            work,
            parent.id,
            bad_cached_parent,
            resolved_semantic={child.id: "Прикрепление"},
        )

        self.assertFalse(ok)
        self.assertIn("introduces an extra label", reason)


if __name__ == "__main__":
    unittest.main()
