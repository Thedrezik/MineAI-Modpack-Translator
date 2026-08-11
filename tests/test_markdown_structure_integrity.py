import unittest

from mineai.processors.selection import (
    collect_book_markdown_selection,
    split_markdown_block_structure,
)
from mineai.text_processing import mask_protected_fragments, unmask_translation


class MarkdownDocumentStructureTests(unittest.TestCase):
    def test_smart_glue_does_not_collapse_markdown_blocks(self) -> None:
        source = "\n".join(
            [
                "---",
                "navigation:",
                "  title: Structure Test",
                "---",
                "![Logo](assets/logo.png)",
                "",
                "## Heading",
                "Paragraph after heading.",
                "",
                "| Bytes | Types |",
                "| --- | --- |",
                "| One | Two |",
                "",
                "1. First item",
                "2. Second item",
            ]
        )

        selection = collect_book_markdown_selection(
            source,
            "",
            "force",
            smart_glue=True,
        )

        self.assertEqual(selection.source_text, source)
        self.assertEqual(len(selection.lines_out), len(source.split("\n")))
        self.assertEqual(selection.lines_out[3], "---")
        self.assertEqual(selection.lines_out[4], "![Logo](assets/logo.png)")
        self.assertEqual(selection.lines_out[6], "## Heading")
        self.assertEqual(selection.lines_out[9], "| Bytes | Types |")
        self.assertEqual(selection.lines_out[10], "| --- | --- |")
        self.assertEqual(selection.lines_out[13], "1. First item")
        self.assertEqual(selection.lines_out[14], "2. Second item")

    def test_block_structure_is_split_byte_for_byte_from_payload(self) -> None:
        cases = {
            "*   Bullet item": ("*   ", "Bullet item", ""),
            "  *   Nested bullet": ("  *   ", "Nested bullet", ""),
            "1.  Ordered item": ("1.  ", "Ordered item", ""),
            "   2)    Ordered item": ("   2)    ", "Ordered item", ""),
            "##   Heading": ("##   ", "Heading", ""),
            "##   Heading   ##": ("##   ", "Heading", "   ##"),
            ">   Quoted text": (">   ", "Quoted text", ""),
            "> *   Nested quote item": ("> *   ", "Nested quote item", ""),
            "  Continuation text": ("  ", "Continuation text", ""),
            "- [ ]   Task item": ("- [ ]   ", "Task item", ""),
            "- [x] Done item": ("- [x] ", "Done item", ""),
            "Plain paragraph": ("", "Plain paragraph", ""),
            "Text with hard break  ": ("", "Text with hard break", "  "),
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(split_markdown_block_structure(source), expected)

    def test_selection_sends_only_bullet_payload_to_translation_service(self) -> None:
        source = '*   ME Extended Interface is a <ItemLink id="ae2:interface" /> with a larger configuration inventory.'
        selection = collect_book_markdown_selection(
            source,
            "",
            "force",
            smart_glue=True,
        )

        key = "0"
        self.assertEqual(
            selection.pending[key],
            'ME Extended Interface is a <ItemLink id="ae2:interface" /> with a larger configuration inventory.',
        )
        self.assertEqual(selection.title_meta[key], ("*   ", ""))

        masked, mapping = mask_protected_fragments(selection.pending[key])
        self.assertNotIn("*   ", masked)
        self.assertNotIn('id="ae2:interface"', masked)
        self.assertIn('<ItemLink id="ae2:interface" />', mapping.values())

        translated_payload = masked.replace(
            "ME Extended Interface is a",
            "МЕ Расширенный интерфейс — это",
        ).replace(
            "with a larger configuration inventory.",
            "с более обширным списком конфигураций.",
        )
        restored_payload = unmask_translation(translated_payload, mapping)
        prefix, suffix = selection.title_meta[key]
        final = prefix + restored_payload + suffix

        self.assertEqual(
            final,
            '*   МЕ Расширенный интерфейс — это <ItemLink id="ae2:interface" /> с более обширным списком конфигураций.',
        )

    def test_selection_preserves_indent_and_hard_break_outside_payload(self) -> None:
        source = "  Continuation text with hard break  "
        selection = collect_book_markdown_selection(
            source,
            "",
            "force",
            smart_glue=False,
        )

        self.assertEqual(selection.pending, {"0": "Continuation text with hard break"})
        self.assertEqual(selection.title_meta["0"], ("  ", "  "))

    def test_fenced_code_block_is_not_selected_for_translation(self) -> None:
        source = "\n".join(
            [
                "```json",
                '{"text": "Do not translate this example"}',
                "```",
                "Translate this paragraph.",
            ]
        )
        selection = collect_book_markdown_selection(
            source,
            "",
            "force",
            smart_glue=False,
        )

        self.assertNotIn("0", selection.pending)
        self.assertNotIn("1", selection.pending)
        self.assertNotIn("2", selection.pending)
        self.assertEqual(selection.pending["3"], "Translate this paragraph.")


class MarkdownInlineShieldTests(unittest.TestCase):
    def test_block_markers_are_not_placeholder_fragments(self) -> None:
        masked, mapping = mask_protected_fragments("## Heading")
        self.assertNotIn("## ", mapping.values())
        self.assertEqual(masked, "## Heading")

        _masked, mapping = mask_protected_fragments("*   Bullet")
        self.assertNotIn("*   ", mapping.values())

    def test_inline_itemlink_round_trips_exactly(self) -> None:
        source = 'ME Interface is a <ItemLink id="ae2:interface" /> component.'
        masked, mapping = mask_protected_fragments(source)

        self.assertNotIn('id="ae2:interface"', masked)
        self.assertIn('<ItemLink id="ae2:interface" />', mapping.values())
        translated = masked.replace("ME Interface is a", "Интерфейс ME — это").replace(
            "component.",
            "компонент.",
        )
        self.assertEqual(
            unmask_translation(translated, mapping),
            'Интерфейс ME — это <ItemLink id="ae2:interface" /> компонент.',
        )

    def test_table_delimiters_round_trip_exactly(self) -> None:
        source = "| Bytes | Types |"
        masked, mapping = mask_protected_fragments(source)

        self.assertEqual(sum(value == "|" for value in mapping.values()), 3)
        translated = masked.replace("Bytes", "Байты").replace("Types", "Типы")
        restored = unmask_translation(translated, mapping)

        self.assertEqual(restored, "| Байты | Типы |")
        self.assertEqual(restored.count("|"), source.count("|"))

    def test_markdown_link_brackets_and_destination_round_trip(self) -> None:
        source = "[Facades](../items/facades.md) will be hidden."
        masked, mapping = mask_protected_fragments(source)

        self.assertNotIn("../items/facades.md", masked)
        self.assertIn("[", mapping.values())
        self.assertIn("](../items/facades.md)", mapping.values())

        translated = masked.replace("Facades", "Фасады").replace(
            "will be hidden.",
            "будут скрыты.",
        )
        self.assertEqual(
            unmask_translation(translated, mapping),
            "[Фасады](../items/facades.md) будут скрыты.",
        )

    def test_image_markup_still_round_trips_without_translation(self) -> None:
        source = "![image](foo.png)"
        masked, mapping = mask_protected_fragments(source)

        self.assertNotIn("foo.png", masked)
        self.assertEqual(unmask_translation(masked, mapping), source)


if __name__ == "__main__":
    unittest.main()
