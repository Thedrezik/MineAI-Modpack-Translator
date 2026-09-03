import unittest

from mineai_formatkit.core import ValidationError
from mineai_formatkit.locale_merge import LocaleMergePlanner
from mineai_formatkit.minecraft_lang import MinecraftLangJsonAdapter


class RealModLocaleHardeningTests(unittest.TestCase):
    def test_duplicate_metadata_section_markers_are_preserved_and_not_translated(self) -> None:
        adapter = MinecraftLangJsonAdapter()
        source = (
            '{\n'
            '  "_comment":"General",\n'
            '  "demo.one":"One",\n'
            '  "_comment":"Items",\n'
            '  "comment_id":"Section A",\n'
            '  "demo.two":"Two",\n'
            '  "comment_id":"Section B"\n'
            '}'
        )
        plan = adapter.prepare("assets/example/lang/en_us.json", source)
        self.assertEqual([unit.id for unit in plan.units], ["key:demo.one", "key:demo.two"])
        self.assertEqual(
            plan.metadata["ignored_metadata_keys"],
            ("_comment", "comment_id"),
        )
        identity = adapter.apply(plan, {unit.id: unit.text for unit in plan.units})
        self.assertEqual(identity, source)

    def test_single_metadata_named_key_remains_translatable(self) -> None:
        adapter = MinecraftLangJsonAdapter()
        source = '{"comment_id":"Visible label"}'
        plan = adapter.prepare("assets/example/lang/en_us.json", source)
        self.assertEqual([unit.id for unit in plan.units], ["key:comment_id"])
        self.assertEqual(plan.metadata["ignored_metadata_keys"], ())

    def test_duplicate_real_locale_key_still_fails_closed(self) -> None:
        adapter = MinecraftLangJsonAdapter()
        source = '{"_comment":"A","same":"one","_comment":"B","same":"two"}'
        with self.assertRaises(ValidationError):
            adapter.prepare("assets/example/lang/en_us.json", source)

    def test_locale_merge_does_not_treat_natural_percent_as_printf(self) -> None:
        planner = LocaleMergePlanner()
        source = '{"chance":"Sheep have a 50% chance to regrow wool.","arcana":"100% Arcana"}'
        target = '{"chance":"У овец 50% шанс отрастить шерсть.","arcana":"100% Арканы"}'
        plan = planner.plan(
            "assets/example/lang/en_us.json",
            source,
            "ru_ru",
            target,
            mode="append",
        )
        self.assertEqual(plan.invalid_existing_keys, ())
        self.assertEqual(plan.pending_ids, ())


if __name__ == "__main__":
    unittest.main()
