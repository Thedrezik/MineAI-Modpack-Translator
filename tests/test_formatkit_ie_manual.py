import unittest

from formatkit import FormatRegistry
from mineai.language_validation import translation_needs_repair


class ImmersiveEngineeringAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = FormatRegistry.default()

    def test_link_with_apostrophe_keeps_id_and_translates_label(self) -> None:
        source = (
            "Blueprints\n"
            "Engineering!\n"
            "All blueprints can be used in an "
            "<link;engineers_workbench;Engineer's Workbench> to craft items.<np>\n"
        )
        plan = self.registry.plan(
            "assets/immersiveengineering/manual/en_us/blueprints.txt",
            source,
            "ru_ru",
        )
        body = plan.units[2]

        self.assertNotIn("engineers_workbench", body.payload)
        self.assertNotIn("<link", body.payload)
        self.assertIn("Engineer's Workbench", body.payload)
        tokens = [anchor.token for anchor in body.anchors]
        translated = (
            f"Все чертежи можно использовать в {tokens[0]}"
            f"Инженерном верстаке{tokens[1]} для создания предметов.{tokens[2]}"
        )
        result = plan.apply({body.id: translated})

        self.assertIn(
            "<link;engineers_workbench;Инженерном верстаке>",
            result.text,
        )
        self.assertIn("<np>", result.text)

    def test_recipe_and_format_codes_round_trip_losslessly(self) -> None:
        source = (
            "<&frame_recipe>§lAccumulators§r store energy.<br>\r\n"
        )
        plan = self.registry.plan(
            "assets/immersiveengineering/manual/en_us/accumulators.txt",
            source,
            "ru_ru",
        )

        self.assertEqual(plan.apply({}).text, source)
        payload = plan.units[0].payload
        self.assertNotIn("frame_recipe", payload)
        self.assertNotIn("§", payload)
        self.assertNotIn("<br>", payload)

    def test_color_reset_keeps_following_sentence_separator(self) -> None:
        source = "Colored §2word§r. Next sentence.\n"
        plan = self.registry.plan(
            "assets/immersiveengineering/manual/en_us/railgun.txt",
            source,
            "ru_ru",
        )
        unit = plan.units[0]
        tokens = unit.anchor_tokens
        candidate = (
            f"Цветное {tokens[0]}слово{tokens[1]}Следующее предложение."
        )

        result = plan.apply({unit.id: candidate})

        self.assertEqual(
            result.text,
            "Цветное §2слово§r. Следующее предложение.\n",
        )

    def test_ie_target_path_uses_plain_locale(self) -> None:
        plan = self.registry.plan(
            "assets/immersiveengineering/manual/en_us/page.txt",
            "Page\nSubtitle\nBody text.\n",
            "ru_ru",
        )
        self.assertEqual(
            plan.target_path,
            "assets/immersiveengineering/manual/ru_ru/page.txt",
        )

    def test_mixed_translation_with_unchanged_link_label_requires_repair(self) -> None:
        source = (
            "This mod explains complex "
            "<link;large_constructions;multiblock machines> in detail.\n"
        )
        plan = self.registry.plan(
            "assets/engineered_schematics/manual/en_us/es.txt",
            source,
            "ru_ru",
        )
        unit = plan.units[0]
        candidate = unit.payload.replace(
            "This mod explains complex ",
            "Этот мод подробно описывает сложные ",
        ).replace(" in detail.", ".")

        target_lang = {
            "file": "ru_ru",
            "api": "ru",
            "regex": r"[А-Яа-яЁё]",
        }
        self.assertIsNotNone(plan.candidate_error(
            unit.id,
            candidate,
            lambda original, translated: (
                "incomplete visible segment"
                if translation_needs_repair(original, translated, target_lang)
                else None
            ),
        ))


if __name__ == "__main__":
    unittest.main()
