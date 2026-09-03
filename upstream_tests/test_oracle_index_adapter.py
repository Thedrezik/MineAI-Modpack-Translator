import unittest

from mineai_formatkit.oracle_index import OracleIndexMdxAdapter, OracleIndexMetaJsonAdapter


class OracleIndexMdxAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = OracleIndexMdxAdapter()
        self.path = "oracle_index/books/demo/.content/start.mdx"
        self.source = '''---
title: Getting Started
id: demo:getting_started
related_items: [demo:guide]
type: item
---

<PrefabObtaining/>

The **Machine** has a 75% chance to succeed. See [Power](@demo:power).

Use `demo:machine` in configs.

| Result | Chance |
| --- | --- |
| Good | 75% |
'''

    def test_matches_target_and_localized_tree_exclusion(self) -> None:
        self.assertTrue(self.adapter.matches(self.path))
        self.assertFalse(
            self.adapter.matches("oracle_index/books/demo/.translated/ru_ru/.content/start.mdx")
        )
        self.assertFalse(
            self.adapter.matches("oracle_index/books/demo/translated/ja_jp/content/start.mdx")
        )
        self.assertEqual(
            self.adapter.target_path(self.path, "ru_ru"),
            "oracle_index/books/demo/.translated/ru_ru/.content/start.mdx",
        )

    def test_identity_and_structural_translation(self) -> None:
        plan = self.adapter.prepare(self.path, self.source)
        self.assertEqual(
            self.adapter.apply(plan, {unit.id: unit.text for unit in plan.units}),
            self.source,
        )
        translated = self.adapter.apply(
            plan, {unit.id: "ТЕСТ " + unit.text for unit in plan.units}
        )
        self.assertEqual(self.adapter.fingerprint(translated), self.adapter.fingerprint(self.source))
        self.assertIn("<PrefabObtaining/>", translated)
        self.assertIn("(@demo:power)", translated)
        self.assertIn("`demo:machine`", translated)

    def test_percent_prose_is_not_a_printf_placeholder(self) -> None:
        plan = self.adapter.prepare(self.path, self.source)
        unit = next(unit for unit in plan.units if "75% chance" in unit.text)
        self.assertIn("75% chance", unit.text)
        self.assertFalse(any("% c" in fragment.value for fragment in unit.protected))


class OracleIndexMetaJsonAdapterTests(unittest.TestCase):
    def test_meta_labels_are_translated_but_keys_are_not(self) -> None:
        adapter = OracleIndexMetaJsonAdapter()
        path = "oracle_index/books/demo/.content/_meta.json"
        source = '{\n  "machines": "Machines",\n  "start.mdx": "Getting Started"\n}'
        self.assertTrue(adapter.matches(path))
        self.assertEqual(
            adapter.target_path(path, "ru_ru"),
            "oracle_index/books/demo/.translated/ru_ru/.content/_meta.json",
        )
        plan = adapter.prepare(path, source)
        self.assertEqual(len(plan.units), 2)
        output = adapter.apply(plan, {unit.id: "ТЕСТ " + unit.text for unit in plan.units})
        self.assertIn('"machines": "ТЕСТ Machines"', output)
        self.assertIn('"start.mdx": "ТЕСТ Getting Started"', output)
        self.assertFalse(adapter.matches("oracle_index/books/demo/.translated/zh_cn/.content/_meta.json"))
        self.assertFalse(adapter.matches("oracle_index/books/demo/translated/zh_cn/content/_meta.json"))


if __name__ == "__main__":
    unittest.main()
