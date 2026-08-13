import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from mineai.analysis_items import (
    AnalysisItem,
    analysis_segment_key,
    selected_segments_for_target,
    target_is_selected,
)
from mineai.engines.base import EngineCallbacks
from mineai.processors.analyzer import ModpackAnalyzer
from mineai.processors.quest_groups import collect_quest_groups
from mineai.processors.snbt_extract import (
    extract_snbt_strings_by_entry as real_extract_snbt_strings_by_entry,
)
from mineai.processors.snbt import SnbtProcessor
from mineai.processors.estimator import StringEstimator
from mineai.processors.snbt_extract import (
    apply_snbt_translations,
    extract_snbt_strings,
)
from mineai.runtime.state import JobState

try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication
except ImportError:
    Qt = None
    QApplication = None

if QApplication is not None:
    from mineai.gui_qt.dialogs import AnalysisSelectionDialog
else:
    AnalysisSelectionDialog = None


CHAPTER_ID = "1111111111111111"
QUEST_A_ID = "AAAAAAAAAAAAAAAA"
QUEST_B_ID = "BBBBBBBBBBBBBBBB"
OTHER_ID = "CCCCCCCCCCCCCCCC"


def _write_ftb_fixture(root: Path) -> Path:
    chapters = root / "config" / "ftbquests" / "quests" / "chapters"
    language = root / "config" / "ftbquests" / "quests" / "lang"
    chapters.mkdir(parents=True)
    language.mkdir(parents=True)
    (chapters / "technology.snbt").write_text(
        "{\n"
        f'\tid: "{CHAPTER_ID}"\n'
        "\tquests: [\n"
        "\t\t{\n"
        f'\t\t\tid: "{QUEST_A_ID}"\n'
        "\t\t}\n"
        "\t\t{\n"
        f'\t\t\tid: "{QUEST_B_ID}"\n'
        "\t\t}\n"
        "\t]\n"
        "}\n",
        encoding="utf-8-sig",
    )
    source = language / "en_us.snbt"
    source.write_text(
        "{\n"
        f'\tchapter.{CHAPTER_ID}.title: "Technology"\n'
        f'\tquest.{QUEST_A_ID}.title: "Shared text"\n'
        f'\tquest.{QUEST_B_ID}.quest_desc: ["First line" "Second line"]\n'
        f'\tquest.{OTHER_ID}.title: "Shared text"\n'
        f'\tquest.{OTHER_ID}.quest_subtitle: "Only other"\n'
        "}\n",
        encoding="utf-8-sig",
    )
    return source


class QuestGroupTests(unittest.TestCase):
    def test_reward_table_display_fields_are_translatable_but_ids_are_not(self) -> None:
        content = (
            '{\n'
            '\tid: "04E2ADE3851064D1"\n'
            '\tloot_crate: {\n'
            '\t\titem_name: "Box of Magical Goods"\n'
            '\t\tstring_id: "magic_bundle"\n'
            '\t}\n'
            '\trewards: [{\n'
            '\t\titem: { components: { spell: {\n'
            '\t\t\tflavor_text: "A useful spell"\n'
            '\t\t\tname: "Emergency Tunnel"\n'
            '\t\t} } }\n'
            '\t}]\n'
            '}\n'
        )

        strings = extract_snbt_strings(content)

        self.assertEqual(
            strings,
            ["Box of Magical Goods", "A useful spell", "Emergency Tunnel"],
        )
        self.assertNotIn("04E2ADE3851064D1", strings)
        self.assertNotIn("magic_bundle", strings)

        translated = apply_snbt_translations(
            content,
            {
                "Box of Magical Goods": "Коробка магических товаров",
                "A useful spell": "Полезное заклинание",
                "Emergency Tunnel": "Аварийный туннель",
            },
        )
        self.assertIn('item_name: "Коробка магических товаров"', translated)
        self.assertIn('flavor_text: "Полезное заклинание"', translated)
        self.assertIn('name: "Аварийный туннель"', translated)
        self.assertIn('string_id: "magic_bundle"', translated)

    def test_ftb_locale_is_grouped_by_real_chapter_membership(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = _write_ftb_fixture(Path(temp_dir))
            groups = collect_quest_groups(str(source), source.read_text(encoding="utf-8-sig"))

        self.assertEqual([group.name for group in groups], ["Technology", "Other entries"])
        self.assertEqual(
            groups[0].entry_ids,
            frozenset({CHAPTER_ID, QUEST_A_ID, QUEST_B_ID}),
        )
        self.assertEqual(groups[0].total, 4)
        self.assertEqual(groups[1].entry_ids, frozenset({OTHER_ID}))

    def test_group_index_does_not_rescan_the_whole_file_for_every_entry_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = _write_ftb_fixture(Path(temp_dir))
            with mock.patch(
                "mineai.processors.quest_groups.extract_snbt_strings_by_entry",
                wraps=real_extract_snbt_strings_by_entry,
            ) as extractor:
                collect_quest_groups(
                    str(source),
                    source.read_text(encoding="utf-8-sig"),
                )

        extractor.assert_called_once()

    def test_entry_id_belongs_to_only_one_selectable_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = _write_ftb_fixture(root)
            duplicate = source.parent.parent / "chapters" / "duplicate.snbt"
            duplicate.write_text(
                "{\n"
                "\tquests: [\n"
                "\t\t{\n"
                f'\t\t\tid: "{QUEST_A_ID}"\n'
                "\t\t}\n"
                "\t]\n"
                "}\n",
                encoding="utf-8-sig",
            )
            groups = collect_quest_groups(
                str(source),
                source.read_text(encoding="utf-8-sig"),
            )

        memberships = [
            group.group_id
            for group in groups
            if QUEST_A_ID in group.entry_ids
        ]
        self.assertEqual(len(memberships), 1)

    def test_snbt_translation_only_changes_selected_entry_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = _write_ftb_fixture(Path(temp_dir))
            content = source.read_text(encoding="utf-8-sig")

        selected = extract_snbt_strings(content, allowed_entry_ids={QUEST_A_ID})
        self.assertEqual(selected, ["Shared text"])

        translated = apply_snbt_translations(
            content,
            {"Shared text": "Выбранный текст"},
            allowed_entry_ids={QUEST_A_ID},
        )
        self.assertIn(f'quest.{QUEST_A_ID}.title: "Выбранный текст"', translated)
        self.assertIn(f'quest.{OTHER_ID}.title: "Shared text"', translated)

    def test_segment_selection_keeps_file_and_group_identity(self) -> None:
        path = "C:/modpack/config/ftbquests/quests/lang/en_us.snbt"
        first = analysis_segment_key(path, "quests", "chapter:first")
        second = analysis_segment_key(path, "quests", "chapter:second")
        selected = frozenset({first})

        self.assertTrue(target_is_selected(selected, path, "quests"))
        self.assertEqual(
            selected_segments_for_target(selected, path, "quests"),
            frozenset({"chapter:first"}),
        )
        self.assertNotIn(second, selected)

    def test_analyzer_emits_file_parent_and_selectable_quest_groups(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = _write_ftb_fixture(Path(temp_dir))
            emitted = []
            analyzer = ModpackAnalyzer(JobState(is_running=True))
            analyzer._analyze_snbt(
                str(source),
                r"[А-Яа-яЁё]",
                lambda *_args: None,
                emitted.append,
            )

        self.assertTrue(emitted[0].is_group)
        self.assertEqual(len(emitted), 3)
        self.assertEqual(
            {item.parent_key for item in emitted[1:]},
            {emitted[0].key},
        )
        self.assertEqual(
            {item.name for item in emitted[1:]},
            {"Technology", "Other entries"},
        )

    def test_processor_translates_only_the_selected_chapter(self) -> None:
        class _Service:
            def translate_dict(self, values, *_args, **_kwargs):
                return {key: f"RU:{value}" for key, value in values.items()}

        with tempfile.TemporaryDirectory() as temp_dir:
            source = _write_ftb_fixture(Path(temp_dir))
            callbacks = EngineCallbacks(
                should_run=lambda: True,
                wait_if_paused=lambda: None,
                on_log=lambda *_args: None,
                on_status=lambda *_args: None,
                on_progress=lambda *_args: None,
                on_metric=lambda *_args: None,
            )
            selected = frozenset(
                {
                    analysis_segment_key(
                        str(source),
                        "quests",
                        f"chapter:{CHAPTER_ID}",
                    )
                }
            )
            written_path = SnbtProcessor(
                _Service(),
                JobState(is_running=True),
                callbacks,
            ).process(
                str(source),
                target_lang={"file": "ru_ru", "regex": r"[А-Яа-яЁё]"},
                mode="force",
                selected_items=selected,
            )
            translated = source.with_name("ru_ru.snbt").read_text(encoding="utf-8-sig")

        self.assertEqual(written_path, str(source.with_name("ru_ru.snbt")))
        self.assertIn(f'quest.{QUEST_A_ID}.title: "RU:Shared text"', translated)
        self.assertIn(f'quest.{OTHER_ID}.title: "Shared text"', translated)

    def test_append_updates_selected_chapter_in_existing_target_only(self) -> None:
        class _Service:
            def translate_dict(self, values, *_args, **_kwargs):
                return {key: f"RU:{value}" for key, value in values.items()}

        with tempfile.TemporaryDirectory() as temp_dir:
            source = _write_ftb_fixture(Path(temp_dir))
            target = source.with_name("ru_ru.snbt")
            target.write_text(
                source.read_text(encoding="utf-8-sig").replace(
                    f'quest.{OTHER_ID}.title: "Shared text"',
                    f'quest.{OTHER_ID}.title: "Сохранено"',
                ),
                encoding="utf-8-sig",
            )
            callbacks = EngineCallbacks(
                should_run=lambda: True,
                wait_if_paused=lambda: None,
                on_log=lambda *_args: None,
                on_status=lambda *_args: None,
                on_progress=lambda *_args: None,
                on_metric=lambda *_args: None,
            )
            selected = frozenset(
                {
                    analysis_segment_key(
                        str(source),
                        "quests",
                        f"chapter:{CHAPTER_ID}",
                    )
                }
            )
            SnbtProcessor(
                _Service(),
                JobState(is_running=True),
                callbacks,
            ).process(
                str(source),
                target_lang={"file": "ru_ru", "regex": r"[А-Яа-яЁё]"},
                mode="append",
                selected_items=selected,
            )
            translated = target.read_text(encoding="utf-8-sig")

        self.assertIn(f'quest.{QUEST_A_ID}.title: "RU:Shared text"', translated)
        self.assertIn(f'quest.{OTHER_ID}.title: "Сохранено"', translated)

    def test_estimator_counts_only_selected_quest_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = _write_ftb_fixture(Path(temp_dir))
            selected = frozenset(
                {
                    analysis_segment_key(
                        str(source),
                        "quests",
                        f"chapter:{CHAPTER_ID}",
                    )
                }
            )
            total = StringEstimator(JobState(is_running=True)).estimate(
                [],
                [],
                [str(source)],
                [],
                target_lang={"file": "ru_ru", "regex": r"[А-Яа-яЁё]"},
                mode="force",
                translate_mods=False,
                translate_books=False,
                translate_quests=True,
                smart_glue=False,
                selected_items=selected,
            )

        self.assertEqual(total, 4)


@unittest.skipIf(QApplication is None, "PyQt6 is not installed")
class AnalysisSelectionDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_dialog_returns_checked_leaf_keys_and_parent_is_not_a_target(self) -> None:
        parent = AnalysisItem(
            key="quests:file",
            path="C:/file.snbt",
            scope="quests",
            icon="📜",
            name="en_us.snbt",
            kind="FTB Quests",
            translated=0,
            total=20,
            percent=0,
            is_group=True,
        )
        first = AnalysisItem(
            key="quests:file|segment|chapter:first",
            path=parent.path,
            scope="quests",
            icon="📘",
            name="First chapter",
            kind="Chapter",
            translated=0,
            total=10,
            percent=0,
            parent_key=parent.key,
        )
        second = AnalysisItem(
            key="quests:file|segment|chapter:second",
            path=parent.path,
            scope="quests",
            icon="📘",
            name="Second chapter",
            kind="Chapter",
            translated=0,
            total=10,
            percent=0,
            parent_key=parent.key,
        )
        dialog = AnalysisSelectionDialog(
            [parent, first, second],
            {first.key, second.key},
        )
        try:
            root = dialog.tree.topLevelItem(0)
            self.assertEqual(root.childCount(), 2)
            root.child(1).setCheckState(0, Qt.CheckState.Unchecked)
            self.assertEqual(dialog.selected_keys(), frozenset({first.key}))
            self.assertNotIn(parent.key, dialog.selected_keys())
        finally:
            dialog.close()


if __name__ == "__main__":
    unittest.main()
