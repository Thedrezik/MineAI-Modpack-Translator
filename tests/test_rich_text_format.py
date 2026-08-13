import unittest

from mineai.formats.rich_text import (
    contains_unsafe_formatting,
    parse_rich_text,
)


class RichTextFormatTests(unittest.TestCase):
    def test_whitespace_between_tags_is_not_duplicated(self):
        source = "<Scene>\n  <Child />\n</Scene>"

        self.assertEqual(parse_rich_text(source).render({}), source)

    def test_real_little_big_redstone_example_round_trips_losslessly(self):
        source = (
            r"\**italics*\*, \*\***bold**\*\*, "
            r"\_\_<Underlined>underline</Underlined>\_\_, and "
            r"\~\~~strikethrough~\~\~"
        )

        template = parse_rich_text(source)

        self.assertEqual(template.render({}), source)
        visible = "".join(part.text for part in template.translatable_parts())
        self.assertIn("italics", visible)
        self.assertIn("bold", visible)
        self.assertIn("underline", visible)
        self.assertIn("strikethrough", visible)
        self.assertNotIn("\\*", visible)
        self.assertNotIn("<Underlined>", visible)

    def test_patchouli_colors_are_reassembled_from_original_parts(self):
        source = (
            "$(#BB00BB)Epic$() items use "
            "$(#ED7014)Mythic$() materials."
        )
        template = parse_rich_text(source)
        translations = {
            part.index: (
                part.text.replace("Epic", "Эпические")
                .replace("items use", "предметы используют")
                .replace("Mythic", "мифические")
                .replace("materials", "материалы")
            )
            for part in template.translatable_parts()
        }

        result = template.render(translations)

        self.assertIn("$(#BB00BB)", result)
        self.assertIn("$(#ED7014)", result)
        self.assertEqual(result.count("$()"), 2)
        self.assertNotIn("[#", result)

    def test_json_book_color_reset_keeps_sentence_separator(self):
        template = parse_rich_text("Colored §2word§r. Next sentence.")
        payload, anchors = template.translation_payload()
        tokens = tuple(anchor.token for anchor in anchors)
        candidate = (
            f"Цветное {tokens[0]}слово{tokens[1]}Следующее предложение."
        )

        self.assertEqual(
            template.render_translation(candidate),
            "Цветное §2слово§r. Следующее предложение.",
        )

    def test_guideme_tag_and_markdown_link_are_never_exposed_as_text(self):
        source = (
            '<ItemLink id="ae2:controller" /> Read '
            "[ME Networks](me-network-connections.md)."
        )

        template = parse_rich_text(source)
        visible = "".join(part.text for part in template.translatable_parts())

        self.assertEqual(template.render({}), source)
        self.assertNotIn("ItemLink", visible)
        self.assertNotIn("me-network-connections.md", visible)
        self.assertIn("ME Networks", visible)

    def test_complex_link_destination_is_an_immutable_fragment(self):
        source = "Read [details](guide_(advanced).md#part)."
        template = parse_rich_text(source)
        visible = "".join(part.text for part in template.translatable_parts())

        self.assertEqual(template.render({}), source)
        self.assertNotIn("guide_", visible)
        self.assertNotIn("advanced", visible)
        self.assertNotIn("md#part", visible)
        self.assertIn("details", visible)
        self.assertIn(
            "](guide_(advanced).md#part)",
            [part.text for part in template.parts if not part.translatable],
        )

    def test_quoted_tag_and_hex_colors_are_never_exposed(self):
        source = (
            '<Panel title="A > B">Text</Panel> '
            "&#12ABEFHex §x§1§2§A§B§E§FLegacy"
        )
        template = parse_rich_text(source)
        visible = "".join(part.text for part in template.translatable_parts())

        self.assertEqual(template.render({}), source)
        self.assertNotIn("Panel", visible)
        self.assertNotIn("12ABEF", visible)
        self.assertNotIn("§x", visible)
        self.assertIn("Text", visible)

    def test_gui_terms_are_immutable_regardless_of_case(self):
        template = parse_rich_text("Open the GUI, then close the gui.")

        immutable = [
            part.text for part in template.parts if not part.translatable
        ]

        self.assertIn("GUI", immutable)
        self.assertIn("gui", immutable)

    def test_embedded_json_component_exposes_only_display_text(self):
        for source in (
            '{"text":"Purple Chalk","color":"#9D7AD0"}',
            r'{\"text\":\"Basic Fluid Tank\",\"color\":\"#5EFCB6\"}',
        ):
            with self.subTest(source=source):
                template = parse_rich_text(source)
                visible = "".join(
                    part.text for part in template.translatable_parts()
                )

                self.assertEqual(template.render({}), source)
                self.assertNotIn('"text"', visible)
                self.assertNotIn("color", visible)
                self.assertNotIn("#", visible)
                self.assertTrue(
                    "Purple Chalk" in visible or "Basic Fluid Tank" in visible
                )

    def test_translated_node_cannot_introduce_formatting(self):
        self.assertTrue(contains_unsafe_formatting("**сломано**"))
        self.assertTrue(contains_unsafe_formatting("$(#ED7014)цвет"))
        self.assertTrue(contains_unsafe_formatting('<ItemLink id="x" />'))
        self.assertFalse(contains_unsafe_formatting("Обычный русский текст."))
        self.assertFalse(contains_unsafe_formatting("После версии 1.20.x"))
        self.assertFalse(contains_unsafe_formatting("GPS(Область)"))

    def test_patchouli_legacy_codes_and_script_identifiers_are_immutable(self):
        source = (
            "$(li)$(thing)$player=<name>/$: call "
            "setTextField(<text>) in minecraft:overworld and read position.x."
        )

        template = parse_rich_text(source)
        payload, _anchors = template.translation_payload()

        self.assertNotIn("/$", payload)
        self.assertNotIn("$player=", payload)
        self.assertNotIn("setTextField", payload)
        self.assertNotIn("minecraft:overworld", payload)
        self.assertNotIn("<name>", payload)
        self.assertNotIn(".x", payload)
        self.assertIn("call", payload)
        self.assertIn("read position", payload)
        self.assertEqual(template.render_translation(payload), source)

    def test_patchouli_tooltip_text_is_translatable_but_wrapper_is_immutable(self):
        source = (
            "$(ttcolor)$(t:Connect on the right to whitelist, on the left "
            "to blacklist)connect/$ a filter."
        )

        template = parse_rich_text(source)
        payload, _anchors = template.translation_payload()

        self.assertIn(
            "Connect on the right to whitelist, on the left to blacklist",
            payload,
        )
        self.assertNotIn("$(t:", payload)
        self.assertEqual(template.render_translation(payload), source)


if __name__ == "__main__":
    unittest.main()
