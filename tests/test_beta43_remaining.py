"""Regression tests for the remaining Beta43 quest-audit failures."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mineai.engines.base import EngineCallbacks
from mineai.engines.service import _validate_candidate
from mineai.language_validation import (
    delimiter_counts_need_repair,
    has_untranslated_source_words,
    translation_needs_repair,
)
from mineai.processors.selection import collect_snbt_selection
from mineai.processors.snbt import SnbtProcessor
from mineai.processors.snbt_chapter_lang import (
    _parse_lang_snbt,
    extract_chapter_lang_entries,
    merge_and_write_lang_snbt,
)
from mineai.runtime.state import JobState
from mineai.engines.base import EngineItem
from mineai.preview import PreviewBuilder, PreviewInput
from mineai.text_processing import mask_protected_fragments


RU = {"api": "ru", "file": "ru_ru", "regex": r"[А-Яа-яЁё]"}


class RemainingBeta43Tests(unittest.TestCase):
    def test_format_code_boundary_is_part_of_translation_validation(self) -> None:
        self.assertTrue(
            translation_needs_repair(
                "&6Extras",
                "&6 Дополнительно",
                RU,
            )
        )

    def test_existing_cache_with_changed_number_or_code_is_repaired(self) -> None:
        self.assertTrue(
            translation_needs_repair(
                "Use &6Energy&r: 256 E/t",
                "Используйте &6Энергию&r: 512 E/t",
                RU,
            )
        )

    def test_residual_source_word_is_repairable_but_mod_name_is_not(self) -> None:
        self.assertTrue(
            has_untranslated_source_words(
                "A Blaze Burner is useful",
                "Горелка Blaze полезна",
                RU,
            )
        )
        self.assertFalse(
            has_untranslated_source_words(
                "A Farmer’s Delight sandwich",
                "Сэндвич Farmer’s Delight",
                RU,
            )
        )
        self.assertFalse(
            translation_needs_repair(
                "&6 Extras",
                "&6 Дополнительно",
                RU,
            )
        )

    def test_capitalized_product_names_are_not_rejected_as_residual_prose(self) -> None:
        cases = (
            (
                "Found an issue? Missing a feature? Please report it here: Advanced AE GitHub",
                "Нашли проблему? Не хватает функции? Пожалуйста, сообщите об этом здесь: Advanced AE GitHub",
            ),
            (
                "Induction Cards (Extra Mod Required: Applied Flux)",
                "Индукционные карты (требуется мод Applied Flux)",
            ),
            (
                "AdvancedAE provider items enable upgrades",
                "AdvancedAE предоставляет предметы для улучшений",
            ),
        )
        for source, candidate in cases:
            with self.subTest(source=source):
                self.assertFalse(has_untranslated_source_words(source, candidate, RU))

    def test_clear_lowercase_source_word_is_still_rejected(self) -> None:
        self.assertTrue(
            has_untranslated_source_words(
                "This machine can create items",
                "Эта машина может create предметы",
                RU,
            )
        )

    def test_common_ui_action_word_is_still_rejected(self) -> None:
        self.assertTrue(
            has_untranslated_source_words(
                "Press enter to apply the input",
                "Нажмите enter, чтобы применить значение",
                RU,
            )
        )

    def test_lowercase_create_hidden_by_legacy_mask_is_still_rejected(self) -> None:
        source = "This machine can create items"
        masked, mapping = mask_protected_fragments(source)
        item = EngineItem(
            key="title",
            original=source,
            masked=masked,
            mapping=mapping,
        )
        accepted, reason, _identity = _validate_candidate(
            item,
            "Эта машина может create предметы",
            RU,
        )
        self.assertFalse(accepted)
        self.assertIn("английские слова", reason or "")

    def test_create_units_are_not_reported_as_untranslated_words(self) -> None:
        self.assertFalse(
            has_untranslated_source_words(
                "Lets you use &6SU&r with an HW capacitor in a WW1 test",
                "Позволяет использовать &6SU&r с конденсатором HW в тесте WW1",
                RU,
            )
        )

    def test_candidate_with_inserted_space_after_color_code_is_rejected(self) -> None:
        item = EngineItem(
            key="title",
            original="&6Extras",
            masked="[#0#]Extras",
            mapping={"[#0#]": "&6"},
        )
        accepted, reason, _identity = _validate_candidate(
            item,
            "&6 Дополнительно",
            RU,
        )
        self.assertFalse(accepted)
        self.assertIn("пробел", reason or "")

    def test_candidate_with_extra_closing_list_delimiter_is_rejected(self) -> None:
        source = "Craft a &6Mechanical Press&r"
        candidate = "Создайте &6Механический пресс&r]"
        self.assertTrue(delimiter_counts_need_repair(source, candidate))
        item = EngineItem(
            key="title",
            original=source,
            masked="Craft a [#0#]Mechanical Press[#1#]",
            mapping={"[#0#]": "&6", "[#1#]": "&r"},
        )
        accepted, reason, _identity = _validate_candidate(item, candidate, RU)
        self.assertFalse(accepted)
        self.assertIn("разделител", reason or "")

    def test_candidate_with_balanced_parenthetical_prose_is_accepted(self) -> None:
        item = EngineItem(
            key="title",
            original="Full Block Pattern Provider",
            masked="Full Block Pattern Provider",
            mapping={},
        )
        accepted, reason, _identity = _validate_candidate(
            item,
            "Поставщик узоров (полный блок)",
            RU,
        )
        self.assertTrue(accepted, reason)

    def test_snbt_selection_retranslates_existing_bad_value(self) -> None:
        source = '{title:"&6Extras"}'
        current = '{title:"&6 Дополнительно"}'
        selection = collect_snbt_selection(
            source,
            current,
            "append",
            RU["regex"],
            target_lang=RU,
        )
        self.assertEqual(selection.pending, ["&6Extras"])
        self.assertEqual(selection.repair_pending, ("&6Extras",))

    def test_skip_selection_keeps_structural_repairs_pending_after_ninety_percent(self) -> None:
        labels = (
            "Zero", "One", "Two", "Three", "Four",
            "Five", "Six", "Seven", "Eight", "Nine",
        )
        source = "{\n" + "\n".join(
            f'\tquest.{i:016X}.title: "&6Entry{labels[i]}"' for i in range(10)
        ) + "\n}\n"
        current = "{\n" + "\n".join(
            f'\tquest.{i:016X}.title: "&6 Entry{labels[i]}"' if i == 9
            else f'\tquest.{i:016X}.title: "&6Перевод{labels[i]}"'
            for i in range(10)
        ) + "\n}\n"
        selection = collect_snbt_selection(
            source,
            current,
            "skip",
            RU["regex"],
            target_lang=RU,
        )
        self.assertEqual(len(selection.pending), 1)
        self.assertEqual(selection.repair_pending, ("&6EntryNine",))

    def test_snbt_extraction_skips_image_and_pagebreak_macros(self) -> None:
        source = """{
\tid: "AAAABBBBCCCCDDDD"
\tdescription: ["Visible text", "{image:allofcreate:textures/qb_image1.png width:397 height:140}", "{@pagebreak}"]
}
"""
        entries = extract_chapter_lang_entries(source)
        self.assertEqual(
            entries,
            {
                "AAAABBBBCCCCDDDD.quest_desc": [
                    "Visible text",
                    "{image:allofcreate:textures/qb_image1.png width:397 height:140}",
                    "{@pagebreak}",
                ]
            },
        )

    def test_preview_does_not_report_structure_macros_as_untranslated(self) -> None:
        source = """{
\tid: "AAAABBBBCCCCDDDD"
\tdescription: ["{image:allofcreate:textures/qb_image1.png width:397 height:140}", "{@pagebreak}"]
}
"""
        report = PreviewBuilder(target_regex=RU["regex"]).build(
            [
                PreviewInput(
                    logical_path="config/ftbquests/quests/chapters/chapter.snbt",
                    source_text=source,
                    target_text=source,
                    kind="quest",
                )
            ]
        )
        self.assertEqual(report.untranslated, 0)

    def test_chapter_processor_keeps_valid_existing_translation(self) -> None:
        source = """{
\tid: "AAAABBBBCCCCDDDD"
\ttitle: "&6Extras"
}
"""

        class Service:
            def __init__(self) -> None:
                self.calls = 0

            def translate_dict(self, values, target_lang, callbacks, **kwargs):
                self.calls += 1
                return {key: "&6Дополнительно" for key in values}

        callbacks = EngineCallbacks(
            should_run=lambda: True,
            wait_if_paused=lambda: None,
            on_log=lambda *_args: None,
            on_status=lambda *_args: None,
        )
        with tempfile.TemporaryDirectory() as directory:
            quests_dir = Path(directory) / "config" / "ftbquests" / "quests"
            chapter = quests_dir / "chapters" / "chapter.snbt"
            target = quests_dir / "lang" / "ru_ru.snbt"
            chapter.parent.mkdir(parents=True)
            target.parent.mkdir(parents=True)
            chapter.write_text(source, encoding="utf-8")
            target.write_text(
                '{\n\t"AAAABBBBCCCCDDDD.title": "&6Дополнительно"\n}\n',
                encoding="utf-8",
            )
            service = Service()
            processor = SnbtProcessor(service, JobState(is_running=True), callbacks)
            processor.process(
                str(chapter),
                target_lang=RU,
                mode="append",
            )
            self.assertEqual(service.calls, 0)

    def test_lang_catalog_rewrites_invalid_target_on_source_skeleton(self) -> None:
        source = '{\n\tquest.0000000000000001.title: "&6Extras"\n}\n'

        class Service:
            def __init__(self) -> None:
                self.calls = 0

            def translate_dict(self, values, target_lang, callbacks, **kwargs):
                self.calls += 1
                return {key: "&6Дополнительно" for key in values}

        callbacks = EngineCallbacks(
            should_run=lambda: True,
            wait_if_paused=lambda: None,
            on_log=lambda *_args: None,
            on_status=lambda *_args: None,
        )
        with tempfile.TemporaryDirectory() as directory:
            quests_dir = Path(directory) / "config" / "ftbquests" / "quests"
            lang = quests_dir / "lang"
            lang.mkdir(parents=True)
            (lang / "en_us.snbt").write_text(source, encoding="utf-8")
            (lang / "ru_ru.snbt").write_text(
                '{\n\tquest.0000000000000001.title: "&6 Дополнительно"\n}\n',
                encoding="utf-8",
            )
            service = Service()
            processor = SnbtProcessor(service, JobState(is_running=True), callbacks)
            output = processor.process(
                str(lang / "en_us.snbt"),
                target_lang=RU,
                mode="append",
            )
            self.assertEqual(output, str(lang / "ru_ru.snbt"))
            self.assertEqual(service.calls, 1)
            self.assertEqual(
                (lang / "ru_ru.snbt").read_text(encoding="utf-8").strip(),
                '{\n\tquest.0000000000000001.title: "&6Дополнительно"\n}',
            )

    def test_chapter_processor_keeps_macro_positions_and_does_not_send_them(self) -> None:
        source = """{
\tid: "AAAABBBBCCCCDDDD"
\tdescription: ["Visible text", "{@pagebreak}"]
}
"""

        class Service:
            def __init__(self) -> None:
                self.values = []

            def translate_dict(self, values, target_lang, callbacks, **kwargs):
                self.values.extend(values.values())
                return {key: f"RU:{value}" for key, value in values.items()}

        callbacks = EngineCallbacks(
            should_run=lambda: True,
            wait_if_paused=lambda: None,
            on_log=lambda *_args: None,
            on_status=lambda *_args: None,
        )
        with tempfile.TemporaryDirectory() as directory:
            quests_dir = Path(directory) / "config" / "ftbquests" / "quests"
            chapter = quests_dir / "chapters" / "chapter.snbt"
            chapter.parent.mkdir(parents=True)
            chapter.write_text(source, encoding="utf-8")
            service = Service()
            processor = SnbtProcessor(service, JobState(is_running=True), callbacks)
            processor.process(
                str(chapter),
                target_lang=RU,
                mode="force",
            )
            output = processor.flush_accumulated_lang(str(quests_dir), "ru_ru")
            self.assertEqual(service.values, ["Visible text"])
            self.assertEqual(
                _parse_lang_snbt(Path(output).read_text(encoding="utf-8")),
                {"AAAABBBBCCCCDDDD.quest_desc": ["RU:Visible text", "{@pagebreak}" ]},
            )

    def test_chapter_repair_preserves_other_valid_list_lines(self) -> None:
        source = """{
\tid: "AAAABBBBCCCCDDDD"
\tdescription: ["First text", "Second text"]
}
"""

        class Service:
            def translate_dict(self, values, target_lang, callbacks, **kwargs):
                self.values = list(values.values())
                return {key: f"RU:{value}" for key, value in values.items()}

        callbacks = EngineCallbacks(
            should_run=lambda: True,
            wait_if_paused=lambda: None,
            on_log=lambda *_args: None,
            on_status=lambda *_args: None,
        )
        with tempfile.TemporaryDirectory() as directory:
            quests_dir = Path(directory) / "config" / "ftbquests" / "quests"
            chapter = quests_dir / "chapters" / "chapter.snbt"
            target = quests_dir / "lang" / "ru_ru.snbt"
            chapter.parent.mkdir(parents=True)
            target.parent.mkdir(parents=True)
            chapter.write_text(source, encoding="utf-8")
            target.write_text(
                '{\n\t"AAAABBBBCCCCDDDD.quest_desc": '
                '["Первая строка", "&6 Неверная строка"]\n}\n',
                encoding="utf-8",
            )
            service = Service()
            processor = SnbtProcessor(service, JobState(is_running=True), callbacks)
            processor.process(str(chapter), target_lang=RU, mode="append")
            output = processor.flush_accumulated_lang(str(quests_dir), "ru_ru")
            self.assertEqual(service.values, ["Second text"])
            self.assertEqual(
                _parse_lang_snbt(Path(output).read_text(encoding="utf-8")),
                {
                    "AAAABBBBCCCCDDDD.quest_desc": [
                        "Первая строка",
                        "RU:Second text",
                    ]
                },
            )

    def test_failed_chapter_line_does_not_compact_list_slots(self) -> None:
        source = """{
\tid: "AAAABBBBCCCCDDDD"
\tdescription: ["First text", "{@pagebreak}", "Second text"]
}
"""

        class Service:
            def translate_dict(self, values, target_lang, callbacks, **kwargs):
                # The second visible line fails; its original slot must remain.
                return {"0": "RU:First text"}

        callbacks = EngineCallbacks(
            should_run=lambda: True,
            wait_if_paused=lambda: None,
            on_log=lambda *_args: None,
            on_status=lambda *_args: None,
        )
        with tempfile.TemporaryDirectory() as directory:
            quests_dir = Path(directory) / "config" / "ftbquests" / "quests"
            chapter = quests_dir / "chapters" / "chapter.snbt"
            chapter.parent.mkdir(parents=True)
            chapter.write_text(source, encoding="utf-8")
            processor = SnbtProcessor(
                Service(),
                JobState(is_running=True),
                callbacks,
            )
            processor.process(str(chapter), target_lang=RU, mode="force")
            output = processor.flush_accumulated_lang(str(quests_dir), "ru_ru")
            self.assertEqual(
                _parse_lang_snbt(Path(output).read_text(encoding="utf-8")),
                {
                    "AAAABBBBCCCCDDDD.quest_desc": [
                        "RU:First text",
                        "{@pagebreak}",
                        "Second text",
                    ]
                },
            )

    def test_preview_reports_changed_format_code_spacing_as_structure(self) -> None:
        source = '{\n\tquest.AAAABBBBCCCCDDDD.title: "&6Extras"\n}\n'
        target = '{\n\tquest.AAAABBBBCCCCDDDD.title: "&6 Дополнительно"\n}\n'
        report = PreviewBuilder(target_regex=RU["regex"]).build(
            [
                PreviewInput(
                    logical_path="config/ftbquests/quests/lang/en_us.snbt",
                    source_text=source,
                    target_text=target,
                    kind="quest",
                )
            ]
        )
        self.assertEqual(report.structure_errors, 1)
        self.assertIn("пробелы", report.issues[0].message)

    def test_preview_reports_extra_list_delimiter_as_structure(self) -> None:
        source = '{\n\tquest.AAAABBBBCCCCDDDD.title: "&6Extras"\n}\n'
        target = '{\n\tquest.AAAABBBBCCCCDDDD.title: "&6Дополнительно ]"\n}\n'
        report = PreviewBuilder(target_regex=RU["regex"]).build(
            [
                PreviewInput(
                    logical_path="config/ftbquests/quests/lang/en_us.snbt",
                    source_text=source,
                    target_text=target,
                    kind="quest",
                )
            ]
        )
        self.assertEqual(report.structure_errors, 1)
        self.assertIn("разделителей", report.issues[0].message)

    def test_merge_can_replace_invalid_existing_translation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "ru_ru.snbt"
            target.write_text(
                '{\n\t"AAAABBBBCCCCDDDD.title": "&6 Дополнительно"\n}\n',
                encoding="utf-8",
            )
            merge_and_write_lang_snbt(
                str(target),
                {"AAAABBBBCCCCDDDD.title": "&6Дополнительно"},
                overwrite_existing=True,
            )
            self.assertEqual(
                _parse_lang_snbt(target.read_text(encoding="utf-8"))[
                    "AAAABBBBCCCCDDDD.title"
                ],
                "&6Дополнительно",
            )


if __name__ == "__main__":
    unittest.main()
