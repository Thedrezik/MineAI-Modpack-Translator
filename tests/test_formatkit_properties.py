import unittest

from formatkit import FormatRegistry


class FormatKitPropertiesTests(unittest.TestCase):
    def test_lang_values_are_units_while_keys_and_layout_stay_lossless(self) -> None:
        source = (
            "# section\r\n"
            "intro=Introduction\r\n"
            "upgrades.subtext=Tools can be enhanced with upgrades.\r\n"
            "empty=\r\n"
        )
        plan = FormatRegistry.default().plan(
            "assets/tconstruct/book/puny_smelting/en_us/language.lang",
            source,
            "ru_ru",
        )

        self.assertEqual(plan.adapter_id, "properties-v1")
        self.assertEqual(
            plan.target_path,
            "assets/tconstruct/book/puny_smelting/ru_ru/language.lang",
        )
        self.assertEqual(
            [unit.payload for unit in plan.units],
            ["Introduction", "Tools can be enhanced with upgrades."],
        )
        output = plan.apply(
            {
                plan.units[0].id: "Введение",
                plan.units[1].id: "Инструменты можно улучшать.",
            }
        ).text
        self.assertEqual(
            output,
            (
                "# section\r\n"
                "intro=Введение\r\n"
                "upgrades.subtext=Инструменты можно улучшать.\r\n"
                "empty=\r\n"
            ),
        )


if __name__ == "__main__":
    unittest.main()
