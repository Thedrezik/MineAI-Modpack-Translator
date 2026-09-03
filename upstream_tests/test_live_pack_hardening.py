import unittest

from mineai_formatkit import (
    DataDrivenGuideMeMarkdownAdapter,
    LocaleMergePlanner,
    MinecraftLangJsonAdapter,
    ValidationError,
)


class LivePackLocaleHardeningTests(unittest.TestCase):
    def test_identical_duplicate_keys_share_one_unit_and_all_spans_are_updated(self) -> None:
        source = '{\n  "a": "One",\n  "a": "One",\n  "b": "Two"\n}'
        adapter = MinecraftLangJsonAdapter()
        plan = adapter.prepare("assets/demo/lang/en_us.json", source)

        self.assertEqual([unit.id for unit in plan.units], ["key:a", "key:b"])
        self.assertEqual(len(plan.metadata["duplicate_alias_spans"]["key:a"]), 1)
        self.assertEqual(
            adapter.apply(plan, {unit.id: unit.text for unit in plan.units}),
            source,
        )

        output = adapter.apply(plan, {"key:a": "Один", "key:b": "Два"})
        self.assertEqual(output.count('"Один"'), 2)
        self.assertIn('"Два"', output)
        self.assertEqual(adapter.fingerprint(output), adapter.fingerprint(source))

    def test_conflicting_duplicate_key_still_fails_closed(self) -> None:
        with self.assertRaises(ValidationError):
            MinecraftLangJsonAdapter().prepare(
                "assets/demo/lang/en_us.json",
                '{"a":"One","a":"Two"}',
            )

    def test_ftb_image_formatting_and_private_use_glyphs_are_protected(self) -> None:
        source = (
            '{"quest":"Use &6Amadron&r network",'
            '"image":"Diagram {image:demo:textures/quest/a.png width:120 height:100}",'
            '"disc":"Music Disc §f(\ueff515)"}'
        )
        adapter = MinecraftLangJsonAdapter()
        plan = adapter.prepare("assets/demo/lang/en_us.json", source)
        protected = {
            fragment.value
            for unit in plan.units
            for fragment in unit.protected
        }
        self.assertTrue({"&6", "&r", "§f", "\ueff5"}.issubset(protected))
        self.assertIn(
            "{image:demo:textures/quest/a.png width:120 height:100}",
            protected,
        )

        translated = {
            unit.id: (
                unit.text.replace("Use", "Используйте")
                .replace("network", "сеть")
                .replace("Diagram", "Схема")
                .replace("Music Disc", "Музыкальный диск")
            )
            for unit in plan.units
        }
        output = adapter.apply(plan, translated)
        self.assertIn("&6", output)
        self.assertIn("&r", output)
        self.assertIn("{image:demo:textures/quest/a.png width:120 height:100}", output)
        self.assertIn("\ueff5", output)

        guarded = next(unit for unit in plan.units if unit.protected)
        broken = translated[guarded.id].replace(guarded.protected[0].placeholder, "", 1)
        with self.assertRaises(ValidationError):
            adapter.apply(plan, {guarded.id: broken})

    def test_merge_rejects_lost_ftb_boundaries_images_and_private_glyphs(self) -> None:
        source = (
            '{"styled":"&6Thing&r",'
            '"image":"Diagram {image:demo:textures/a.png width:10}",'
            '"disc":"Music \ueff5 15"}'
        )
        good_target = (
            '{"styled":"&bВещь&r",'
            '"image":"Схема {image:demo:textures/a.png width:10}",'
            '"disc":"Музыка \ueff5 15"}'
        )
        planner = LocaleMergePlanner()
        good = planner.plan(
            "assets/demo/lang/en_us.json",
            source,
            "ru_ru",
            target_text=good_target,
            mode="append",
        )
        self.assertEqual(good.pending_ids, ())
        self.assertEqual(good.invalid_existing_keys, ())

        lost_format = good_target.replace("&bВещь&r", "Вещь&r")
        bad_format = planner.plan(
            "assets/demo/lang/en_us.json",
            source,
            "ru_ru",
            target_text=lost_format,
            mode="append",
        )
        self.assertIn("styled", bad_format.invalid_existing_keys)

        lost_image = good_target.replace(
            " {image:demo:textures/a.png width:10}", ""
        )
        bad_image = planner.plan(
            "assets/demo/lang/en_us.json",
            source,
            "ru_ru",
            target_text=lost_image,
            mode="append",
        )
        self.assertIn("image", bad_image.invalid_existing_keys)

        lost_glyph = good_target.replace("\ueff5", "")
        bad_glyph = planner.plan(
            "assets/demo/lang/en_us.json",
            source,
            "ru_ru",
            target_text=lost_glyph,
            mode="append",
        )
        self.assertIn("disc", bad_glyph.invalid_existing_keys)


class LivePackGuideMeHardeningTests(unittest.TestCase):
    SOURCE = (
        "---\n"
        "title: Data Center\n"
        "---\n"
        "#### *Data Center Multiblock*\n"
        "Optional. Can be replaced with **Black Stained Glass** if desired.\n"
    )

    def test_star_emphasis_delimiters_are_structural_but_inner_text_translates(self) -> None:
        adapter = DataDrivenGuideMeMarkdownAdapter()
        path = "assets/hostilenetworks/guides/hostilenetworks/guide/index.md"
        self.assertTrue(adapter.matches(path))
        self.assertEqual(
            adapter.target_path(path, "ru_ru"),
            "assets/hostilenetworks/guides/hostilenetworks/guide/_ru_ru/index.md",
        )

        plan = adapter.prepare(path, self.SOURCE)
        emphasized = [unit for unit in plan.units if "Black Stained Glass" in unit.text]
        self.assertEqual(len(emphasized), 1)
        self.assertNotIn("**", emphasized[0].text)
        self.assertEqual(
            [fragment.value for fragment in emphasized[0].protected if fragment.value == "**"],
            ["**", "**"],
        )
        italic = [unit for unit in plan.units if "Data Center Multiblock" in unit.text]
        self.assertEqual(len(italic), 1)
        self.assertNotIn("*Data Center", italic[0].text)

        identity = adapter.apply(plan, {unit.id: unit.text for unit in plan.units})
        self.assertEqual(identity, self.SOURCE)

        translations = {
            unit.id: (
                unit.text.replace("Data Center", "Центр данных")
                .replace("Multiblock", "мультиблок")
                .replace("Optional", "Необязательно")
                .replace("Black Stained Glass", "Чёрное окрашенное стекло")
                .replace("if desired", "при желании")
            )
            for unit in plan.units
        }
        output = adapter.apply(plan, translations)
        self.assertIn("*Центр данных мультиблок*", output)
        self.assertIn("**Чёрное окрашенное стекло**", output)

        guarded = emphasized[0]
        broken = translations[guarded.id].replace(guarded.protected[0].placeholder, "", 1)
        with self.assertRaises(ValidationError):
            adapter.apply(plan, {guarded.id: broken})

        with self.assertRaises(ValidationError):
            adapter.validate(
                self.SOURCE,
                self.SOURCE.replace("**Black Stained Glass**", "Black Stained Glass"),
            )


if __name__ == "__main__":
    unittest.main()
