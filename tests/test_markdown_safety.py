import unittest

import tempfile
from pathlib import Path
from types import SimpleNamespace
import zipfile

from mineai import text_processing
from mineai.formats.markdown import MarkdownSkeleton
from mineai.processors import selection
from mineai.processors.jar import JarProcessor


class _Config:
    @staticmethod
    def getboolean(_section: str, _key: str) -> bool:
        return False


class _BreakingService:
    config = _Config()

    @staticmethod
    def translate_dict(strings, _target_lang, _callbacks, **_kwargs):
        result = {}
        for key, value in strings.items():
            if "[devices](devices.md)" in value:
                result[key] = value.replace("[devices]", "устройства]")
            else:
                result[key] = "Перевод: " + value
        return result


class _Writer:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def write(self, path: str, data: bytes) -> None:
        self.files[path] = data


class _RoutingService:
    config = _Config()

    def __init__(self) -> None:
        self.calls = []

    def translate_dict(self, strings, _target_lang, _callbacks, **kwargs):
        self.calls.append(("plain", dict(strings), kwargs))
        return {key: "Обычный перевод" for key in strings}

    def translate_formatted_dict(
        self,
        strings,
        _target_lang,
        _callbacks,
        **kwargs,
    ):
        self.calls.append(("formatted", dict(strings), kwargs))
        return {
            key: value.replace("Epic", "Эпический")
            for key, value in strings.items()
        }


class _ParagraphService:
    config = _Config()

    def __init__(self) -> None:
        self.received = {}

    def translate_dict(self, strings, _target_lang, _callbacks, **_kwargs):
        self.received = dict(strings)
        return {
            key: value.replace(
                "This long sentence starts on the first line",
                "Это цельный перевод длинного предложения",
            ).replace(
                "and finishes on the second physical line.",
                "на двух физических строках.",
            )
            for key, value in strings.items()
        }

    def translate_formatted_dict(
        self,
        strings,
        _target_lang,
        _callbacks,
        **_kwargs,
    ):
        self.received = dict(strings)
        return {
            key: "Это цельный перевод длинного предложения на двух физических строках."
            for key in strings
        }


class MarkdownDocumentSafetyTests(unittest.TestCase):
    def test_horizontal_rule_after_front_matter_does_not_open_yaml(self) -> None:
        source = "\n".join(
            [
                "---",
                "title: Page title",
                "---",
                "",
                "First paragraph.",
                "",
                "---",
                "",
                "Second paragraph after the horizontal rule.",
            ]
        )

        result = selection.collect_book_markdown_selection(
            source,
            "",
            "force",
            smart_glue=False,
        )

        self.assertEqual(
            result.pending,
            {
                "1": "Page title",
                "4": "First paragraph.",
                "8": "Second paragraph after the horizontal rule.",
            },
        )

    def test_plain_wrapped_paragraph_becomes_one_translation_unit(self) -> None:
        source = (
            "This sentence is physically wrapped\r\n"
            "and continues on the second line.\r\n"
            "\r\n"
            "# Separate heading\r\n"
        )

        result = selection.collect_book_markdown_selection(
            source,
            "",
            "force",
            smart_glue=False,
        )

        self.assertEqual(
            result.pending,
            {
                "paragraph:0-1": (
                    "This sentence is physically wrapped and continues "
                    "on the second line."
                ),
                "3": "Separate heading",
            },
        )
        self.assertEqual(result.unit_lines["paragraph:0-1"], (0, 1))
        self.assertEqual(result.total_translatable, 2)

        accepted = result.apply_translation(
            "paragraph:0-1",
            "Это предложение физически перенесено и продолжается на второй строке.",
        )

        self.assertTrue(accepted)
        self.assertEqual(result.render().count("\r\n"), source.count("\r\n"))
        self.assertEqual(
            " ".join(result.lines_out[:2]),
            "Это предложение физически перенесено и продолжается на второй строке.",
        )

    def test_markdown_blocks_and_formatted_lines_are_not_paragraph_grouped(self) -> None:
        source = "\n".join(
            [
                "# Heading",
                "* First item",
                "Read [Guide](guide.md).",
                "$(#ED7014)Mythic$() item.",
            ]
        )

        result = selection.collect_book_markdown_selection(
            source,
            "",
            "force",
            smart_glue=False,
        )

        self.assertEqual(set(result.pending), {"0", "1", "2", "3"})
        self.assertFalse(any(key.startswith("paragraph:") for key in result.pending))

    def test_jar_processor_applies_one_paragraph_result_to_source_lines(self) -> None:
        source_path = "assets/demo/guide/en_us/page.md"
        target_path = "assets/demo/guide/ru_ru/page.md"
        source = (
            "This long sentence starts on the first line\r\n"
            "and finishes on the second physical line.\r\n"
        )
        target_lang = {
            "file": "ru_ru",
            "api": "ru",
            "regex": r"[А-Яа-яЁё]",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            jar_path = Path(temp_dir) / "guide.jar"
            with zipfile.ZipFile(jar_path, "w") as archive:
                archive.writestr(source_path, source)
            state = SimpleNamespace(
                should_run=lambda: True,
                wait_if_paused=lambda: None,
            )
            callbacks = SimpleNamespace(on_log=lambda *_args: None)
            writer = _Writer()
            service = _ParagraphService()
            JarProcessor(service, state, callbacks).process(
                str(jar_path),
                target_lang=target_lang,
                mode="force",
                output_mode="resourcepack",
                translate_mods=False,
                translate_books=True,
                pack_writer=writer,
            )

        self.assertEqual(len(service.received), 1)
        self.assertIn(
            "This long sentence starts",
            next(iter(service.received.values())),
        )
        output = writer.files[target_path].decode("utf-8")
        self.assertNotIn("This", output)
        self.assertEqual(output.count("\r\n"), source.count("\r\n"))

    def test_crlf_source_is_preserved_by_document_skeleton(self) -> None:
        source = "---\r\ntitle: Page\r\n---\r\n\r\n# Heading\r\nBody\r\n"

        result = selection.collect_book_markdown_selection(
            source,
            "",
            "force",
            smart_glue=False,
        )

        self.assertEqual(result.source_text, source)
        self.assertEqual(result.render(), source)
        self.assertEqual(
            result.pending,
            {
                "1": "Page",
                "4": "Heading",
                "5": "Body",
            },
        )
        self.assertTrue(all("\r" not in value for value in result.pending.values()))

    def test_mixed_line_endings_are_restored_exactly_after_translation(self) -> None:
        source = "# Heading\r\nBody line\nLast line\r"
        skeleton = MarkdownSkeleton.from_text(source)

        self.assertEqual(skeleton.line_endings, ("\r\n", "\n", "\r", ""))
        self.assertEqual(skeleton.render(skeleton.original_lines), source)

        result = selection.collect_book_markdown_selection(
            source,
            "",
            "force",
            smart_glue=False,
        )
        result.lines_out[0] = "# Заголовок"
        result.lines_out[1] = "Строка"

        self.assertEqual(result.render(), "# Заголовок\r\nСтрока\nLast line\r")

    def test_interface_json_and_book_json_use_their_correct_pipelines(self) -> None:
        target_lang = {
            "file": "ru_ru",
            "api": "ru",
            "regex": r"[А-Яа-яЁё]",
        }
        state = SimpleNamespace(
            should_run=lambda: True,
            wait_if_paused=lambda: None,
        )
        callbacks = SimpleNamespace(on_log=lambda *_args: None)

        with tempfile.TemporaryDirectory() as temp_dir:
            jar_path = Path(temp_dir) / "routing.jar"
            with zipfile.ZipFile(jar_path, "w") as archive:
                archive.writestr(
                    "assets/demo/lang/en_us.json",
                    '{"demo.name":"Machine Name"}',
                )
                archive.writestr(
                    "assets/demo/patchouli_books/manual/en_us/entries/page.json",
                    '{"name":"$(#ED7014)Epic$() Machine"}',
                )

            service = _RoutingService()
            processor = JarProcessor(service, state, callbacks)
            processor.process(
                str(jar_path),
                target_lang=target_lang,
                mode="force",
                output_mode="resourcepack",
                translate_mods=True,
                translate_books=True,
                pack_writer=_Writer(),
            )

        self.assertEqual(
            [kind for kind, _strings, _kwargs in service.calls],
            ["plain", "plain"],
        )
        self.assertEqual(service.calls[0][2].get("prompt_type", "mods"), "mods")
        self.assertEqual(service.calls[1][2]["prompt_type"], "books")
        book_payload = next(iter(service.calls[1][1].values()))
        self.assertIn("[#0#]", book_payload)
        self.assertNotIn("$(#ED7014)", book_payload)

    def test_skip_mode_rebuilds_existing_translation_with_source_endings(self) -> None:
        source_path = "assets/demo/guide/en_us/page.md"
        target_path = "assets/demo/guide/ru_ru/page.md"
        source = "# Heading\r\n\r\nBody text.\r\n"
        existing = "# Заголовок\n\nОсновной текст.\n"
        target_lang = {
            "file": "ru_ru",
            "api": "ru",
            "regex": r"[А-Яа-яЁё]",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            jar_path = Path(temp_dir) / "guide.jar"
            with zipfile.ZipFile(jar_path, "w") as archive:
                archive.writestr(source_path, source)
                archive.writestr(target_path, existing)

            state = SimpleNamespace(
                should_run=lambda: True,
                wait_if_paused=lambda: None,
            )
            callbacks = SimpleNamespace(on_log=lambda *_args: None)
            writer = _Writer()
            processor = JarProcessor(_BreakingService(), state, callbacks)
            processor.process(
                str(jar_path),
                target_lang=target_lang,
                mode="skip",
                output_mode="resourcepack",
                translate_mods=False,
                translate_books=True,
                pack_writer=writer,
            )

        output = writer.files[target_path].decode("utf-8")
        self.assertEqual(output, "# Заголовок\r\n\r\nОсновной текст.\r\n")

    def test_list_item_containing_only_guideme_tag_is_not_translated(self) -> None:
        source = '* <ItemLink id="advanced_ae:walk_speed_card" />\n'

        result = selection.collect_book_markdown_selection(
            source,
            "",
            "force",
            smart_glue=False,
        )

        self.assertEqual(result.total_translatable, 0)
        self.assertEqual(result.pending, {})

    def test_text_after_guideme_tag_remains_translatable(self) -> None:
        source = '<ItemLink id="energy_card" /> in order to increase capacity\n'

        result = selection.collect_book_markdown_selection(
            source,
            "",
            "force",
            smart_glue=False,
        )

        self.assertEqual(
            result.pending,
            {"0": '<ItemLink id="energy_card" /> in order to increase capacity'},
        )

    def test_structure_validator_accepts_equivalent_crlf_and_lf_documents(self) -> None:
        source = "---\r\nnavigation:\r\n  title: Page\r\n---\r\n\r\n# Heading\r\n"
        translated = "---\nnavigation:\n  title: Страница\n---\n\n# Заголовок\n"

        self.assertIsNone(
            selection.validate_markdown_structure(source, translated)
        )

    def test_smart_glue_never_changes_markdown_line_structure(self) -> None:
        source = "\n".join(
            [
                "---",
                "navigation:",
                "  title: Index",
                "---",
                "",
                "![Logo](assets/logo.png)",
                "",
                "# Heading",
                "",
                "Paragraph wrapped across",
                "two source lines.",
                "",
                "* First item",
                "* Second item",
            ]
        )

        result = selection.collect_book_markdown_selection(
            source,
            "",
            "force",
            smart_glue=True,
        )

        self.assertEqual(result.source_text, source)
        self.assertEqual(result.lines_out, source.split("\n"))
        self.assertEqual(
            result.pending,
            {
                "2": "Index",
                "7": "Heading",
                "paragraph:9-10": "Paragraph wrapped across two source lines.",
                "12": "First item",
                "13": "Second item",
            },
        )
        self.assertEqual(result.line_meta["7"], ("# ", ""))
        self.assertEqual(result.line_meta["12"], ("* ", ""))

    def test_append_ignores_target_from_a_different_document_structure(self) -> None:
        source = "\n".join(
            [
                "---",
                "navigation:",
                "  title: Current Page",
                "---",
                "",
                "# Current Heading",
                "",
                "Current paragraph.",
                "",
                '<RecipeFor id="current_recipe" />',
            ]
        )
        stale_target = "\n".join(
            [
                "---",
                "navigation:",
                "  title: Старая страница",
                "---",
                "",
                "# Старый заголовок",
                "",
                "Старый абзац.",
                "Лишняя строка старой версии.",
            ]
        )

        result = selection.collect_book_markdown_selection(
            source,
            stale_target,
            "append",
            smart_glue=False,
        )

        self.assertEqual(
            result.pending,
            {
                "2": "Current Page",
                "5": "Current Heading",
                "7": "Current paragraph.",
            },
        )
        self.assertEqual(result.lines_out, source.split("\n"))

    def test_markdown_link_delimiters_are_protected_separately(self) -> None:
        masked, mapping = text_processing.mask_protected_fragments(
            "Read about [ME Networks](me-network-connections.md)."
        )

        self.assertNotIn("[ME Networks", masked)
        self.assertIn("ME Networks", masked)
        self.assertIn("[", mapping.values())
        self.assertIn("](me-network-connections.md)", mapping.values())

    def test_inline_markdown_emphasis_is_protected_per_line(self) -> None:
        italic_masked, italic_mapping = text_processing.mask_protected_fragments(
            "find the recipe again, and click move *again*."
        )
        bold_masked, bold_mapping = text_processing.mask_protected_fragments(
            "With **ae2helpers**, wait in the terminal."
        )

        self.assertNotIn("*again*", italic_masked)
        self.assertEqual(list(italic_mapping.values()).count("*"), 2)
        self.assertNotIn("**ae2helpers**", bold_masked)
        self.assertEqual(list(bold_mapping.values()).count("**"), 2)

    def test_compound_ae2_brand_is_protected_as_one_fragment(self) -> None:
        source = (
            "The Quantum Bridge Card is for "
            "[AE2wtlib Wireless Terminals](wireless_terminals.md)."
        )

        _masked, mapping = text_processing.mask_protected_fragments(source)

        self.assertIn("AE2wtlib", mapping.values())
        self.assertNotIn("AE", mapping.values())

    def test_structure_validator_rejects_broken_markdown(self) -> None:
        validator = getattr(selection, "validate_markdown_structure", None)
        self.assertIsNotNone(validator)
        if validator is None:
            return

        source = "\n".join(
            [
                "---",
                "navigation:",
                "  title: Index",
                "---",
                "",
                "# Heading",
                "",
                "Read [devices](devices.md).",
                '<ItemLink id="controller" />',
            ]
        )
        valid = source.replace("Index", "Оглавление").replace(
            "Heading", "Заголовок"
        ).replace("devices]", "устройства]")
        broken = valid.replace("[устройства]", "устройства]")

        self.assertIsNone(validator(source, valid))
        self.assertIn("structure", validator(source, broken).lower())

    def test_jar_processor_never_exposes_markdown_link_syntax_to_engine(self) -> None:
        source_path = "assets/demo/guide/en_us/page.md"
        target_path = "assets/demo/guide/ru_ru/page.md"
        source = "\n".join(
            [
                "---",
                "navigation:",
                "  title: Page",
                "---",
                "",
                "# Heading",
                "",
                "Read [devices](devices.md).",
            ]
        )
        target_lang = {
            "file": "ru_ru",
            "api": "ru",
            "regex": r"[А-Яа-яЁё]",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            jar_path = Path(temp_dir) / "guide.jar"
            with zipfile.ZipFile(jar_path, "w") as archive:
                archive.writestr(source_path, source)

            state = SimpleNamespace(
                should_run=lambda: True,
                wait_if_paused=lambda: None,
            )
            logs = []
            callbacks = SimpleNamespace(on_log=lambda *args: logs.append(args))
            writer = _Writer()
            processor = JarProcessor(
                _BreakingService(),
                state,
                callbacks,
            )

            processor.process(
                str(jar_path),
                target_lang=target_lang,
                mode="force",
                output_mode="resourcepack",
                translate_mods=False,
                translate_books=True,
                pack_writer=writer,
            )

            output = writer.files[target_path].decode("utf-8")
            self.assertIn("# Перевод: Heading", output)
            self.assertIn("[devices](devices.md)", output)
            self.assertFalse(any("восстанов" in message for message, _tag in logs))

    def test_jar_processor_writes_crlf_markdown_without_structure_error(self) -> None:
        source_path = "assets/demo/guide/en_us/page.md"
        target_path = "assets/demo/guide/ru_ru/page.md"
        source = (
            "---\r\n"
            "navigation:\r\n"
            "  title: Page\r\n"
            "---\r\n"
            "\r\n"
            "# Heading\r\n"
            "\r\n"
            '* <ItemLink id="demo:card" />\r\n'
            "Body text.\r\n"
        )
        target_lang = {
            "file": "ru_ru",
            "api": "ru",
            "regex": r"[А-Яа-яЁё]",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            jar_path = Path(temp_dir) / "guide.jar"
            with zipfile.ZipFile(jar_path, "w") as archive:
                archive.writestr(source_path, source)

            state = SimpleNamespace(
                should_run=lambda: True,
                wait_if_paused=lambda: None,
            )
            callbacks = SimpleNamespace(on_log=lambda *_args: None)
            writer = _Writer()
            processor = JarProcessor(_BreakingService(), state, callbacks)

            processor.process(
                str(jar_path),
                target_lang=target_lang,
                mode="force",
                output_mode="resourcepack",
                translate_mods=False,
                translate_books=True,
                pack_writer=writer,
            )

            self.assertIn(target_path, writer.files)
            output = writer.files[target_path].decode("utf-8")
            self.assertEqual(output.count("\r\n"), source.count("\r\n"))
            self.assertEqual(output.replace("\r\n", "").count("\r"), 0)
            self.assertIn('<ItemLink id="demo:card" />', output)


if __name__ == "__main__":
    unittest.main()
