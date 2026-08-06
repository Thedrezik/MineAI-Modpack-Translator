import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from mineai.engines.base import EngineCallbacks
from mineai.processors.bq_json import BQProcessor
from mineai.processors.estimator import StringEstimator
from mineai.processors.jar import JarProcessor
from mineai.processors.locale_keys import (
    collect_lang_keys_to_translate,
    count_translatable_lang_entries,
)
from mineai.processors.loose_json import LooseJsonProcessor
from mineai.processors.snbt import SnbtProcessor
from mineai.runtime.state import JobState


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


class _Service:
    def __init__(self, smart_glue: bool = False) -> None:
        self.config = _Config(smart_glue)
        self.calls: list[dict[str, str]] = []

    def translate_dict(
        self,
        strings,
        _target_lang,
        _callbacks,
        **_kwargs,
    ):
        self.calls.append(dict(strings))
        return {key: f"Перевод: {value}" for key, value in strings.items()}


class _Writer:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def write(self, path: str, payload: bytes) -> None:
        self.files[path] = payload


def _callbacks(logs: list[str] | None = None) -> EngineCallbacks:
    logs = logs if logs is not None else []
    return EngineCallbacks(
        should_run=lambda: True,
        wait_if_paused=lambda: None,
        on_log=lambda message, _tag: logs.append(message),
        on_status=lambda _message: None,
        on_progress=lambda _count: None,
    )


def _state() -> JobState:
    state = JobState()
    state.start()
    return state


class LocaleContractTests(unittest.TestCase):
    def test_collect_lang_keys_obeys_force_append_and_skip(self) -> None:
        source = {
            "new": "New entry",
            "same": "Same entry",
            "done": "Completed entry",
            "empty": "",
            "technical": "create_machine",
            "target": "Готово",
        }
        target = {
            "same": "Same entry",
            "done": "Готовая запись",
        }

        force = collect_lang_keys_to_translate(
            source,
            target,
            "force",
            TARGET_LANG["regex"],
        )
        append = collect_lang_keys_to_translate(
            source,
            target,
            "append",
            TARGET_LANG["regex"],
        )
        skip = collect_lang_keys_to_translate(
            source,
            target,
            "skip",
            TARGET_LANG["regex"],
        )

        self.assertEqual(set(force), {"new", "same", "done"})
        self.assertEqual(set(append), {"new", "same"})
        self.assertEqual(skip, append)
        self.assertEqual(count_translatable_lang_entries(source), 3)

    def test_loose_json_estimator_matches_processor_for_all_modes(self) -> None:
        cases = {
            "force": 3,
            "append": 2,
            "skip": 2,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "en_us.json"
            target_path = root / "ru_ru.json"
            source_path.write_text(
                json.dumps(
                    {
                        "new": "New entry",
                        "same": "Same entry",
                        "done": "Completed entry",
                    }
                ),
                encoding="utf-8",
            )
            target_path.write_text(
                json.dumps(
                    {
                        "same": "Same entry",
                        "done": "Готовая запись",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            for mode, expected in cases.items():
                with self.subTest(mode=mode):
                    state = _state()
                    estimator = StringEstimator(state)
                    estimated = estimator._estimate_loose(
                        str(source_path),
                        "ru_ru.json",
                        mode,
                        TARGET_LANG["regex"],
                    )

                    service = _Service()
                    processor = LooseJsonProcessor(
                        service,
                        state,
                        _callbacks(),
                    )
                    processor.process(
                        str(source_path),
                        str(root),
                        target_lang=TARGET_LANG,
                        mode=mode,
                        output_mode="inplace",
                        pack_writer=None,
                    )
                    actual = len(service.calls[0]) if service.calls else 0

                    self.assertEqual(estimated, expected)
                    self.assertEqual(actual, expected)
                    # Restore the fixture changed by the previous subtest.
                    target_path.write_text(
                        json.dumps(
                            {
                                "same": "Same entry",
                                "done": "Готовая запись",
                            },
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )

    def test_loose_json_skip_uses_ninety_percent_formula(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = {f"key{i}": f"Source line {i}" for i in range(10)}
            target = {
                f"key{i}": f"Перевод {i}"
                for i in range(9)
            }
            (root / "en_us.json").write_text(
                json.dumps(source),
                encoding="utf-8",
            )
            target_path = root / "ru_ru.json"
            original_target = json.dumps(target, ensure_ascii=False)
            target_path.write_text(original_target, encoding="utf-8")

            state = _state()
            estimator = StringEstimator(state)
            self.assertEqual(
                estimator._estimate_loose(
                    str(root / "en_us.json"),
                    "ru_ru.json",
                    "skip",
                    TARGET_LANG["regex"],
                ),
                0,
            )

            service = _Service()
            LooseJsonProcessor(service, state, _callbacks()).process(
                str(root / "en_us.json"),
                str(root),
                target_lang=TARGET_LANG,
                mode="skip",
                output_mode="inplace",
                pack_writer=None,
            )
            self.assertEqual(service.calls, [])
            self.assertEqual(
                target_path.read_text(encoding="utf-8"),
                original_target,
            )


class BookParityTests(unittest.TestCase):
    @staticmethod
    def _write_jar(path: Path, files: dict[str, str]) -> None:
        with zipfile.ZipFile(path, "w") as archive:
            for internal, content in files.items():
                archive.writestr(internal, content.encode("utf-8"))

    def test_book_json_append_preserves_existing_and_counts_new(self) -> None:
        source_path = "assets/demo/patchouli_books/guide/en_us/entries/a.json"
        target_path = "assets/demo/patchouli_books/guide/ru_ru/entries/a.json"
        source = {
            "name": "Original title",
            "text": "New paragraph",
        }
        target = {
            "name": "Сохранённый заголовок",
            "text": "New paragraph",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            jar_path = Path(temp_dir) / "book.jar"
            self._write_jar(
                jar_path,
                {
                    source_path: json.dumps(source),
                    target_path: json.dumps(target, ensure_ascii=False),
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
                mode="append",
                translate_mods=False,
                translate_books=True,
                translate_quests=False,
                smart_glue=False,
            )

            service = _Service()
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

            self.assertEqual(estimated, 1)
            self.assertEqual(len(service.calls), 1)
            self.assertEqual(set(service.calls[0]), {"text"})
            output = json.loads(writer.files[target_path])
            self.assertEqual(output["name"], "Сохранённый заголовок")
            self.assertEqual(output["text"], "Перевод: New paragraph")

    def test_book_markdown_append_uses_same_yaml_and_line_rules(self) -> None:
        source_path = "assets/demo/guide/en_us/page.md"
        target_path = "assets/demo/guide/ru_ru/page.md"
        source = "\n".join(
            [
                "---",
                "title: Original Title",
                "---",
                "<page>",
                "![image](image.png)",
                "Existing paragraph",
                "New paragraph",
            ]
        )
        target = "\n".join(
            [
                "---",
                "title: Сохранённый заголовок",
                "---",
                "<page>",
                "![image](image.png)",
                "Сохранённый абзац",
                "New paragraph",
            ]
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            jar_path = Path(temp_dir) / "manual.jar"
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
                mode="append",
                translate_mods=False,
                translate_books=True,
                translate_quests=False,
                smart_glue=False,
            )

            service = _Service()
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

            self.assertEqual(estimated, 1)
            self.assertEqual(service.calls, [{"6": "New paragraph"}])
            output = writer.files[target_path].decode("utf-8")
            self.assertIn("title: Сохранённый заголовок", output)
            self.assertIn("Сохранённый абзац", output)
            self.assertIn("Перевод: New paragraph", output)
            self.assertIn("![image](image.png)", output)


class SnbtParityTests(unittest.TestCase):
    def test_existing_separate_target_is_preserved_in_append(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "en_us.snbt"
            target_path = root / "ru_ru.snbt"
            source_path.write_text('{title:"Original title"}', encoding="utf-8")
            target_content = '{title:"Существующий перевод"}'
            target_path.write_text(target_content, encoding="utf-8")

            state = _state()
            estimator = StringEstimator(state)
            self.assertEqual(
                estimator._estimate_snbt(
                    str(source_path),
                    "append",
                    TARGET_LANG["regex"],
                    TARGET_LANG["file"],
                ),
                0,
            )

            logs: list[str] = []
            service = _Service()
            SnbtProcessor(service, state, _callbacks(logs)).process(
                str(source_path),
                target_lang=TARGET_LANG,
                mode="append",
            )
            self.assertEqual(service.calls, [])
            self.assertEqual(
                target_path.read_text(encoding="utf-8"),
                target_content,
            )
            self.assertTrue(any("без перезаписи" in log for log in logs))

    def test_root_snbt_skip_uses_ninety_percent_formula(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "chapter.snbt"
            backup = root / "chapter.snbt.bak"
            original_values = [f'"Source {index}"' for index in range(10)]
            current_values = [
                f'"Перевод {index}"' if index < 9 else '"Source 9"'
                for index in range(10)
            ]
            backup.write_text(
                "{description:[" + ",".join(original_values) + "]}",
                encoding="utf-8",
            )
            current_content = (
                "{description:[" + ",".join(current_values) + "]}"
            )
            path.write_text(current_content, encoding="utf-8")

            state = _state()
            estimator = StringEstimator(state)
            self.assertEqual(
                estimator._estimate_snbt(
                    str(path),
                    "skip",
                    TARGET_LANG["regex"],
                    TARGET_LANG["file"],
                ),
                0,
            )

            service = _Service()
            SnbtProcessor(service, state, _callbacks()).process(
                str(path),
                target_lang=TARGET_LANG,
                mode="skip",
            )
            self.assertEqual(service.calls, [])
            self.assertEqual(path.read_text(encoding="utf-8"), current_content)

    def test_force_overwrites_separate_snbt_from_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "en_us.snbt"
            target_path = root / "ru_ru.snbt"
            source_path.write_text('{title:"Original title"}', encoding="utf-8")
            target_path.write_text('{title:"Старый перевод"}', encoding="utf-8")

            state = _state()
            estimator = StringEstimator(state)
            self.assertEqual(
                estimator._estimate_snbt(
                    str(source_path),
                    "force",
                    TARGET_LANG["regex"],
                    TARGET_LANG["file"],
                ),
                1,
            )

            service = _Service()
            SnbtProcessor(service, state, _callbacks()).process(
                str(source_path),
                target_lang=TARGET_LANG,
                mode="force",
            )
            self.assertEqual(len(service.calls[0]), 1)
            self.assertIn(
                "Перевод: Original title",
                target_path.read_text(encoding="utf-8"),
            )


class BetterQuestingParityTests(unittest.TestCase):
    @staticmethod
    def _data(name: str, desc: str) -> dict:
        return {
            "properties:10": {
                "betterquesting:10": {
                    "name:8": name,
                    "desc:8": desc,
                }
            }
        }

    def test_bq_append_estimator_matches_processor_and_preserves_existing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "quest.json"
            path.write_text(
                json.dumps(
                    self._data("Существующее имя", "New description"),
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            state = _state()
            estimator = StringEstimator(state)
            self.assertEqual(
                estimator._estimate_bq(
                    str(path),
                    "append",
                    TARGET_LANG["regex"],
                ),
                1,
            )

            service = _Service()
            BQProcessor(service, state, _callbacks()).process(
                str(path),
                target_lang=TARGET_LANG,
                mode="append",
            )
            self.assertEqual(len(service.calls), 1)
            self.assertEqual(set(service.calls[0]), {"desc:8"})
            output = json.loads(path.read_text(encoding="utf-8"))
            bq = output["properties:10"]["betterquesting:10"]
            self.assertEqual(bq["name:8"], "Существующее имя")
            self.assertEqual(
                bq["desc:8"],
                "Перевод: New description",
            )

    def test_bq_skip_does_not_touch_fully_translated_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "quest.json"
            original = json.dumps(
                self._data("Готовое имя", "Готовое описание"),
                ensure_ascii=False,
            )
            path.write_text(original, encoding="utf-8")

            state = _state()
            estimator = StringEstimator(state)
            self.assertEqual(
                estimator._estimate_bq(
                    str(path),
                    "skip",
                    TARGET_LANG["regex"],
                ),
                0,
            )

            service = _Service()
            BQProcessor(service, state, _callbacks()).process(
                str(path),
                target_lang=TARGET_LANG,
                mode="skip",
            )
            self.assertEqual(service.calls, [])
            self.assertEqual(path.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
