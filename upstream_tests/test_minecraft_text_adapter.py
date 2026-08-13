import json
import unittest

from mineai_formatkit.core import ValidationError
from mineai_formatkit.minecraft_text import MinecraftTextComponentAdapter


class MinecraftTextComponentAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = MinecraftTextComponentAdapter()

    def test_matches_only_known_data_families(self):
        self.assertTrue(self.adapter.matches("data/demo/advancement/root.json"))
        self.assertTrue(self.adapter.matches("data/demo/loot_table/test.json"))
        self.assertFalse(self.adapter.matches("data/demo/worldgen/template_pool/test.json"))

    def test_advancement_translate_fallback_is_extracted(self):
        src = '{"display":{"title":{"translate":"demo.title","fallback":"Hello World"}}}'
        plan = self.adapter.prepare("data/demo/advancement/root.json", src)
        self.assertEqual([u.text for u in plan.units], ["Hello World"])
        out = self.adapter.apply(plan, {plan.units[0].id: "Привет, мир"})
        self.assertEqual(json.loads(out)["display"]["title"]["fallback"], "Привет, мир")

    def test_resource_location_fallback_is_not_translated(self):
        src = '{"fallback":"minecraft:empty"}'
        plan = self.adapter.prepare("data/demo/advancement/root.json", src)
        self.assertEqual(plan.units, ())

    def test_set_name_and_lore_plain_strings_are_extracted(self):
        src = '{"functions":[{"function":"minecraft:set_name","name":"Horn of Hatred"},{"function":"minecraft:set_lore","lore":["Bad omen %s"]}]}'
        plan = self.adapter.prepare("data/demo/loot_table/a.json", src)
        self.assertEqual([u.text for u in plan.units], ["Horn of Hatred", "Bad omen [#0#]"])
        out = self.adapter.apply(plan, {plan.units[0].id:"Рог ненависти", plan.units[1].id:"Дурное знамение [#0#]"})
        data=json.loads(out)
        self.assertEqual(data["functions"][0]["name"], "Рог ненависти")
        self.assertEqual(data["functions"][1]["lore"][0], "Дурное знамение %s")

    def test_nested_custom_name_string_is_preserved_as_nested_json(self):
        src = '{"function":"minecraft:set_components","components":{"minecraft:custom_name":"\\\"Thanks Cake!!!\\\""}}'
        plan = self.adapter.prepare("data/demo/loot_table/a.json", src)
        self.assertEqual(plan.units[0].text, "Thanks Cake!!!")
        out = self.adapter.apply(plan, {plan.units[0].id:"Спасибо за торт!!!"})
        nested=json.loads(out)["components"]["minecraft:custom_name"]
        self.assertEqual(json.loads(nested), "Спасибо за торт!!!")

    def test_natural_percent_phrase_is_not_a_printf_placeholder(self):
        src = '{"function":"minecraft:set_lore","lore":["75% chance"]}'
        plan = self.adapter.prepare("data/demo/loot_table/a.json", src)
        self.assertEqual(plan.units[0].text, "75% chance")
        self.assertFalse(any("% c" in fragment.value for fragment in plan.units[0].protected))

    def test_placeholder_loss_is_rejected(self):
        src = '{"function":"minecraft:set_lore","lore":["Count %s"]}'
        plan = self.adapter.prepare("data/demo/loot_table/a.json", src)
        with self.assertRaises(ValidationError):
            self.adapter.apply(plan, {plan.units[0].id:"Количество"})

class MinecraftTextIdentityTests(unittest.TestCase):
    def test_identity_preserves_exact_escape_spelling(self):
        adapter = MinecraftTextComponentAdapter()
        src = '{"display":{"title":{"translate":"demo.title","fallback":"Hello\\u0020World"}}}'
        plan = adapter.prepare("data/demo/advancement/root.json", src)
        out = adapter.apply(plan, {plan.units[0].id: plan.units[0].text})
        self.assertEqual(out, src)
