import json
import unittest

from mineai.constants import LANGUAGES
from mineai.engines.base import EngineCallbacks, TranslationEngine
from mineai.engines.service import TranslationService
from mineai.processors.formatkit_books_pilot_v341 import FormatKitBooksJarProcessor as V341BooksJarProcessor
from mineai.formatkit_books_bridge import (
    build_book_output,
    plan_book_work,
    semantic_resolved_values,
    split_semantic_book_pending,
    validate_book_candidate,
)

RU = LANGUAGES["Русский"]


class _Config:
    def getboolean(self, section, key):
        if (section, key) == ("GENERAL", "smart_glue"):
            return True
        if (section, key) == ("AI", "fallback_google"):
            return False
        raise AssertionError((section, key))

    def getint(self, _section, _key, fallback=0):
        return fallback


class _Cache:
    def __init__(self):
        self.values = {}
        self.discarded = []

    def get(self, api_code, source):
        value = self.values.get((api_code, source))
        return (value, False) if value is not None else (None, False)

    def set(self, api_code, source, translated):
        self.values[(api_code, source)] = translated

    def set_identity(self, api_code, source):
        self.values[(api_code, source)] = source

    def discard(self, api_code, source, *, include_imported=False):
        self.values.pop((api_code, source), None)
        self.discarded.append((api_code, source, include_imported))

    def save_if_threshold(self):
        pass


class _Engine(TranslationEngine):
    def __init__(self, responses):
        self.responses = responses

    def translate_batch(self, items, target_lang, callbacks):
        return {
            key: self.responses.get(item.original, item.original)
            for key, item in items.items()
        }


class _Service(TranslationService):
    def __init__(self, cache, responses):
        super().__init__("ai", cache, _Config(), ai_batch=20)
        self.engine = _Engine(responses)

    def _build_engine(self, context="", prompt_type="mods"):
        return self.engine


def _callbacks(logs):
    return EngineCallbacks(
        should_run=lambda: True,
        wait_if_paused=lambda: None,
        on_log=lambda message, tag: logs.append((message, tag)),
        on_status=lambda _message: None,
        on_progress=lambda _count: None,
    )


class V341RuntimeWiringTests(unittest.TestCase):
    def test_runtime_wrapper_is_a_narrow_subclass(self):
        from mineai.processors.formatkit_books_pilot import FormatKitBooksJarProcessor as Base

        self.assertTrue(issubclass(V341BooksJarProcessor, Base))
        self.assertIsNot(V341BooksJarProcessor, Base)


class PatchouliSemanticAnchorGuardV341Tests(unittest.TestCase):
    path = "assets/apotheosis/patchouli_books/apoth_chronicle/en_us/entries/adventure/affixes.json"

    @staticmethod
    def source(text="An $(6)Affix$() is the driving force behind Affix Items."):
        return json.dumps(
            {"pages": [{"type": "patchouli:text", "text": text}]},
            separators=(",", ":"),
        )

    def _work(self, source=None):
        work = plan_book_work(
            self.path,
            source or self.source(),
            "ru_ru",
            RU["regex"],
            None,
            "force",
        )
        assert work is not None
        return work

    def test_semantic_child_is_scheduled_before_dependent_parent(self):
        work = self._work()
        children, remaining = split_semantic_book_pending(work)
        child = next(unit for unit in work.source_plan.units if unit.kind == "patchouli-semantic-child")
        parent = next(unit for unit in work.source_plan.units if unit.kind == "patchouli-text")
        self.assertEqual(set(children), {child.id})
        self.assertIn(parent.id, remaining)
        self.assertNotIn(parent.id, children)

    def test_repeating_translated_child_next_to_anchor_is_rejected(self):
        work = self._work()
        child = next(unit for unit in work.source_plan.units if unit.kind == "patchouli-semantic-child")
        parent = next(unit for unit in work.source_plan.units if unit.kind == "patchouli-text")
        resolved = {child.id: "Аффикс"}
        bad = parent.text.replace("An ", "Аффикс ").replace(
            " is the driving force behind Affix Items.",
            " является движущей силой предметов с аффиксами.",
        )
        ok, reason = validate_book_candidate(
            work, parent.id, bad, resolved_semantic=resolved
        )
        self.assertFalse(ok)
        self.assertIn("duplicates translated child", reason)

    def test_good_parent_keeps_style_owned_by_child(self):
        work = self._work()
        child = next(unit for unit in work.source_plan.units if unit.kind == "patchouli-semantic-child")
        parent = next(unit for unit in work.source_plan.units if unit.kind == "patchouli-text")
        resolved = {child.id: "Аффикс"}
        good = parent.text.replace("An ", "").replace(
            " is the driving force behind Affix Items.",
            " является движущей силой предметов с аффиксами.",
        )
        ok, reason = validate_book_candidate(
            work, parent.id, good, resolved_semantic=resolved
        )
        self.assertTrue(ok, reason)
        output = json.loads(build_book_output(work, {child.id: "Аффикс", parent.id: good}))
        text = output["pages"][0]["text"]
        self.assertIn("$(6)Аффикс$() является", text)
        self.assertNotIn("Аффикс $(6)Аффикс$()", text)

    def test_source_owned_repetition_is_not_a_false_positive(self):
        source = self.source("Use $(thing)very$() very carefully.")
        work = self._work(source)
        child = next(unit for unit in work.source_plan.units if unit.kind == "patchouli-semantic-child")
        parent = next(unit for unit in work.source_plan.units if unit.kind == "patchouli-text")
        resolved = {child.id: "очень"}
        candidate = parent.text.replace("Use ", "Используйте ").replace(
            " very carefully.", " очень осторожно."
        )
        ok, reason = validate_book_candidate(
            work, parent.id, candidate, resolved_semantic=resolved
        )
        self.assertTrue(ok, reason)

    def test_bad_parent_cache_is_discarded_before_it_can_reappear(self):
        work = self._work()
        children, remaining = split_semantic_book_pending(work)
        child = next(unit for unit in work.source_plan.units if unit.kind == "patchouli-semantic-child")
        parent = next(unit for unit in work.source_plan.units if unit.kind == "patchouli-text")
        self.assertEqual(set(children), {child.id})
        resolved = {child.id: "Аффикс"}
        bad = parent.text.replace("An ", "Аффикс ").replace(
            " is the driving force behind Affix Items.",
            " является движущей силой предметов с аффиксами.",
        )
        good = parent.text.replace("An ", "").replace(
            " is the driving force behind Affix Items.",
            " является движущей силой предметов с аффиксами.",
        )
        cache = _Cache()
        cache.values[("ru", parent.text)] = bad
        service = _Service(cache, {parent.text: good})
        logs = []
        result = service.translate_dict(
            remaining,
            RU,
            _callbacks(logs),
            context="Apotheosis",
            candidate_validator=lambda unit_id, candidate: validate_book_candidate(
                work,
                unit_id,
                candidate,
                resolved_semantic=resolved,
            ),
            preserve_source_structure=True,
        )
        self.assertEqual(result[parent.id], good)
        self.assertTrue(any(entry[:2] == ("ru", parent.text) for entry in cache.discarded))
        self.assertNotEqual(cache.values[("ru", parent.text)], bad)


class IeSemanticAnchorGuardV341Tests(unittest.TestCase):
    path = "assets/immersiveengineering/manual/en_us/mining_drill.txt"

    def test_ie_parent_cannot_repeat_drill_head_outside_style(self):
        source = "The §2Drill Head§r determines the mining speed.\n"
        work = plan_book_work(self.path, source, "ru_ru", RU["regex"], None, "force")
        assert work is not None
        child = next(unit for unit in work.source_plan.units if unit.kind == "ie-format-semantic-child")
        parent = next(unit for unit in work.source_plan.units if unit.kind == "ie-manual-prose")
        resolved = {child.id: "Буровая головка"}
        bad = parent.text.replace("The ", "Буровая головка ").replace(
            "determines the mining speed.", "определяет скорость добычи."
        )
        ok, reason = validate_book_candidate(
            work, parent.id, bad, resolved_semantic=resolved
        )
        self.assertFalse(ok)
        self.assertIn("duplicates translated child", reason)

        good = parent.text.replace("The ", "").replace(
            "determines the mining speed.", "определяет скорость добычи."
        )
        ok, reason = validate_book_candidate(
            work, parent.id, good, resolved_semantic=resolved
        )
        self.assertTrue(ok, reason)
        output = build_book_output(work, {child.id: "Буровая головка", parent.id: good})
        self.assertIn("§2Буровая головка§r определяет", output)
        self.assertNotIn("Буровая головка §2Буровая головка§r", output)


if __name__ == "__main__":
    unittest.main()
