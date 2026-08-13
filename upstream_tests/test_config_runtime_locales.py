import unittest

from mineai_formatkit import (
    CollapsibleGroupsConfigLangJsonAdapter,
    FormatRegistry,
    JaopcaConfigLangJsonAdapter,
    LocaleMergePlanner,
)


class RuntimeConfigLocaleTests(unittest.TestCase):
    def test_collapsible_groups_config_locale_round_trip_and_merge(self) -> None:
        adapter = CollapsibleGroupsConfigLangJsonAdapter()
        path = "config/collapsiblegroups/lang/en_us.json"
        source = '{"collapsible_groups.group.demo":"Demo Group","demo.command":"Run /create"}'
        self.assertTrue(adapter.matches(path))
        self.assertFalse(adapter.matches("config/other/lang/en_us.json"))
        self.assertEqual(
            adapter.target_path(path, "ru_ru"),
            "config/collapsiblegroups/lang/ru_ru.json",
        )
        plan = adapter.prepare(path, source)
        self.assertEqual(adapter.apply(plan, {unit.id: unit.text for unit in plan.units}), source)
        command = next(unit for unit in plan.units if unit.context == "demo.command")
        self.assertIn("/create", {fragment.value for fragment in command.protected})

        merge = LocaleMergePlanner(adapter).plan(
            path,
            source,
            "ru_ru",
            target_text='{"collapsible_groups.group.demo":"Демо-группа","demo.command":"Запустить /create"}',
            mode="append",
        )
        self.assertEqual(merge.pending_ids, ())
        self.assertEqual(len(merge.existing_values), 2)

    def test_jaopca_config_locale_round_trip_and_target(self) -> None:
        adapter = JaopcaConfigLangJsonAdapter()
        path = "config/jaopca/lang/en_us.json"
        source = '{"material.jaopca.demo":"Demo Material","gui.jaopca.demo":"Demo Screen"}'
        self.assertTrue(adapter.matches(path))
        self.assertFalse(adapter.matches("assets/jaopca/lang/en_us.json"))
        self.assertEqual(adapter.target_path(path, "pt_br"), "config/jaopca/lang/pt_br.json")
        plan = adapter.prepare(path, source)
        self.assertEqual(len(plan.units), 2)
        self.assertEqual(adapter.apply(plan, {unit.id: unit.text for unit in plan.units}), source)

    def test_default_registry_detects_only_the_two_proven_config_roots(self) -> None:
        registry = FormatRegistry.default()
        collapsible = registry.detect_result("config/collapsiblegroups/lang/en_us.json")
        jaopca = registry.detect_result("config/jaopca/lang/en_us.json")
        unknown = registry.detect_result("config/unprovenmod/lang/en_us.json")
        self.assertIsNotNone(collapsible)
        self.assertIsNotNone(jaopca)
        assert collapsible is not None and jaopca is not None
        self.assertEqual(collapsible.capabilities.name, "collapsible-groups-config-lang-json")
        self.assertEqual(jaopca.capabilities.name, "jaopca-config-lang-json")
        self.assertTrue(collapsible.capabilities.supports_existing_target_merge)
        self.assertTrue(jaopca.capabilities.supports_existing_target_merge)
        self.assertIsNone(unknown)


if __name__ == "__main__":
    unittest.main()
