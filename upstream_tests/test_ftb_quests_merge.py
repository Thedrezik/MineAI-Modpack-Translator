import unittest

from mineai_formatkit.core import ValidationError
from mineai_formatkit.ftb_quests import FtbQuestsLangAdapter
from mineai_formatkit.ftb_quests_merge import FtbQuestsLocaleMergePlanner


class FtbQuestsLocaleMergePlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = "config/ftbquests/quests/lang/en_us.snbt"
        self.planner = FtbQuestsLocaleMergePlanner()
        self.source = r'''{
chapter.001.title: "Welcome"
quest.002.quest_desc: [
    "&aBuild&r a machine"
    "Use /home after setup"
    "Turn it on/off and choose item/fluid mode"
]
quest.003.title: "Untranslated"
}'''

    def test_append_reuses_only_safe_target_wording_and_rebuilds_from_english(self) -> None:
        target = r'''{
chapter.001.title: "Добро пожаловать"
quest.002.quest_desc: [
    "&bПостройте&r машину"
    "Используйте /home после настройки"
    "Включите или выключите и выберите режим предмета/жидкости"
]
quest.003.title: "Untranslated"
stale.title: "Старое"
}'''
        plan = self.planner.plan(self.path, self.source, "ru_ru", target, mode="append")
        self.assertEqual(plan.orphan_target_keys, ("stale.title",))
        self.assertEqual(plan.untranslated_ids, ("key:quest.003.title",))
        self.assertEqual(plan.pending_ids, ("key:quest.003.title",))
        output = self.planner.build(plan, {"key:quest.003.title": "Переведено"})
        self.assertEqual(
            FtbQuestsLangAdapter().fingerprint(output),
            FtbQuestsLangAdapter().fingerprint(self.source),
        )
        self.assertNotIn("stale.title", output)
        self.assertIn("&aПостройте&r машину", output)
        self.assertIn("/home", output)
        self.assertIn("предмета/жидкости", output)

    def test_shape_mismatch_forces_entire_key_back_to_source_structure(self) -> None:
        target = r'''{
chapter.001.title: "Добро пожаловать"
quest.002.quest_desc: ["Сломанная одна строка"]
quest.003.title: "Переведено"
}'''
        plan = self.planner.plan(self.path, self.source, "ru_ru", target, mode="append")
        self.assertEqual(plan.shape_mismatch_keys, ("quest.002.quest_desc",))
        desc_ids = tuple(
            unit.id for unit in plan.source_plan.units if unit.context == "quest.002.quest_desc"
        )
        self.assertTrue(set(desc_ids).issubset(set(plan.pending_ids)))

    def test_force_ignores_existing_target_and_translates_every_unit(self) -> None:
        plan = self.planner.plan(
            self.path,
            self.source,
            "ru_ru",
            target_text="not valid snbt at all",
            mode="force",
        )
        self.assertEqual(len(plan.pending_ids), len(plan.source_plan.units))
        translations = {unit.id: "RU " + unit.text for unit in plan.source_plan.units}
        output = self.planner.build(plan, translations)
        self.assertEqual(
            FtbQuestsLangAdapter().fingerprint(output),
            FtbQuestsLangAdapter().fingerprint(self.source),
        )

    def test_missing_or_unknown_translations_fail_closed(self) -> None:
        plan = self.planner.plan(self.path, self.source, "ru_ru", None, mode="append")
        with self.assertRaises(ValidationError):
            self.planner.build(plan, {})
        with self.assertRaises(ValidationError):
            self.planner.build(
                plan,
                {**{unit.id: unit.text for unit in plan.source_plan.units}, "unknown": "x"},
            )

    def test_slash_words_are_translatable_but_real_commands_remain_protected(self) -> None:
        adapter = self.planner.adapter
        plan = adapter.prepare(self.path, self.source)
        command = next(u for u in plan.units if "Use " in u.text)
        slash_words = next(u for u in plan.units if "on/off" in u.text)
        self.assertEqual([fragment.value for fragment in command.protected], ["/home"])
        self.assertEqual(slash_words.protected, ())

    def test_public_export_and_registry_capability(self) -> None:
        from mineai_formatkit import FtbQuestsLocaleMergePlanner as PublicPlanner
        from mineai_formatkit.registry import FormatRegistry

        self.assertIs(PublicPlanner, FtbQuestsLocaleMergePlanner)
        result = FormatRegistry.default().detect_result(self.path)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.capabilities.supports_existing_target_merge)


if __name__ == "__main__":
    unittest.main()
