"""Regression tests for the Beta43 read-only preview and audit model."""

from __future__ import annotations

import unittest
from pathlib import Path
import tempfile
import zipfile

from mineai.preview import (
    PreviewBuilder,
    PreviewInput,
    build_quest_graph_layout,
    _candidate_book_path,
    build_preview_from_directory,
    discover_preview_items,
    preview_selection_key,
    render_preview_html,
)


class Beta43PreviewTests(unittest.TestCase):
    def test_preview_exposes_stable_selection_keys_for_book_units(self):
        source = "# Guide\n\nHello machines\n"
        target = "# Руководство\n\nПривет машины\n"
        report = PreviewBuilder(target_regex=r"[А-Яа-яЁё]").build(
            [
                PreviewInput(
                    logical_path="assets/example/guide/en_us/start.md",
                    source_text=source,
                    target_text=target,
                    kind="book",
                )
            ]
        )

        document = report.documents[0]
        self.assertTrue(document.pages[0].unit_ids)
        unit_id = document.pages[0].unit_ids[0]
        issue = next(issue for issue in document.issues if issue.unit_id == unit_id)
        self.assertEqual(
            issue.selection_key,
            preview_selection_key(document.logical_path, unit_id),
        )

    def test_quest_graph_layout_contains_dependency_edges_and_levels(self):
        source = """{
\tquests: [
\t\t{id: "AAAABBBBCCCCDDDD" title: "First"}
\t\t{id: "1111222233334444" title: "Second" dependencies: ["AAAABBBBCCCCDDDD"]}
\t]
}
"""
        report = PreviewBuilder(target_regex=r"[А-Яа-яЁё]").build(
            [
                PreviewInput(
                    logical_path="config/ftbquests/quests/chapters/chapter.snbt",
                    source_text=source,
                    target_text=source.replace("First", "Первый").replace("Second", "Второй"),
                    kind="quest",
                )
            ]
        )

        layout = build_quest_graph_layout(report.documents[0])
        self.assertEqual({node.node_id for node in layout.nodes}, {
            "AAAABBBBCCCCDDDD",
            "1111222233334444",
        })
        self.assertEqual(
            layout.edges,
            (("AAAABBBBCCCCDDDD", "1111222233334444"),),
        )
        self.assertLess(
            layout.nodes[0].level,
            layout.nodes[1].level,
        )

    def test_quest_pages_expose_units_for_entry_level_retry(self):
        source = """{
\tquests: [{
\t\tid: "AAAABBBBCCCCDDDD"
\t\ttitle: "First quest"
\t}, {
\t\tid: "1111222233334444"
\t\ttitle: "Second quest"
\t}]
}
"""
        target = source.replace("First quest", "Первый квест")
        report = PreviewBuilder(target_regex=r"[А-Яа-яЁё]").build(
            [
                PreviewInput(
                    logical_path="config/ftbquests/quests/chapters/chapter.snbt",
                    source_text=source,
                    target_text=target,
                    kind="quest",
                )
            ]
        )

        pages = report.documents[0].pages
        self.assertEqual(len(pages), 2)
        self.assertTrue(pages[0].unit_ids)
        self.assertTrue(pages[1].unit_ids)
        self.assertNotEqual(set(pages[0].unit_ids), set(pages[1].unit_ids))

    def test_ftb_quest_preview_resolves_titles_from_other_chapter_files(self):
        dependency = "1111222233334444"
        source_dependency = f'''{{
\tquests: [{{
\t\tid: "{dependency}"
\t\ttitle: "First Chapter Quest"
\t}}]
}}
'''
        target_dependency = source_dependency.replace(
            "First Chapter Quest", "Первый квест главы"
        )
        source_main = f'''{{
\tquests: [{{
\t\tid: "AAAABBBBCCCCDDDD"
\t\tdependencies: ["{dependency}"]
\t}}]
}}
'''

        report = PreviewBuilder(target_regex=r"[А-Яа-яЁё]").build(
            [
                PreviewInput(
                    logical_path="config/ftbquests/quests/chapters/first.snbt",
                    source_text=source_dependency,
                    target_text=target_dependency,
                    kind="quest",
                ),
                PreviewInput(
                    logical_path="config/ftbquests/quests/chapters/main.snbt",
                    source_text=source_main,
                    target_text=source_main,
                    kind="quest",
                ),
            ]
        )

        self.assertEqual(
            report.documents[1].graph_nodes[0].dependency_titles,
            ("Первый квест главы",),
        )

    def test_ftb_quest_preview_uses_stable_fallback_when_title_is_missing(self):
        dependency = "1111222233334444"
        source = f'''{{
\tquests: [{{
\t\tid: "AAAABBBBCCCCDDDD"
\t\tdependencies: ["{dependency}"]
\t}}]
}}
'''

        report = PreviewBuilder(target_regex=r"[А-Яа-яЁё]").build(
            [
                PreviewInput(
                    logical_path="config/ftbquests/quests/chapters/main.snbt",
                    source_text=source,
                    target_text=source,
                    kind="quest",
                )
            ]
        )

        dependency_title = report.documents[0].graph_nodes[0].dependency_titles[0]
        self.assertNotEqual(dependency_title, "Название не найдено")
        self.assertIn(dependency[:8], dependency_title)

    def test_ftb_quest_preview_uses_translated_subtitle_when_title_is_absent(self):
        source = """{
\tquests: [{
\t\tid: "AAAABBBBCCCCDDDD"
\t\tquest_subtitle: "A simple quest"
\t}]
}
"""
        target = source.replace("A simple quest", "Простое задание")

        report = PreviewBuilder(target_regex=r"[А-Яа-яЁё]").build(
            [
                PreviewInput(
                    logical_path="config/ftbquests/quests/chapters/chapter.snbt",
                    source_text=source,
                    target_text=target,
                    kind="quest",
                )
            ]
        )

        self.assertEqual(
            report.documents[0].graph_nodes[0].title,
            "Простое задание",
        )

    def test_ftb_quest_preview_uses_catalog_titles_and_named_dependencies(self):
        source_chapter = """{
\tid: \"ABCDEFABCDEFABCD\"
\tquests: [
\t\t{
\t\t\tid: \"AAAABBBBCCCCDDDD\"
\t\t\tdependencies: [\"1111222233334444\"]
\t\t\ttasks: [{id: \"9999AAAABBBBCCCC\", item: {id: \"minecraft:stone\", count: 1}}]
\t\t}
\t\t{
\t\t\tid: \"1111222233334444\"
\t\t\ttasks: [{id: \"8888AAAABBBBCCCC\", item: {id: \"minecraft:dirt\", count: 1}}]
\t\t}
\t]
}
"""
        source_lang = """{
\tquest.AAAABBBBCCCCDDDD.title: \"&6First Quest\"
\tquest.1111222233334444.title: \"Second Quest\"
}
"""
        target_lang = """{
\tquest.AAAABBBBCCCCDDDD.title: \"&6Первый квест\"
\tquest.1111222233334444.title: \"Второй квест\"
}
"""

        report = PreviewBuilder(target_regex=r"[А-Яа-яЁё]").build(
            [
                PreviewInput(
                    logical_path="config/ftbquests/quests/lang/en_us.snbt",
                    target_path="config/ftbquests/quests/lang/ru_ru.snbt",
                    source_text=source_lang,
                    target_text=target_lang,
                    kind="quest",
                ),
                PreviewInput(
                    logical_path="config/ftbquests/quests/chapters/chapter.snbt",
                    source_text=source_chapter,
                    target_text=source_chapter,
                    kind="quest",
                ),
            ]
        )

        document = report.documents[1]
        self.assertEqual(
            [node.node_id for node in document.graph_nodes],
            ["AAAABBBBCCCCDDDD", "1111222233334444"],
        )
        self.assertEqual(
            [node.title for node in document.graph_nodes],
            ["&6Первый квест", "Второй квест"],
        )
        self.assertEqual(
            document.graph_nodes[0].dependency_titles,
            ("Второй квест",),
        )
        self.assertNotIn("9999AAAABBBBCCCC", [node.node_id for node in document.graph_nodes])

        html = render_preview_html(report, kind="quest")
        self.assertIn("Первый квест", html)
        self.assertIn("Второй квест", html)
        self.assertIn("Зависит от", html)

    def test_ftb_quest_preview_resolves_localization_key_from_json_catalog(self):
        chapter = """{
\tquests: [{
\t\tid: \"AAAABBBBCCCCDDDD\"
\t\ttitle: \"{atm9.quest.first}\"
\t}]
}
"""
        report = PreviewBuilder(target_regex=r"[А-Яа-яЁё]").build(
            [
                PreviewInput(
                    logical_path="assets/kubejs/lang/en_us.json",
                    source_text='{"atm9.quest.first":"First Quest"}',
                    target_text='{"atm9.quest.first":"Первый квест"}',
                    kind="quest_catalog",
                ),
                PreviewInput(
                    logical_path="config/ftbquests/quests/chapters/chapter.snbt",
                    source_text=chapter,
                    target_text=chapter,
                    kind="quest",
                ),
            ]
        )

        node = report.documents[0].graph_nodes[0]
        self.assertEqual(node.title, "Первый квест")
        self.assertNotIn("atm9.quest.first", render_preview_html(report))

    def test_markdown_preview_reports_untranslated_text_without_changing_structure(self):
        source = "# Guide\n\nHello [machines](machines.md)\n"
        target = "# Руководство\n\nHello [машины](machines.md)\n"

        report = PreviewBuilder(target_regex=r"[А-Яа-яЁё]").build(
            [
                PreviewInput(
                    logical_path="assets/example/guide/en_us/start.md",
                    source_text=source,
                    target_text=target,
                    kind="book",
                )
            ]
        )

        self.assertEqual(report.structure_errors, 0)
        self.assertEqual(report.untranslated, 1)
        self.assertEqual(report.documents[0].format, "markdown-v2")
        self.assertEqual(report.documents[0].pages[0].title, "Guide")
        self.assertTrue(any(issue.kind == "untranslated" for issue in report.issues))

    def test_markdown_preview_reports_format_code_spacing(self):
        source = "# Guide\n\n&6Extras\n"
        target = "# Руководство\n\n&6 Дополнительно\n"

        report = PreviewBuilder(target_regex=r"[А-Яа-яЁё]").build(
            [
                PreviewInput(
                    logical_path="assets/example/guide/en_us/start.md",
                    source_text=source,
                    target_text=target,
                    kind="book",
                )
            ]
        )

        self.assertEqual(report.structure_errors, 1)
        self.assertTrue(
            any("пробелы" in issue.message for issue in report.issues)
        )

    def test_quest_preview_keeps_ids_dependencies_and_item_requirements(self):
        source = """{
\tid: \"AAAABBBBCCCCDDDD\"
\ttitle: \"Collect a diamond\"
\ttasks: [{id: \"minecraft:diamond\", count: 1}]
\tdependencies: [\"1111222233334444\"]
\trewards: [{item: {id: \"minecraft:diamond\", count: 1}}]
}
"""
        target = source.replace("Collect a diamond", "Соберите алмаз")

        report = PreviewBuilder(target_regex=r"[А-Яа-яЁё]").build(
            [
                PreviewInput(
                    logical_path="config/ftbquests/quests/chapters/chapter.snbt",
                    source_text=source,
                    target_text=target,
                    kind="quest",
                )
            ]
        )

        self.assertEqual(report.structure_errors, 0)
        self.assertEqual(report.untranslated, 0)
        self.assertEqual(report.documents[0].format, "ftbquests-snbt")
        self.assertTrue(report.documents[0].graph_nodes)
        self.assertIn("AAAABBBBCCCCDDDD", report.documents[0].graph_nodes[0].node_id)

    def test_quest_preview_reports_changed_gameplay_structure(self):
        source = """{
\tid: \"AAAABBBBCCCCDDDD\"
\ttitle: \"Collect a diamond\"
\ttasks: [{id: \"minecraft:diamond\", count: 1}]
\tdependencies: [\"1111222233334444\"]
}
"""
        target = source.replace("minecraft:diamond", "minecraft:emerald")

        report = PreviewBuilder(target_regex=r"[А-Яа-яЁё]").build(
            [
                PreviewInput(
                    logical_path="config/ftbquests/quests/chapters/chapter.snbt",
                    source_text=source,
                    target_text=target,
                    kind="quest",
                )
            ]
        )

        self.assertGreaterEqual(report.structure_errors, 1)
        self.assertTrue(any(issue.kind == "structure" for issue in report.issues))

    def test_quest_language_catalog_missing_key_is_not_a_gameplay_structure_error(self):
        source = """{
\tchapter.AAAABBBBCCCCDDDD.title: \"Collect a diamond\"
\tchapter.1111222233334444.title: \"Build a machine\"
}
"""
        target = """{
\tchapter.AAAABBBBCCCCDDDD.title: \"Соберите алмаз\"
}
"""

        report = PreviewBuilder(target_regex=r"[А-Яа-яЁё]").build(
            [
                PreviewInput(
                    logical_path="config/ftbquests/quests/lang/en_us.snbt",
                    target_path="config/ftbquests/quests/lang/ru_ru.snbt",
                    source_text=source,
                    target_text=target,
                    kind="quest",
                )
            ]
        )

        self.assertEqual(report.structure_errors, 0)
        self.assertEqual(report.missing, 1)

    def test_quest_graph_uses_target_offsets_after_a_longer_translation(self):
        source = """{
\tid: \"AAAABBBBCCCCDDDD\"
\ttitle: \"A\"
}
{
\tid: \"1111222233334444\"
\ttitle: \"B\"
}
"""
        target = source.replace('title: "A"', 'title: "Очень длинный первый квест"').replace(
            'title: "B"', 'title: "Второй квест"'
        )

        report = PreviewBuilder(target_regex=r"[А-Яа-яЁё]").build(
            [
                PreviewInput(
                    logical_path="config/ftbquests/quests/chapters/chapter.snbt",
                    source_text=source,
                    target_text=target,
                    kind="quest",
                )
            ]
        )

        self.assertEqual(
            [node.title for node in report.documents[0].graph_nodes],
            ["Очень длинный первый квест", "Второй квест"],
        )

    def test_quest_preview_detects_changed_protected_ftb_reference_inside_text(self):
        source = """{
\tid: \"AAAABBBBCCCCDDDD\"
\tfeedback_message: \"Open <atm.quest.first>\"
}
"""
        target = source.replace("<atm.quest.first>", "<atm.quest.other>")

        report = PreviewBuilder(target_regex=r"[А-Яа-яЁё]").build(
            [
                PreviewInput(
                    logical_path="config/ftbquests/quests/chapters/chapter.snbt",
                    source_text=source,
                    target_text=target,
                    kind="quest",
                )
            ]
        )

        self.assertTrue(
            any("защищённые ссылки" in issue.message for issue in report.issues)
        )

    def test_html_preview_contains_book_pages_quest_graph_and_escaped_values(self):
        report = PreviewBuilder(target_regex=r"[А-Яа-яЁё]").build(
            [
                PreviewInput(
                    logical_path="assets/example/guide/en_us/start.md",
                    source_text="# <script>alert(1)</script>\n\nHello\n",
                    target_text="# Заголовок\n\nПривет\n",
                    kind="book",
                ),
                PreviewInput(
                    logical_path="config/ftbquests/quests/chapters/chapter.snbt",
                    source_text='{id: "AAAABBBBCCCCDDDD", title: "Quest"}',
                    target_text='{id: "AAAABBBBCCCCDDDD", title: "Квест"}',
                    kind="quest",
                ),
            ]
        )

        html = render_preview_html(report)

        self.assertIn("book-pages", html)
        self.assertIn("quest-graph", html)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("<script>", html)
        self.assertIn("Заголовок", html)
        self.assertIn("untranslated", html)

    def test_book_preview_uses_readable_two_page_spreads(self):
        report = PreviewBuilder(target_regex=r"[А-Яа-яЁё]").build(
            [
                PreviewInput(
                    logical_path="assets/example/guide/en_us/start.md",
                    source_text="# Guide\n\nHello\n\nSecond page\n",
                    target_text="# Руководство\n\nПривет\n\nВторая страница\n",
                )
            ]
        )

        html = render_preview_html(report, kind="book")

        self.assertIn("book-spread", html)
        self.assertIn("book-page-number", html)
        self.assertIn("book-target", html)
        self.assertIn("page-seam", html)
        self.assertNotIn("Показать оригинал", html)
        self.assertIn("Руководство", html)
        self.assertNotIn(">Guide</h4>", html)
        original = render_preview_html(report, kind="book", book_original=True)
        self.assertIn(">Guide</h4>", original)

    def test_html_preview_looks_like_game_content_and_hides_internal_ids(self):
        report = PreviewBuilder(target_regex=r"[А-Яа-яЁё]").build(
            [
                PreviewInput(
                    logical_path="assets/example/guide/en_us/start.md",
                    source_text="# &6Extras\n\nHello\n",
                    target_text="# &6Дополнительно\n\nПривет\n",
                    kind="book",
                ),
                PreviewInput(
                    logical_path="config/ftbquests/quests/chapters/chapter.snbt",
                    source_text='{id: "AAAABBBBCCCCDDDD", title: "Quest"}',
                    target_text='{id: "AAAABBBBCCCCDDDD", title: "Квест"}',
                    kind="quest",
                ),
            ]
        )

        html = render_preview_html(report)

        self.assertIn("minecraft-book", html)
        self.assertIn("minecraft-page", html)
        self.assertIn("quest-board", html)
        self.assertIn("quest-card", html)
        self.assertIn("mc-6", html)
        self.assertNotIn("data-node-id=\"AAAABBBBCCCCDDDD\"", html)
        self.assertNotIn(">AAAABBBBCCCCDDDD<", html)

    def test_text_preview_is_user_friendly_without_internal_unit_ids(self):
        report = PreviewBuilder(target_regex=r"[А-Яа-яЁё]").build(
            [
                PreviewInput(
                    logical_path="config/ftbquests/quests/chapters/chapter.snbt",
                    source_text='{id: "AAAABBBBCCCCDDDD", title: "Quest"}',
                    target_text='{id: "AAAABBBBCCCCDDDD", title: "Квест"}',
                    kind="quest",
                )
            ]
        )

        text = report.to_text()

        self.assertIn("Квест", text)
        self.assertIn("Зависимостей", text)
        self.assertNotIn("AAAABBBBCCCCDDDD", text)

    def test_quest_without_title_does_not_expose_id_in_user_preview(self):
        report = PreviewBuilder(target_regex=r"[А-Яа-яЁё]").build(
            [
                PreviewInput(
                    logical_path="config/ftbquests/quests/data.snbt",
                    source_text='{id: "AAAABBBBCCCCDDDD"}',
                    target_text='{id: "AAAABBBBCCCCDDDD"}',
                    kind="quest",
                )
            ]
        )

        self.assertNotIn("AAAABBBBCCCCDDDD", report.to_text())
        self.assertNotIn("AAAABBBBCCCCDDDD", render_preview_html(report))

    def test_preview_discovery_ignores_service_markdown_but_keeps_real_guides(self):
        self.assertFalse(_candidate_book_path("README.md"))
        self.assertFalse(_candidate_book_path("LICENSE.md"))
        self.assertFalse(_candidate_book_path("assets/fancymenu/credits_and_copyright.md"))
        self.assertTrue(_candidate_book_path("assets/ae2/ae2guide/en_us/index.md"))
        self.assertTrue(_candidate_book_path("assets/example/modonomicon/books/demo/entry.md"))
        self.assertTrue(
            _candidate_book_path(
                "assets/modern_industrialization/mi_guidebook/io_guide.md"
            )
        )
        self.assertTrue(_candidate_book_path("assets/example/handbook/start.xml"))
        self.assertTrue(_candidate_book_path("assets/example/engineering_book/page.properties"))

    def test_preview_discovers_locale_free_markdown_guidebook(self):
        source_path = "assets/modern_industrialization/mi_guidebook/io_guide.md"
        target_path = "assets/modern_industrialization/mi_guidebook/_ru_ru/io_guide.md"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mods = root / "mods"
            packs = root / "resourcepacks"
            mods.mkdir()
            packs.mkdir()
            with zipfile.ZipFile(mods / "modern-industrialization.jar", "w") as archive:
                archive.writestr(source_path, "# Guidebook\n\nHello machine\n")
            with zipfile.ZipFile(packs / "MineAI_Pack.zip", "w") as archive:
                archive.writestr(target_path, "# Руководство\n\nПривет машина\n")

            items, _ = discover_preview_items(directory)

        books = [item for item in items if item.logical_path == source_path]
        self.assertEqual(len(books), 1)
        self.assertEqual(books[0].target_path, target_path)
        self.assertEqual(books[0].target_text.count("Привет"), 1)

    def test_preview_discovers_patchouli_json_book_inside_mod_jar(self):
        source_path = (
            "assets/creategarnished/patchouli_books/garnishment_book/"
            "en_us/categories/foods.json"
        )
        target_path = source_path.replace("/en_us/", "/ru_ru/")
        source = '{"name":"Foods","description":"A cookbook"}'
        target = '{"name":"Еда","description":"Кулинарная книга"}'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mods = root / "mods"
            packs = root / "resourcepacks"
            mods.mkdir()
            packs.mkdir()
            with zipfile.ZipFile(mods / "creategarnished-test.jar", "w") as archive:
                archive.writestr(source_path, source)
            with zipfile.ZipFile(packs / "MineAI_Pack.zip", "w") as archive:
                archive.writestr(target_path, target)

            items, _ = discover_preview_items(directory)

        books = [item for item in items if item.logical_path == source_path]
        self.assertEqual(len(books), 1)
        self.assertEqual(books[0].kind, "book")
        report = PreviewBuilder(target_regex=r"[А-Яа-яЁё]").build(books)
        self.assertEqual(report.documents[0].format, "patchouli-book-json")
        self.assertEqual(
            {page.target for page in report.documents[0].pages},
            {"Еда", "Кулинарная книга"},
        )

    def test_preview_discovers_loose_modonomicon_book_and_pairs_datapack_target(self):
        source_path = (
            "kubejs/data/paganbless/modonomicon/books/pagan_guide/"
            "entries/features/herbalist_bench.json"
        )
        logical_path = source_path.removeprefix("kubejs/")
        source = '{"name":"Herbalist Bench","description":"A cutting bench"}'
        target = (
            '{"name":"mineai.book.paganbless.pagan_guide.entries.features.'
            'herbalist_bench.name",'
            '"description":"mineai.book.paganbless.pagan_guide.entries.features.'
            'herbalist_bench.description"}'
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_file = root / Path(source_path)
            source_file.parent.mkdir(parents=True)
            source_file.write_text(source, encoding="utf-8")
            pack = root / "resourcepacks" / "Beta43_preview.zip"
            pack.parent.mkdir()
            with zipfile.ZipFile(pack, "w") as archive:
                archive.writestr(logical_path, target)
                archive.writestr(
                    "assets/paganbless/lang/ru_ru.json",
                    '{"mineai.book.paganbless.pagan_guide.entries.features.'
                    'herbalist_bench.name":"Верстак травника",'
                    '"mineai.book.paganbless.pagan_guide.entries.features.'
                    'herbalist_bench.description":"Стол для резки"}',
                )

            items, _ = discover_preview_items(directory)
            books = [item for item in items if item.logical_path == logical_path]
            self.assertEqual(len(books), 1)
            report = PreviewBuilder(target_regex=r"[А-Яа-яЁё]").build(books)

        self.assertEqual(report.documents[0].format, "modonomicon-json-v1")
        self.assertIn("Верстак травника", " ".join(page.target for page in report.documents[0].pages))

    def test_directory_preview_is_read_only_and_pairs_latest_output_with_sources(self):
        source = "# Guide\n\nHello\n"
        target = "# Руководство\n\nПривет\n"
        quest = '{id: "AAAABBBBCCCCDDDD", title: "Quest"}\n'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "mods").mkdir()
            quest_path = root / "config" / "ftbquests" / "quests" / "chapters" / "chapter.snbt"
            quest_path.parent.mkdir(parents=True)
            quest_path.write_text(quest, encoding="utf-8")
            (quest_path.with_suffix(".snbt.bak")).write_text(quest, encoding="utf-8")
            archive_path = root / "resourcepacks" / "Beta43_preview.zip"
            archive_path.parent.mkdir()
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("assets/example/guide/ru_ru/start.md", target)
            before = quest_path.read_bytes()

            items, selected = discover_preview_items(directory)
            report = build_preview_from_directory(directory)

            self.assertEqual(selected, str(archive_path))
            self.assertTrue(any(item.kind == "quest" for item in items))
            self.assertTrue(any(item.kind == "book" for item in items) is False)
            self.assertEqual(quest_path.read_bytes(), before)
            self.assertEqual(report.output_path, str(archive_path))

    def test_directory_preview_pairs_loose_json_catalog_with_quest_archive(self):
        quest = '{quests: [{id: "AAAABBBBCCCCDDDD", title: "{atm9.quest.first}"}]}\n'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            quest_path = root / "config" / "ftbquests" / "quests" / "chapters" / "chapter.snbt"
            quest_path.parent.mkdir(parents=True)
            quest_path.write_text(quest, encoding="utf-8")
            lang_path = root / "kubejs" / "assets" / "kubejs" / "lang" / "en_us.json"
            lang_path.parent.mkdir(parents=True)
            lang_path.write_text('{"atm9.quest.first":"First Quest"}', encoding="utf-8")
            archive_path = root / "resourcepacks" / "Beta43_preview.zip"
            archive_path.parent.mkdir()
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(
                    "assets/kubejs/lang/ru_ru.json",
                    '{"atm9.quest.first":"Первый квест"}',
                )

            report = build_preview_from_directory(directory)

        quest_documents = [document for document in report.documents if document.kind == "quest"]
        self.assertEqual(len(quest_documents), 1)
        self.assertEqual(quest_documents[0].graph_nodes[0].title, "Первый квест")


if __name__ == "__main__":
    unittest.main()
