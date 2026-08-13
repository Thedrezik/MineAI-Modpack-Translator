"""Regression tests for plain-text Minecraft book adapters."""

import unittest

from formatkit import FormatRegistry


class PlainTextBookAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = FormatRegistry.default()

    def test_txt_book_is_supported_and_keeps_plain_locale_path(self) -> None:
        plan = self.registry.plan(
            "assets/alexscaves/books/en_us/root.txt",
            "Hidden caves exist.\n",
            "ru_ru",
        )

        self.assertEqual(
            plan.target_path,
            "assets/alexscaves/books/ru_ru/root.txt",
        )
        self.assertEqual(plan.apply({}).text, "Hidden caves exist.\n")

    def test_braced_book_link_exposes_label_but_hides_target(self) -> None:
        source = "• {Magnetic Caves|magnetic_caves/chapter.json}\n"
        plan = self.registry.plan(
            "assets/alexscaves/books/en_us/root.txt",
            source,
            "ru_ru",
        )
        unit = plan.units[0]

        self.assertIn("Magnetic Caves", unit.payload)
        self.assertNotIn("magnetic_caves", unit.payload)
        result = plan.apply(
            {unit.id: unit.payload.replace("Magnetic Caves", "Магнитные пещеры")}
        )
        self.assertEqual(
            result.text,
            "• {Магнитные пещеры|magnetic_caves/chapter.json}\n",
        )

    def test_shared_game_tokenizer_protects_codes_and_placeholders(self) -> None:
        source = "§lEnergy§r for {player}: %1$s $(#ED7014)Mythic$().\n"
        plan = self.registry.plan(
            "assets/demo/guide/en_us/page.md",
            source,
            "ru_ru",
        )
        payload = plan.units[0].payload

        for protected in ("§l", "§r", "{player}", "%1$s", "$(#ED7014)", "$()"):
            self.assertNotIn(protected, payload)
        self.assertEqual(plan.apply({}).text, source)

    def test_markdown_color_reset_owns_its_sentence_separator(self) -> None:
        source = "Colored §2word§r. Next sentence.\n"
        plan = self.registry.plan(
            "assets/demo/guide/en_us/page.md",
            source,
            "ru_ru",
        )
        unit = plan.units[0]
        tokens = unit.anchor_tokens

        result = plan.apply({
            unit.id: (
                f"Цветное {tokens[0]}слово{tokens[1]}Следующее предложение."
            )
        })

        self.assertEqual(
            result.text,
            "Цветное §2слово§r. Следующее предложение.\n",
        )


if __name__ == "__main__":
    unittest.main()
