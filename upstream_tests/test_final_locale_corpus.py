from __future__ import annotations

import json
import unittest

from mineai_formatkit import LocaleMergePlanner, MinecraftLangJsonAdapter, ValidationError


class FinalLocaleCorpusCompactTests(unittest.TestCase):
    def test_unicode_slash_boundary(self) -> None:
        source = json.dumps({"x": "стрелками/Tab 文档/Wiki /create §a/track on/off"}, ensure_ascii=False)
        unit = MinecraftLangJsonAdapter().prepare("assets/demo/lang/en_us.json", source).units[0]
        protected = {fragment.value for fragment in unit.protected}
        self.assertTrue({"/create", "/track"}.issubset(protected))
        self.assertNotIn("/Tab", protected)
        self.assertNotIn("/Wiki", protected)

    def test_tempad_structured_component_and_semantic_merge(self) -> None:
        source = '{"x":["",{"text":"Saved location: ","color":"#ff6f00"},{"index":0,"color":"#91450d"}]}'
        target = '{"x": ["", {"color":"#ff6f00", "text":"Konum: "}, {"color":"#91450d", "index":0}]}'
        adapter = MinecraftLangJsonAdapter()
        plan = adapter.prepare("assets/tempad_static/lang/en_us.json", source)
        units = [u for u in plan.units if u.kind == "minecraft-structured-locale-component"]
        self.assertEqual(len(units), 1)
        self.assertEqual(adapter.apply(plan, {u.id: u.text for u in plan.units}), source)
        merge = LocaleMergePlanner(adapter).plan(
            "assets/tempad_static/lang/en_us.json", source, "tr_tr", target_text=target, mode="append"
        )
        self.assertEqual(merge.invalid_existing_keys, ())
        self.assertEqual(merge.pending_ids, ())
        self.assertEqual(json.loads(LocaleMergePlanner(adapter).build(merge, {}))["x"][1]["text"], "Konum: ")
        with self.assertRaises(ValidationError):
            adapter.validate(source, source.replace("#91450d", "#000000"))

    def test_serialized_component_is_span_safe(self) -> None:
        inner = (
            '[{"text":"Current Version: "},{"text":"%s","color":"dark_red"},'
            '{"text":"Click for Download","color":"green",'
            '"clickEvent":{"action":"open_url","value":"%s"}}]'
        )
        source = json.dumps({"update": inner}, ensure_ascii=False)
        adapter = MinecraftLangJsonAdapter()
        plan = adapter.prepare("assets/actuallyadditions/lang/en_us.json", source)
        units = [u for u in plan.units if u.kind == "minecraft-serialized-locale-component"]
        self.assertEqual([u.text for u in units], ["Current Version: ", "Click for Download"])
        self.assertTrue(all(source[u.start:u.end].startswith('\\"') for u in units))
        output = adapter.apply(plan, {u.id: "RU " + u.text for u in units})
        decoded = json.loads(json.loads(output)["update"])
        self.assertEqual(decoded[1]["text"], "%s")
        self.assertEqual(decoded[2]["clickEvent"], {"action": "open_url", "value": "%s"})
        with self.assertRaises(ValidationError):
            adapter.validate(source, source.replace('\\"open_url\\"', '\\"run_command\\"'))

    def test_serialized_merge_requires_canonical_shape(self) -> None:
        en = '[{"text":"There is an Update for "},{"text":"Mod ","color":"dark_green"}]'
        ru = '[ {"text":"Есть обновление "}, {"color":"dark_green","text":"Мода "} ]'
        source = json.dumps({"x": en}, ensure_ascii=False)
        target = json.dumps({"x": ru}, ensure_ascii=False)
        planner = LocaleMergePlanner(MinecraftLangJsonAdapter())
        good = planner.plan("assets/demo/lang/en_us.json", source, "ru_ru", target_text=target, mode="append")
        self.assertEqual(good.invalid_existing_keys, ())
        self.assertEqual(good.pending_ids, ())
        bad = planner.plan(
            "assets/demo/lang/en_us.json", source, "ru_ru",
            target_text=json.dumps({"x": ru.replace("dark_green", "dark_red")}, ensure_ascii=False),
            mode="append",
        )
        self.assertEqual(bad.invalid_existing_keys, ("x",))

    def test_gag_and_malformed_target_contracts_remain_safe(self) -> None:
        gag = '{"g":[{"extra":[{"strikethrough":true,"text":"bottle"}," bundle"],"text":"Save time "}],"u":{"text":"No","color":"red"}}'
        plan = MinecraftLangJsonAdapter().prepare("assets/gag/lang/en_us.json", gag)
        self.assertEqual(len([u for u in plan.units if u.context == "g"]), 3)
        self.assertEqual(plan.metadata["unsupported_non_string_keys"], ("u",))

        planner = LocaleMergePlanner(MinecraftLangJsonAdapter())
        merge = planner.plan(
            "assets/ntgl/lang/en_us.json", '{"one":"One"}', "ru_ru",
            target_text='{"one":"Один" // comment}', mode="append",
        )
        self.assertEqual(merge.pending_ids, ("key:one",))
        self.assertEqual(merge.existing_values, {})
        self.assertIsNotNone(merge.target_parse_error)


if __name__ == "__main__":
    unittest.main()
