import json
import os
import shutil
import tempfile
import unittest
import zipfile

from mineai.processors.analyzer import ModpackAnalyzer
from mineai.processors.estimator import StringEstimator
from mineai.runtime.state import JobState

TARGET_LANG = {"file": "ru_ru", "api": "ru", "name": "Russian", "regex": r"[А-Яа-яЁё]"}


class AnalyzerEstimatorAlignmentTests(unittest.TestCase):
    def _make_jar(self, entries: dict[str, str]) -> str:
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp_dir)
        path = os.path.join(temp_dir, "books.jar")
        with zipfile.ZipFile(path, "w") as archive:
            for name, content in entries.items():
                archive.writestr(name, content)
        return path

    def _counts(
        self,
        path: str,
        *,
        translate_mods: bool = False,
        translate_books: bool = True,
    ):
        state = JobState()
        state.start()
        rows = []
        analyzed = ModpackAnalyzer(state)._analyze_jar(
            path,
            "ru_ru.json",
            TARGET_LANG["regex"],
            translate_mods,
            translate_books,
            lambda *row: rows.append(row), "Example",
        )
        estimated = StringEstimator(state)._estimate_jar(
            path,
            "ru_ru.json",
            TARGET_LANG,
            "force",
            translate_mods,
            translate_books,
            False,
        )
        return analyzed, estimated, rows

    def test_conflicting_duplicate_locale_uses_legacy_count_fallback(self) -> None:
        path = self._make_jar({
            "assets/example/lang/en_us.json": (
                '{"example.name":"Old","example.name":"Resources"}'
            ),
        })

        analyzed, estimated, rows = self._counts(
            path,
            translate_mods=True,
            translate_books=False,
        )

        self.assertEqual(analyzed, (1, 0))
        self.assertEqual(estimated, 1)
        self.assertEqual(rows, [("📦", "Example", "Интерфейс", 0, 1, 0)])

    def test_legacy_patchouli_string_pages_use_legacy_count_fallback(self) -> None:
        path = self._make_jar({
            "data/example/patchouli_books/guide/en_us/entries/legacy.json": (
                '{"name":"Resources","pages":["Welcome to the guide."]}'
            ),
        })

        analyzed, estimated, rows = self._counts(path)

        self.assertEqual(analyzed, (2, 0))
        self.assertEqual(estimated, 2)
        self.assertEqual(rows, [("📚", "Example", "Книги · Patchouli", 0, 2, 0)])

    def test_nonlocalized_research_json_is_ignored_by_both(self) -> None:
        path = self._make_jar({
            "assets/example/research/topic.json": json.dumps({"title": "Research Topic"}),
        })
        analyzed, estimated, rows = self._counts(path)
        self.assertEqual(analyzed, (0, 0))
        self.assertEqual(estimated, 0)
        self.assertEqual(rows, [])

    def test_data_pack_patchouli_book_is_counted_by_both(self) -> None:
        path = self._make_jar({
            "data/example/patchouli_books/guide/en_us/entries/start.json": (
                json.dumps({
                    "name": "Getting Started",
                    "pages": [{"text": "Welcome to the guide."}],
                })
            ),
        })

        analyzed, estimated, rows = self._counts(path)

        self.assertEqual(analyzed, (2, 0))
        self.assertEqual(estimated, 2)
        self.assertEqual(rows, [("📚", "Example", "Книги · Patchouli", 0, 2, 0)])

    def test_oracle_index_book_is_counted_by_analysis_and_estimator(self) -> None:
        path = self._make_jar({
            "assets/example/oracle_index/books/guide/index.mdx": (
                "# Getting Started\n\nWelcome to the guide.\n"
            ),
        })

        analyzed, estimated, rows = self._counts(path)

        self.assertEqual(analyzed, (2, 0))
        self.assertEqual(estimated, 2)
        self.assertEqual(rows, [("📚", "Example", "Книги · Oracle Index", 0, 2, 0)])

    def test_explicitly_localized_text_is_counted_outside_book_folders(self) -> None:
        path = self._make_jar({
            "assets/example/minigame/en_us/abyss.txt": "Enter the abyss.",
        })

        analyzed, estimated, rows = self._counts(path)

        self.assertEqual(analyzed, (1, 0))
        self.assertEqual(estimated, 1)
        self.assertEqual(rows, [("📚", "Example", "Книги · Markdown / MDX", 0, 1, 0)])

    def test_legacy_lang_file_is_counted_as_mod_interface(self) -> None:
        path = self._make_jar({
            "assets/example/lang/en_US.lang": (
                "example.ready=Ready\n"
                "example.machine=Machine controls\n"
            ),
        })
        state = JobState()
        state.start()
        rows = []

        analyzed = ModpackAnalyzer(state)._analyze_jar(
            path,
            "ru_ru.json",
            TARGET_LANG["regex"],
            True,
            False,
            lambda *row: rows.append(row),
            "Example",
        )
        estimated = StringEstimator(state)._estimate_jar(
            path,
            "ru_ru.json",
            TARGET_LANG,
            "force",
            True,
            False,
            False,
        )

        self.assertEqual(analyzed, (2, 0))
        self.assertEqual(estimated, 2)
        self.assertEqual(rows, [("📦", "Example", "Интерфейс", 0, 2, 0)])

    def test_custom_application_localization_is_not_counted_as_minecraft_lang(self) -> None:
        path = self._make_jar({
            "crash_assistant_localization/en_us.json": json.dumps({
                "crash.title": "Minecraft crashed",
            }),
        })
        state = JobState()
        state.start()

        analyzed = ModpackAnalyzer(state)._analyze_jar(
            path,
            "ru_ru.json",
            TARGET_LANG["regex"],
            True,
            False,
            lambda *_row: None,
            "Crash Assistant",
        )
        estimated = StringEstimator(state)._estimate_jar(
            path,
            "ru_ru.json",
            TARGET_LANG,
            "force",
            True,
            False,
            False,
        )

        self.assertEqual(analyzed, (0, 0))
        self.assertEqual(estimated, 0)

    def test_shorthand_namespace_lang_json_remains_counted(self) -> None:
        path = self._make_jar({
            "ae2ct/lang/en_us.json": json.dumps({"ae2ct.tree": "Crafting Tree"}),
        })
        state = JobState()
        state.start()

        analyzed = ModpackAnalyzer(state)._analyze_jar(
            path,
            "ru_ru.json",
            TARGET_LANG["regex"],
            True,
            False,
            lambda *_row: None,
            "AE2CT",
        )
        estimated = StringEstimator(state)._estimate_jar(
            path,
            "ru_ru.json",
            TARGET_LANG,
            "force",
            True,
            False,
            False,
        )

        self.assertEqual(analyzed, (1, 0))
        self.assertEqual(estimated, 1)

    def test_root_markdown_book_without_en_us_is_counted_by_both(self) -> None:
        path = self._make_jar({
            "assets/example/manual/page.md": "Manual page text",
        })

        analyzed, estimated, rows = self._counts(path)

        self.assertEqual(analyzed, (1, 0))
        self.assertEqual(estimated, 1)
        self.assertEqual(rows, [("📚", "Example", "Книги · Markdown / MDX", 0, 1, 0)])

    def test_wrapped_markdown_paragraph_is_one_translated_unit(self) -> None:
        path = self._make_jar({
            "assets/example/manual/page.md": (
                "This sentence is physically wrapped\n"
                "and continues on the second line."
            ),
            "assets/example/manual/ru_ru/page.md": (
                "Это предложение физически перенесено\n"
                "и продолжается на второй строке."
            ),
        })

        analyzed, estimated, rows = self._counts(path)

        self.assertEqual(analyzed, (1, 1))
        self.assertEqual(estimated, 1)
        self.assertEqual(rows, [("📚", "Example", "Книги · Markdown / MDX", 1, 1, 100)])

    def test_unrelated_asset_directories_are_not_mistaken_for_books(self) -> None:
        path = self._make_jar({
            "assets/example/textures/gui/guide/readme.txt": "Texture notes",
            "assets/example/bookshelf/readme.txt": "Bookshelf notes",
        })

        analyzed, estimated, rows = self._counts(path)

        self.assertEqual(analyzed, (0, 0))
        self.assertEqual(estimated, 0)
        self.assertEqual(rows, [])

    def test_en_us_research_file_is_counted_by_both(self) -> None:
        path = self._make_jar({
            "assets/example/research/en_us/topic.json": json.dumps({"title": "Research Topic"}),
        })
        analyzed, estimated, _rows = self._counts(path)
        self.assertEqual(analyzed, (1, 0))
        self.assertEqual(estimated, 1)

    def test_namespace_containing_guide_does_not_hide_regular_lang_file(self) -> None:
        path = self._make_jar({
            "assets/guideme/lang/en_us.json": json.dumps(
                {"guideme.screen.title": "Guide screen"}
            ),
        })
        state = JobState()
        state.start()
        analyzed = ModpackAnalyzer(state)._analyze_jar(
            path,
            "ru_ru.json",
            TARGET_LANG["regex"],
            True,
            False,
            lambda *_row: None,
            "GuideME",
        )
        estimated = StringEstimator(state)._estimate_jar(
            path,
            "ru_ru.json",
            TARGET_LANG,
            "force",
            True,
            False,
            False,
        )

        self.assertEqual(analyzed, (1, 0))
        self.assertEqual(estimated, 1)

    def test_malformed_builtin_locale_matches_append_estimator(self) -> None:
        path = self._make_jar({
            "assets/example/lang/en_us.json": json.dumps({
                "example.ready": "Ready",
                "example.partial": "Open settings",
                "example.missing": "Machine controls",
            }),
            "assets/example/lang/ru_ru.json": '{"example.ready": "Готово"',
        })
        state = JobState()
        state.start()
        analyzed = ModpackAnalyzer(state)._analyze_jar(
            path,
            "ru_ru.json",
            TARGET_LANG["regex"],
            True,
            False,
            lambda *_row: None,
            "Example",
        )
        estimated = StringEstimator(state)._estimate_jar(
            path,
            "ru_ru.json",
            TARGET_LANG,
            "append",
            True,
            False,
            False,
        )

        self.assertEqual(analyzed, (3, 0))
        self.assertEqual(estimated, 3)

    def test_book_metadata_is_not_counted_twice_with_mods_enabled(self) -> None:
        path = self._make_jar({
            "assets/immersiveengineering/lang/en_us.json": json.dumps(
                {"manual.immersiveengineering.resources": "Resources"}
            ),
            "assets/immersiveengineering/manual/en_us/index.txt": (
                "Introduction\nEngineering\nManual body.\n"
            ),
        })
        state = JobState()
        state.start()
        analyzed = ModpackAnalyzer(state)._analyze_jar(
            path,
            "ru_ru.json",
            TARGET_LANG["regex"],
            True,
            True,
            lambda *_row: None,
            "Immersive Engineering",
        )
        estimated = StringEstimator(state)._estimate_jar(
            path,
            "ru_ru.json",
            TARGET_LANG,
            "force",
            True,
            True,
            False,
        )

        self.assertEqual(analyzed[0], estimated)


if __name__ == "__main__":
    unittest.main()
