from __future__ import annotations

import unittest

from mineai_formatkit import (
    CollapsibleGroupsLangJsonAdapter,
    CrashAssistantLocalizationAdapter,
    DataDrivenGuideMeMarkdownAdapter,
    FormatRegistry,
    ImmersiveEngineeringManualAdapter,
    MinecraftAdvancementTextAdapter,
    ValidationError,
)


class CorpusDrivenAdaptersTest(unittest.TestCase):
    def test_advancement_direct_display_strings_are_translatable(self) -> None:
        adapter = MinecraftAdvancementTextAdapter()
        path = "data/demo/advancement/root.json"
        source = (
            '{"display":{"icon":{"id":"minecraft:stone"},'
            '"title":"Cobblestone Addiction: I",'
            '"description":"§fCompress 9x §2Cobblestone§f"}}'
        )
        plan = adapter.prepare(path, source)
        self.assertEqual(
            {unit.context for unit in plan.units},
            {"/display/title", "/display/description"},
        )
        identity = {unit.id: unit.text for unit in plan.units}
        self.assertEqual(adapter.apply(plan, identity), source)
        translated = {
            unit.id: unit.text.replace("Cobblestone Addiction", "Зависимость от булыжника")
            .replace("Compress", "Сожмите")
            .replace("Cobblestone", "булыжник")
            for unit in plan.units
        }
        adapter.apply(plan, translated)

    def test_collapsible_groups_uses_its_runtime_locale_tree(self) -> None:
        adapter = CollapsibleGroupsLangJsonAdapter()
        path = "assets/collapsible_groups/group_lang/en_us.json"
        source = '{"collapsible_groups.group.demo":"Demo Group"}'
        self.assertTrue(adapter.matches(path))
        self.assertEqual(
            adapter.target_path(path, "ru_ru"),
            "assets/collapsible_groups/group_lang/ru_ru.json",
        )
        plan = adapter.prepare(path, source)
        self.assertEqual(len(plan.units), 1)
        self.assertEqual(adapter.apply(plan, {plan.units[0].id: plan.units[0].text}), source)

    def test_crash_assistant_runtime_syntax_is_protected(self) -> None:
        adapter = CrashAssistantLocalizationAdapter()
        path = "crash_assistant_localization/en_us.json"
        source = (
            '{"msg":"Open $LOG_FILENAME$ at <code>https://example.test/log</code> '
            'and keep %s"}'
        )
        plan = adapter.prepare(path, source)
        self.assertEqual(len(plan.units), 1)
        unit = plan.units[0]
        protected_values = {fragment.value for fragment in unit.protected}
        self.assertIn("$LOG_FILENAME$", protected_values)
        self.assertIn("<code>", protected_values)
        self.assertIn("https://example.test/log", protected_values)
        self.assertIn("</code>", protected_values)
        self.assertIn("%s", protected_values)
        self.assertEqual(adapter.apply(plan, {unit.id: unit.text}), source)
        first_placeholder = unit.protected[0].placeholder
        with self.assertRaises(ValidationError):
            adapter.apply(plan, {unit.id: unit.text.replace(first_placeholder, "", 1)})

    def test_data_driven_guideme_gets_locale_prefixed_target(self) -> None:
        adapter = DataDrivenGuideMeMarkdownAdapter()
        path = "assets/energymeter/guides/energymeter/guide/interface.md"
        source = "---\nnavigation:\n  title: Interface\n---\n\n# Energy Meter Interface\n"
        self.assertTrue(adapter.matches(path))
        self.assertFalse(
            adapter.matches(
                "assets/energymeter/guides/energymeter/guide/_ru_ru/interface.md"
            )
        )
        self.assertEqual(
            adapter.target_path(path, "ru_ru"),
            "assets/energymeter/guides/energymeter/guide/_ru_ru/interface.md",
        )
        plan = adapter.prepare(path, source)
        self.assertGreaterEqual(len(plan.units), 2)
        self.assertEqual(adapter.apply(plan, {unit.id: unit.text for unit in plan.units}), source)

    def test_ie_manual_keeps_directives_and_translates_only_safe_fields(self) -> None:
        adapter = ImmersiveEngineeringManualAdapter()
        path = "assets/immersiveengineering/manual/en_us/demo.txt"
        source = (
            "Demo Manual\nSubtitle\n"
            "<&start>See <link;target;§2Workbench§r;anchor>.<br>\n"
            "<config;b;feature.enabled;enabled text;disabled text>\n"
            "<keybind;key.demo>\n"
        )
        plan = adapter.prepare(path, source)
        self.assertEqual(adapter.target_path(path, "ru_ru"), "assets/immersiveengineering/manual/ru_ru/demo.txt")
        kinds = [unit.kind for unit in plan.units]
        self.assertIn("ie-link-label", kinds)
        self.assertEqual(kinds.count("ie-config-label"), 2)
        identity = {unit.id: unit.text for unit in plan.units}
        self.assertEqual(adapter.apply(plan, identity), source)
        translated = {unit.id: unit.text + " RU" for unit in plan.units}
        output = adapter.apply(plan, translated)
        self.assertIn("<link;target;", output)
        self.assertIn(";anchor>", output)
        self.assertIn("<keybind;key.demo>", output)

        link_unit = next(unit for unit in plan.units if unit.kind == "ie-link-label")
        with self.assertRaises(ValidationError):
            adapter.apply(plan, {link_unit.id: link_unit.text + ";broken"})

    def test_default_registry_exposes_new_adapters(self) -> None:
        names = {adapter.name for adapter in FormatRegistry.default().adapters}
        self.assertTrue(
            {
                "minecraft-advancement-text",
                "collapsible-groups-lang-json",
                "crash-assistant-localization",
                "guideme-data-driven-markdown",
                "immersive-engineering-manual",
            }.issubset(names)
        )


if __name__ == "__main__":
    unittest.main()
