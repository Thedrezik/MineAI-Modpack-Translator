from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import zipfile

from formatkit import FormatRegistry
from mineai.engines.base import EngineCallbacks
from mineai.processors.loose_json import LooseJsonProcessor
from mineai.processors.loose_paths import loose_target_disk_path
from mineai.processors.book_paths import MarkdownBookLocator
from mineai.processors.jar import JarProcessor
from mineai.processors.snbt import SnbtProcessor
from mineai.processors.snbt_extract import build_snbt_document
from mineai.runtime.state import JobState


TARGET_LANG = {
    "file": "ru_ru",
    "api": "ru",
    "name": "Russian",
    "regex": r"[А-Яа-яЁё]",
}


class _Service:
    def translate_dict(self, values, *_args, **_kwargs):
        return {key: f"RU:{value}" for key, value in values.items()}


class _Writer:
    def __init__(self):
        self.files: dict[str, bytes] = {}

    def write(self, path: str, payload: bytes) -> None:
        self.files[path] = payload


class PreviewRetranslationTests(unittest.TestCase):
    def test_loose_book_retranslation_changes_only_checked_unit(self):
        source = "# Guide\n\nFirst paragraph.\n\nSecond paragraph.\n"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "assets" / "demo" / "guide" / "en_us" / "start.md"
            source_path.parent.mkdir(parents=True)
            source_path.write_text(source, encoding="utf-8-sig")
            target_path = Path(loose_target_disk_path(str(source_path), "ru_ru"))
            target_path.parent.mkdir(parents=True)
            target_path.write_text(
                "# Руководство\n\nПервый абзац.\n\nSecond paragraph.\n",
                encoding="utf-8-sig",
            )

            plan = FormatRegistry.default().plan(
                "assets/demo/guide/en_us/start.md",
                source,
                "ru_ru",
                target_path_hint="assets/demo/guide/ru_ru/start.md",
            )
            selected = frozenset({plan.units[-1].id})
            state = JobState(is_running=True)
            callbacks = EngineCallbacks(
                should_run=lambda: True,
                wait_if_paused=lambda: None,
                on_log=lambda *_args: None,
                on_status=lambda *_args: None,
                on_progress=lambda *_args: None,
                on_metric=lambda *_args: None,
            )

            LooseJsonProcessor(_Service(), state, callbacks).process(
                str(source_path),
                str(root),
                target_lang=TARGET_LANG,
                mode="append",
                output_mode="inplace",
                pack_writer=None,
                selected_units={
                    "assets/demo/guide/en_us/start.md": selected,
                },
                retranslate_selected=True,
            )

            result = target_path.read_text(encoding="utf-8-sig")
            self.assertIn("Первый абзац.", result)
            self.assertIn("RU:Second paragraph.", result)
            self.assertNotIn("RU:First paragraph.", result)

    def test_jar_book_retranslation_writes_only_checked_unit(self):
        source = "# Guide\n\nFirst paragraph.\n\nSecond paragraph.\n"
        target = "# Руководство\n\nПервый абзац.\n\nSecond paragraph.\n"
        entry = "assets/demo/guide/en_us/start.md"
        target_entry = "assets/demo/guide/ru_ru/start.md"
        with tempfile.TemporaryDirectory() as temp_dir:
            jar_path = Path(temp_dir) / "demo.jar"
            plan = FormatRegistry.default().plan(
                entry,
                source,
                "ru_ru",
                target_path_hint=target_entry,
            )
            target_entry = plan.target_path or target_entry
            with zipfile.ZipFile(jar_path, "w") as archive:
                archive.writestr(entry, source)
                archive.writestr(target_entry, target)
            locator = MarkdownBookLocator([entry, target_entry], "ru_ru")
            selected = frozenset({plan.units[-1].id})
            writer = _Writer()
            state = JobState(is_running=True)
            callbacks = EngineCallbacks(
                should_run=lambda: True,
                wait_if_paused=lambda: None,
                on_log=lambda *_args: None,
                on_status=lambda *_args: None,
                on_progress=lambda *_args: None,
                on_metric=lambda *_args: None,
            )

            JarProcessor(_Service(), state, callbacks).process(
                str(jar_path),
                target_lang=TARGET_LANG,
                mode="append",
                output_mode="resourcepack",
                translate_mods=False,
                translate_books=True,
                pack_writer=writer,
                book_locator=locator,
                selected_units={entry: selected},
                retranslate_selected=True,
            )

            self.assertTrue(writer.files)
            result = next(iter(writer.files.values())).decode("utf-8")
            self.assertIn("Первый абзац.", result)
            self.assertIn("RU:Second paragraph.", result)
            self.assertNotIn("RU:First paragraph.", result)

    def test_quest_retranslation_changes_only_checked_text_node(self):
        source = (
            '{\n'
            'quest.AAAAAAAAAAAAAAAA.title: "First quest"\n'
            'quest.BBBBBBBBBBBBBBBB.title: "Second quest"\n'
            '}\n'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "en_us.snbt"
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_text(source, encoding="utf-8-sig")
            target_path = source_path.with_name("ru_ru.snbt")
            target_path.write_text(
                source.replace("First quest", "Первый квест"),
                encoding="utf-8-sig",
            )
            nodes = build_snbt_document(source).nodes
            selected = frozenset({nodes[-1].key})
            state = JobState(is_running=True)
            callbacks = EngineCallbacks(
                should_run=lambda: True,
                wait_if_paused=lambda: None,
                on_log=lambda *_args: None,
                on_status=lambda *_args: None,
                on_progress=lambda *_args: None,
                on_metric=lambda *_args: None,
            )

            SnbtProcessor(_Service(), state, callbacks).process(
                str(source_path),
                target_lang=TARGET_LANG,
                mode="append",
                selected_units={str(source_path): selected},
                retranslate_selected=True,
            )

            result = target_path.read_text(encoding="utf-8-sig")
            self.assertIn('"Первый квест"', result)
            self.assertIn('"RU:Second quest"', result)
            self.assertNotIn('"RU:First quest"', result)

    def test_quest_chapter_retranslation_scopes_selected_entry(self):
        source = (
            '{\n'
            '\tquests: [{\n'
            '\t\tid: "AAAAAAAAAAAAAAAA"\n'
            '\t\ttitle: "First quest"\n'
            '\t}, {\n'
            '\t\tid: "BBBBBBBBBBBBBBBB"\n'
            '\t\ttitle: "Second quest"\n'
            '\t}]\n'
            '}\n'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            quests_dir = Path(temp_dir) / "config" / "ftbquests" / "quests"
            chapter = quests_dir / "chapters" / "chapter.snbt"
            chapter.parent.mkdir(parents=True)
            chapter.write_text(source, encoding="utf-8-sig")
            nodes = build_snbt_document(source).nodes
            selected = frozenset(
                node.key for node in nodes if node.metadata.get("entry_id") == "BBBBBBBBBBBBBBBB"
            )
            self.assertTrue(selected)
            callbacks = EngineCallbacks(
                should_run=lambda: True,
                wait_if_paused=lambda: None,
                on_log=lambda *_args: None,
                on_status=lambda *_args: None,
                on_progress=lambda *_args: None,
                on_metric=lambda *_args: None,
            )

            processor = SnbtProcessor(_Service(), JobState(is_running=True), callbacks)
            processor.process(
                str(chapter),
                target_lang=TARGET_LANG,
                mode="append",
                selected_units={str(chapter): selected},
                retranslate_selected=True,
            )
            processor.flush_accumulated_lang(str(quests_dir), "ru_ru")
            result = (quests_dir / "lang" / "ru_ru.snbt").read_text(encoding="utf-8-sig")
            self.assertNotIn("AAAAAAAAAAAAAAAA", result)
            self.assertIn("BBBBBBBBBBBBBBBB", result)
            self.assertIn("RU:Second quest", result)


if __name__ == "__main__":
    unittest.main()
