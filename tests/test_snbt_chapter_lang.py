"""Beta41 regression tests for lossless FTB Quests chapter localization."""

import tempfile
import unittest
from pathlib import Path

from mineai.engines.base import EngineCallbacks
from mineai_formatkit.ftb_quests import FtbQuestsChapterAdapter
from mineai.processors.snbt import SnbtProcessor
from mineai.processors.snbt_chapter_lang import (
    _parse_lang_snbt,
    dump_lang_snbt,
    extract_chapter_lang_entries,
    is_chapter_or_reward_snbt,
    is_lang_snbt,
    merge_and_write_lang_snbt,
)
from mineai.runtime.state import JobState


class TestPathDetection(unittest.TestCase):
    def test_chapter_file(self):
        self.assertTrue(is_chapter_or_reward_snbt(r"C:\mc\config\ftbquests\quests\chapters\getting_started.snbt"))

    def test_reward_table_file(self):
        self.assertTrue(is_chapter_or_reward_snbt("config/ftbquests/quests/reward_tables/default.snbt"))

    def test_data_snbt(self):
        self.assertTrue(is_chapter_or_reward_snbt("config/ftbquests/quests/data.snbt"))

    def test_lang_catalog_excluded(self):
        self.assertFalse(is_chapter_or_reward_snbt("config/ftbquests/quests/lang/en_us.snbt"))

    def test_non_quest_snbt(self):
        self.assertFalse(is_chapter_or_reward_snbt("config/something_else.snbt"))

    def test_lang_folder(self):
        self.assertTrue(is_lang_snbt("config/ftbquests/quests/lang/ru_ru.snbt"))

    def test_lang_subfolder(self):
        self.assertTrue(is_lang_snbt("config/ftbquests/quests/lang/en_us/chapter.snbt"))

    def test_chapter_not_lang(self):
        self.assertFalse(is_lang_snbt("config/ftbquests/quests/chapters/ch.snbt"))


CHAPTER_FIXTURE = """{
\tid: \"1A2B3C4D5E6F7890\"
\ttitle: \"Getting Started\"
\tdescription: [\"First step\", \"Second step\"]
\tsubtitle: \"{atm9.some.key}\"
}
"""

CHAPTER_ATM9 = """{
\tid: \"AAAAAAAAAAAAAAAA\"
\ttitle: \"{atm9.quest.title}\"
\tdescription: [\"Literal hint here\"]
}
"""

MULTI_ENTRY_CHAPTER = """{
\tid: \"1111111111111111\"
\ttitle: \"Quest One\"
\tdesc: [\"Step one\"]
\tid: \"2222222222222222\"
\ttitle: \"Quest Two\"
\tquest_desc: [\"Step two A\", \"Step two B\"]
}
"""


class TestEntryExtraction(unittest.TestCase):
    def test_title_extracted(self):
        entries = extract_chapter_lang_entries(CHAPTER_FIXTURE)
        self.assertEqual(entries["1A2B3C4D5E6F7890.title"], "Getting Started")

    def test_description_extracted_as_list(self):
        entries = extract_chapter_lang_entries(CHAPTER_FIXTURE)
        self.assertEqual(entries["1A2B3C4D5E6F7890.quest_desc"], ["First step", "Second step"])

    def test_translation_key_subtitle_excluded(self):
        self.assertFalse(any("subtitle" in key for key in extract_chapter_lang_entries(CHAPTER_FIXTURE)))

    def test_atm9_chapter_only_literal_extracted(self):
        entries = extract_chapter_lang_entries(CHAPTER_ATM9)
        self.assertNotIn("AAAAAAAAAAAAAAAA.title", entries)
        self.assertEqual(entries["AAAAAAAAAAAAAAAA.quest_desc"], ["Literal hint here"])

    def test_multiple_quests_in_chapter(self):
        entries = extract_chapter_lang_entries(MULTI_ENTRY_CHAPTER)
        self.assertEqual(entries["1111111111111111.title"], "Quest One")
        self.assertEqual(entries["2222222222222222.title"], "Quest Two")
        self.assertEqual(entries["2222222222222222.quest_desc"], ["Step two A", "Step two B"])

    def test_empty_and_orphan_text_are_ignored(self):
        self.assertEqual(extract_chapter_lang_entries("{}"), {})
        self.assertEqual(extract_chapter_lang_entries('{\n\ttitle: "Orphan title"\n}'), {})

    def test_technical_terms_excluded(self):
        content = '{\n\tid: "ABCDEF1234567890"\n\ttitle: "mod:some_id"\n}'
        self.assertEqual(extract_chapter_lang_entries(content), {})

    def test_gameplay_references_are_not_translation_units(self):
        """IDs, item requirements, dependencies and rewards stay untouched."""
        content = """{
\tid: \"AAAABBBBCCCCDDDD\"
\ttitle: \"Collect a diamond\"
\ttasks: [{id: \"minecraft:diamond\", count: 1}]
\tdependencies: [\"1111222233334444\"]
\trewards: [{item: {id: \"minecraft:diamond\", count: 1}}]
}
"""
        entries = extract_chapter_lang_entries(content)
        self.assertEqual(set(entries), {"AAAABBBBCCCCDDDD.title"})
        self.assertNotIn("minecraft:diamond", " ".join(entries))


class TestLangSerialization(unittest.TestCase):
    def test_roundtrip_string(self):
        data = {"ABCD.title": "Hello World"}
        self.assertEqual(_parse_lang_snbt(dump_lang_snbt(data)), data)

    def test_roundtrip_list(self):
        data = {"ABCD.quest_desc": ["Line one", "Line two"]}
        self.assertEqual(_parse_lang_snbt(dump_lang_snbt(data)), data)

    def test_roundtrip_unicode_and_escapes(self):
        data = {"FF00.title": 'Quête avec "guillemets"'}
        self.assertEqual(_parse_lang_snbt(dump_lang_snbt(data)), data)

    def test_empty_dict(self):
        self.assertEqual(dump_lang_snbt({}), "{\n}\n")

    def test_creates_file_if_missing_and_preserves_existing(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "ru_ru.snbt"
            merge_and_write_lang_snbt(str(target), {"A.title": "Hello"})
            self.assertEqual(_parse_lang_snbt(target.read_text(encoding="utf-8")), {"A.title": "Hello"})
            merge_and_write_lang_snbt(str(target), {"A.title": "New", "B.title": "New"})
            loaded = _parse_lang_snbt(target.read_text(encoding="utf-8"))
            self.assertEqual(loaded["A.title"], "Hello")
            self.assertEqual(loaded["B.title"], "New")

    def test_overwrite_existing_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "ru_ru.snbt"
            target.write_text('{\n\t"A.title": "Old"\n}\n', encoding="utf-8")
            merge_and_write_lang_snbt(str(target), {"A.title": "New"}, overwrite_existing=True)
            self.assertEqual(_parse_lang_snbt(target.read_text(encoding="utf-8"))["A.title"], "New")


class _FakeService:
    def translate_dict(self, values, target_lang, callbacks, *, context, prompt_type):
        return {key: f"RU:{value}" for key, value in values.items()}


class TestGameplaySafeChapterProcessing(unittest.TestCase):
    def test_formatkit_accepts_description_lists_and_mojang_snbt_escapes(self):
        source = """{
\tdescription: [\"First \\ \\ second\", \"\"]
\ttasks: [{id: \"minecraft:diamond\", count: 1}]
}
"""
        adapter = FtbQuestsChapterAdapter()
        plan = adapter.prepare("config/ftbquests/quests/chapters/chapter.snbt", source)
        self.assertEqual(len(plan.units), 1)
        self.assertEqual(adapter.apply(plan, {plan.units[0].id: plan.units[0].text}), source)

    def test_formatkit_adapter_covers_reward_tables(self):
        self.assertTrue(
            FtbQuestsChapterAdapter().matches(
                "config/ftbquests/quests/reward_tables/rewards.snbt"
            )
        )

    def test_formatkit_chapter_adapter_preserves_requirement_and_reward_ids(self):
        source = """{
\tid: \"AAAABBBBCCCCDDDD\"
\tfeedback_message: \"Collect a diamond\"
\ttasks: [{id: \"minecraft:diamond\", count: 1}]
\tdependencies: [\"1111222233334444\"]
\trewards: [{item: {id: \"minecraft:diamond\", count: 1}}]
}
"""
        path = "config/ftbquests/quests/chapters/chapter.snbt"
        adapter = FtbQuestsChapterAdapter()
        plan = adapter.prepare(path, source)
        self.assertEqual(len(plan.units), 1)
        output = adapter.apply(plan, {plan.units[0].id: "Соберите алмаз"})
        self.assertIn('id: "minecraft:diamond"', output)
        self.assertIn('dependencies: ["1111222233334444"]', output)
        self.assertIn('count: 1', output)
        self.assertEqual(adapter.fingerprint(source), adapter.fingerprint(output))

    def test_source_and_gameplay_links_remain_identical(self):
        """Chapter processing writes only lang overlay, never gameplay SNBT."""
        source = """{
\tid: \"AAAABBBBCCCCDDDD\"
\ttitle: \"Collect a diamond\"
\ttasks: [{id: \"minecraft:diamond\", count: 1}]
\tdependencies: [\"1111222233334444\"]
\trewards: [{item: {id: \"minecraft:diamond\", count: 1}}]
}
"""
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
            processor = SnbtProcessor(_FakeService(), JobState(is_running=True), callbacks)
            processor.process(
                str(chapter),
                target_lang={"file": "ru_ru", "regex": "[А-Яа-я]"},
                mode="force",
            )
            output = processor.flush_accumulated_lang(str(quests_dir), "ru_ru")
            self.assertIsNotNone(output)
            self.assertEqual(chapter.read_text(encoding="utf-8"), source)
            translated = _parse_lang_snbt(Path(output).read_text(encoding="utf-8"))
            self.assertEqual(translated, {"AAAABBBBCCCCDDDD.title": "RU:Collect a diamond"})
            self.assertNotIn("minecraft:diamond", Path(output).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
