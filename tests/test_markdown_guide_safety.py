import tempfile
import unittest
import zipfile
from pathlib import Path

from mineai.engines.base import EngineCallbacks
from mineai.processors.analyzer import ModpackAnalyzer
from mineai.processors.estimator import StringEstimator
from mineai.processors.jar import JarProcessor
from mineai.processors.markdown_guides import (
    get_markdown_target_path,
    is_source_markdown_guide,
)
from mineai.processors.selection import (
    collect_book_markdown_selection,
    markdown_line_has_translatable_prose,
)
from mineai.runtime.state import JobState
from mineai.text_processing import mask_protected_fragments, unmask_translation


TARGET_LANG = {
    "file": "ru_ru",
    "api": "ru",
    "name": "Russian",
    "regex": r"[А-Яа-яЁё]",
}


class _Config:
    def __init__(self, smart_glue: bool = False) -> None:
        self.smart_glue = smart_glue

    def getboolean(self, _section: str, key: str) -> bool:
        return self.smart_glue if key == "smart_glue" else False


class _ShieldingService:
    def __init__(self, smart_glue: bool = False) -> None:
        self.config = _Config(smart_glue)
        self.calls: list[dict[str, str]] = []

    def translate_dict(self, strings, _target_lang, _callbacks, **_kwargs):
        self.calls.append(dict(strings))
        translated: dict[str, str] = {}
        for key, source in strings.items():
            masked, mapping = mask_protected_fragments(source)
            translated_masked = masked
            translated_masked = translated_masked.replace(
                "is used to store items.",
                "используется для хранения предметов.",
            )
            translated_masked = translated_masked.replace(
                "This machine stores energy.",
                "Эта машина хранит энергию.",
            )
            translated_masked = translated_masked.replace(
                "New paragraph",
                "Новый абзац",
            )
            translated[key] = unmask_translation(translated_masked, mapping)
        return translated


class _Writer:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def write(self, path: str, payload: bytes) -> None:
        self.files[path] = payload


def _callbacks() -> EngineCallbacks:
    return EngineCallbacks(
        should_run=lambda: True,
        wait_if_paused=lambda: None,
        on_log=lambda *_args: None,
        on_status=lambda *_args: None,
        on_progress=lambda *_args: None,
    )


def _state() -> JobState:
    state = JobState()
    state.start()
    return state


class MarkdownSelectionSafetyTests(unittest.TestCase):
    def _selection(self, text: str, *, smart_glue: bool = False):
        return collect_book_markdown_selection(
            text,
            "",
            "append",
            smart_glue=smart_glue,
        )

    def test_markup_only_component_is_not_selected(self) -> None:
        source = '<ItemLink id="foo" />'
        selection = self._selection(source)

        self.assertEqual(selection.pending, {})
        self.assertEqual(selection.total_translatable, 0)
        self.assertFalse(markdown_line_has_translatable_prose(source))

    def test_itemlink_plus_prose_is_selected_and_tag_round_trips(self) -> None:
        source = '<ItemLink id="foo" /> is used to store items.'
        selection = self._selection(source)

        self.assertEqual(selection.pending, {"0": source})
        self.assertTrue(markdown_line_has_translatable_prose(source))

        masked, mapping = mask_protected_fragments(source)
        self.assertNotIn("ItemLink", masked)
        self.assertIn('<ItemLink id="foo" />', mapping.values())
        translated = unmask_translation(
            masked.replace(
                "is used to store items.",
                "используется для хранения предметов.",
            ),
            mapping,
        )
        self.assertEqual(
            translated,
            '<ItemLink id="foo" /> используется для хранения предметов.',
        )

    def test_html_markup_plus_prose_is_selected_and_href_is_protected(self) -> None:
        source = '<a href="facades.md">Facades</a> will be hidden when disabled.'
        selection = self._selection(source)

        self.assertEqual(selection.pending, {"0": source})
        masked, mapping = mask_protected_fragments(source)
        self.assertNotIn("facades.md", masked)
        self.assertIn('<a href="facades.md">', mapping.values())
        self.assertIn("</a>", mapping.values())
        restored = unmask_translation(masked, mapping)
        self.assertEqual(restored, source)

    def test_normal_markdown_text_is_still_selected(self) -> None:
        source = "This machine stores energy."
        selection = self._selection(source)

        self.assertEqual(selection.pending, {"0": source})
        self.assertEqual(selection.total_translatable, 1)

    def test_image_and_markup_only_lines_do_not_create_translation_work(self) -> None:
        source = "\n".join(
            [
                "![image](foo.png)",
                '<SomeComponent foo="bar" />',
                "![image](foo.png) Some caption text",
            ]
        )
        selection = self._selection(source)

        self.assertEqual(
            selection.pending,
            {"2": "![image](foo.png) Some caption text"},
        )
        self.assertEqual(selection.total_translatable, 1)

        image = "![image](foo.png)"
        masked, mapping = mask_protected_fragments(image)
        self.assertNotIn("foo.png", masked)
        self.assertIn("![image]", mapping.values())
        self.assertIn("(foo.png)", mapping.values())
        self.assertEqual(unmask_translation(masked, mapping), image)

    def test_edge_case_markup_lines_detect_only_real_prose(self) -> None:
        translatable = [
            '<ItemLink id="foo" />Text immediately after tag',
            '<ItemLink id="foo" /> Text after tag',
            "<strong>Important:</strong> Do not break this.",
            '<a href="../path/file.md">Click here</a> to continue.',
            "![image](foo.png) Some caption text",
        ]
        technical_only = [
            "![image](foo.png)",
            '<SomeComponent foo="bar" />',
            "<item:minecraft:dirt>",
        ]

        for line in translatable:
            with self.subTest(line=line):
                self.assertTrue(markdown_line_has_translatable_prose(line))
        for line in technical_only:
            with self.subTest(line=line):
                self.assertFalse(markdown_line_has_translatable_prose(line))

    def test_smart_glue_does_not_merge_markdown_structure(self) -> None:
        source = "\n".join(
            [
                "---",
                "navigation:",
                "  title: Index/Table of Contents",
                "  position: 0",
                "---",
                "![Logo](assets/logo.png)",
                "This machine stores energy.",
            ]
        )
        selection = self._selection(source, smart_glue=True)

        self.assertEqual(selection.total_translatable, 2)
        self.assertEqual(
            selection.pending,
            {
                "2": "Index/Table of Contents",
                "6": "This machine stores energy.",
            },
        )
        self.assertIn("---\n![Logo](assets/logo.png)", selection.source_text)


class MarkdownGuidePathTests(unittest.TestCase):
    def test_direct_ae2_guide_without_en_us_is_source_markdown(self) -> None:
        self.assertTrue(
            is_source_markdown_guide(
                "assets/ae2/ae2guide/getting-started.md"
            )
        )

    def test_existing_en_us_guide_support_is_preserved(self) -> None:
        self.assertTrue(
            is_source_markdown_guide(
                "assets/demo/guide/en_us/getting-started.md"
            )
        )

    def test_unrelated_or_already_localized_markdown_is_not_source(self) -> None:
        self.assertFalse(
            is_source_markdown_guide("assets/demo/docs/readme.md")
        )
        self.assertFalse(
            is_source_markdown_guide(
                "assets/ae2/ae2guide/_ru_ru/getting-started.md"
            )
        )

    def test_target_path_uses_guideme_locale_subdirectory(self) -> None:
        source = "assets/ae2/ae2guide/getting-started.md"
        target = get_markdown_target_path(source, "ru_ru")

        self.assertEqual(
            target,
            "assets/ae2/ae2guide/_ru_ru/getting-started.md",
        )
        self.assertNotEqual(source, target)
        self.assertEqual(
            get_markdown_target_path(
                "assets/demo/guide/en_us/page.md",
                "ru_ru",
            ),
            "assets/demo/guide/ru_ru/page.md",
        )


class MarkdownGuidePipelineTests(unittest.TestCase):
    @staticmethod
    def _write_jar(path: Path, files: dict[str, str]) -> None:
        with zipfile.ZipFile(path, "w") as archive:
            for internal, content in files.items():
                archive.writestr(internal, content.encode("utf-8"))

    def test_direct_ae2_fixture_matches_analyzer_estimator_and_processor(self) -> None:
        source_path = "assets/ae2/ae2guide/getting-started.md"
        target_path = "assets/ae2/ae2guide/_ru_ru/getting-started.md"
        source = "\n".join(
            [
                '<ItemLink id="foo" /> is used to store items.',
                '<SomeComponent foo="bar" />',
                "This machine stores energy.",
            ]
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            jar_path = Path(temp_dir) / "ae2.jar"
            self._write_jar(jar_path, {source_path: source})
            source_jar_bytes = jar_path.read_bytes()

            state = _state()
            analyzer_rows: list[tuple] = []
            analyzer = ModpackAnalyzer(state)
            analyzed, translated = analyzer._analyze_jar(
                str(jar_path),
                "ru_ru.json",
                TARGET_LANG["regex"],
                False,
                True,
                lambda *row: analyzer_rows.append(row),
                "AE2",
            )

            estimator = StringEstimator(state)
            estimated = estimator.estimate(
                [str(jar_path)],
                [],
                [],
                [],
                target_lang=TARGET_LANG,
                mode="append",
                translate_mods=False,
                translate_books=True,
                translate_quests=False,
                smart_glue=False,
            )

            service = _ShieldingService()
            writer = _Writer()
            JarProcessor(service, state, _callbacks()).process(
                str(jar_path),
                target_lang=TARGET_LANG,
                mode="append",
                output_mode="resourcepack",
                translate_mods=False,
                translate_books=True,
                pack_writer=writer,
            )

            self.assertEqual(analyzed, 2)
            self.assertEqual(translated, 0)
            self.assertEqual(estimated, 2)
            self.assertEqual(len(service.calls), 1)
            self.assertEqual(
                service.calls[0],
                {
                    "0": '<ItemLink id="foo" /> is used to store items.',
                    "2": "This machine stores energy.",
                },
            )
            self.assertIn(target_path, writer.files)
            self.assertNotIn(source_path, writer.files)
            output = writer.files[target_path].decode("utf-8")
            self.assertIn(
                '<ItemLink id="foo" /> используется для хранения предметов.',
                output,
            )
            self.assertIn("Эта машина хранит энергию.", output)
            self.assertEqual(jar_path.read_bytes(), source_jar_bytes)
            self.assertTrue(analyzer_rows)

    def test_direct_ae2_append_skip_force_keep_estimator_processor_parity(self) -> None:
        source_path = "assets/ae2/ae2guide/page.md"
        target_path = "assets/ae2/ae2guide/_ru_ru/page.md"
        source = "Existing paragraph\nNew paragraph"
        target = "Существующий абзац\nNew paragraph"

        expected = {"append": 1, "skip": 1, "force": 2}
        for mode, expected_count in expected.items():
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp_dir:
                jar_path = Path(temp_dir) / "ae2.jar"
                self._write_jar(
                    jar_path,
                    {
                        source_path: source,
                        target_path: target,
                    },
                )
                state = _state()
                estimator = StringEstimator(state)
                estimated = estimator.estimate(
                    [str(jar_path)],
                    [],
                    [],
                    [],
                    target_lang=TARGET_LANG,
                    mode=mode,
                    translate_mods=False,
                    translate_books=True,
                    translate_quests=False,
                    smart_glue=False,
                )

                service = _ShieldingService()
                writer = _Writer()
                JarProcessor(service, state, _callbacks()).process(
                    str(jar_path),
                    target_lang=TARGET_LANG,
                    mode=mode,
                    output_mode="resourcepack",
                    translate_mods=False,
                    translate_books=True,
                    pack_writer=writer,
                )
                actual = len(service.calls[0]) if service.calls else 0

                self.assertEqual(estimated, expected_count)
                self.assertEqual(actual, expected_count)
                self.assertIn(target_path, writer.files)


if __name__ == "__main__":
    unittest.main()
