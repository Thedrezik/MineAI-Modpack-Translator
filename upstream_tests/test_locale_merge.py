import json
import unittest

from mineai_formatkit.core import ValidationError
from mineai_formatkit.locale_merge import LocaleMergePlanner


class LocaleMergePlannerTests(unittest.TestCase):
    def setUp(self):
        self.planner = LocaleMergePlanner()
        self.path = "assets/example/lang/en_us.json"

    def test_append_uses_english_as_canonical_structure(self):
        en = '{\n  "a": "Hello",\n  "b": "World",\n  "c": "%s items"\n}\n'
        ru = '{"a":"Привет","c":"%s предметов","old":"Старое"}'
        plan = self.planner.plan(self.path, en, "ru_ru", ru, mode="append")
        self.assertEqual(plan.pending_ids, ("key:b",))
        self.assertEqual(plan.orphan_target_keys, ("old",))
        out = self.planner.build(plan, {"key:b": "Мир"})
        self.assertEqual(list(json.loads(out)), ["a", "b", "c"])
        self.assertEqual(json.loads(out), {"a":"Привет","b":"Мир","c":"%s предметов"})

    def test_force_retranslates_every_source_value(self):
        en = '{"a":"Hello","b":"World"}'
        ru = '{"a":"Привет","b":"Мир"}'
        plan = self.planner.plan(self.path, en, "ru_ru", ru, mode="force")
        self.assertEqual(set(plan.pending_ids), {"key:a", "key:b"})

    def test_invalid_existing_placeholders_are_forced_pending(self):
        en = '{"count":"%s items"}'
        ru = '{"count":"предметов"}'
        plan = self.planner.plan(self.path, en, "ru_ru", ru, mode="append")
        self.assertEqual(plan.invalid_existing_keys, ("count",))
        self.assertEqual(plan.pending_ids, ("key:count",))

    def test_build_requires_all_pending_translations(self):
        plan = self.planner.plan(self.path, '{"a":"Hello"}', "ru_ru", None, mode="append")
        with self.assertRaises(ValidationError):
            self.planner.build(plan, {})

class LocaleMergeExistingFormattingTests(unittest.TestCase):
    def test_existing_target_may_change_linebreaks_and_minecraft_style(self):
        planner = LocaleMergePlanner()
        en = '{"a":"Line one\\nLine two","b":"The beginning"}'
        ru = '{"a":"Строка один и строка два","b":"§mПервое§r начало"}'
        plan = planner.plan("assets/demo/lang/en_us.json", en, "ru_ru", ru, "append")
        self.assertEqual(plan.pending_ids, ())
        out = json.loads(planner.build(plan, {}))
        self.assertEqual(out["a"], "Строка один и строка два")
        self.assertEqual(out["b"], "§mПервое§r начало")
