import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from formatkit import FormatRegistry, FormatValidationError
from mineai.engines.base import EngineCallbacks
from mineai.processors.discovery import discover_heracles_files
from mineai.processors.analyzer import ModpackAnalyzer
from mineai.processors.estimator import StringEstimator
from mineai.processors.heracles import HeraclesProcessor
from mineai.runtime.state import JobState
from mineai.runtime.job import TranslationJob, TranslationOptions


TARGET_LANG = {
    "api": "ru",
    "file": "ru_ru",
    "name": "Russian",
    "regex": r"[А-Яа-яЁё]",
}


def _source_quest() -> str:
    return r"""{
  "display": {
    "title": "Quest Title",
    "subtitle": {"translate": "Useful subtitle", "color": "gold"},
    "description": [
      "# Getting Started",
      "Use <task task=\"check\" quest=\"demo\"/> and [the guide](guide.md)."
    ],
    "groups": {"Main Group": {"position": [2, 4]}}
  },
  "settings": {"repeatable": false},
  "dependencies": ["other_quest"],
  "tasks": {
    "check": {
      "type": "heracles:dummy",
      "title": "Custom Task",
      "description": "Click **the button**",
      "value": "DO_NOT_TRANSLATE",
      "nbt": {"title": "NBT title must stay English"}
    },
    "nested": {
      "type": "heracles:composite",
      "title": "Combined Task",
      "tasks": {
        "child": {"type": "heracles:check", "title": "Child Task"}
      }
    }
  },
  "rewards": {
    "run": {
      "type": "heracles:command",
      "title": "Command Reward",
      "command": "/say Hello player"
    }
  }
}"""


class HeraclesFormatKitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = FormatRegistry.default()

    def test_extracts_only_schema_defined_user_text(self) -> None:
        source = _source_quest()
        plan = self.registry.plan(
            "config/heracles/quests/main/demo.json",
            source,
            "ru_ru",
        )
        payload = "\n".join(unit.payload for unit in plan.units)

        for expected in (
            "Quest Title",
            "Useful subtitle",
            "Getting Started",
            "the guide",
            "Main Group",
            "Custom Task",
            "the button",
            "Combined Task",
            "Child Task",
            "Command Reward",
        ):
            self.assertIn(expected, payload)
        self.assertNotIn("title", [unit.payload for unit in plan.units])
        self.assertNotIn("description", [unit.payload for unit in plan.units])
        for protected in (
            "heracles:dummy",
            "DO_NOT_TRANSLATE",
            "NBT title must stay English",
            "other_quest",
            "/say Hello player",
            "guide.md",
        ):
            self.assertNotIn(protected, payload)

    def test_apply_json_escapes_translation_and_preserves_structure(self) -> None:
        source = _source_quest()
        plan = self.registry.plan(
            "config/heracles/quests/main/demo.json",
            source,
            "ru_ru",
        )
        translations = {}
        for unit in plan.units:
            candidate = unit.payload
            candidate = candidate.replace("Quest Title", 'Название "квеста"')
            candidate = candidate.replace("Useful subtitle", "Полезный подзаголовок")
            candidate = candidate.replace("Getting Started", "Начало работы")
            candidate = candidate.replace("Use ", "Используйте ")
            candidate = candidate.replace("the guide", "руководство")
            candidate = candidate.replace("Main Group", "Главная группа")
            candidate = candidate.replace("Custom Task", "Особая задача")
            candidate = candidate.replace("Click ", "Нажмите ")
            candidate = candidate.replace("the button", "кнопку")
            candidate = candidate.replace("Combined Task", "Составная задача")
            candidate = candidate.replace("Child Task", "Дочерняя задача")
            candidate = candidate.replace("Command Reward", "Командная награда")
            translations[unit.id] = candidate

        result = plan.apply(translations)
        parsed = json.loads(result.text)

        self.assertEqual(parsed["display"]["title"], 'Название "квеста"')
        self.assertEqual(
            parsed["display"]["subtitle"]["translate"],
            "Полезный подзаголовок",
        )
        self.assertIn("Главная группа", parsed["display"]["groups"])
        self.assertEqual(parsed["tasks"]["check"]["value"], "DO_NOT_TRANSLATE")
        self.assertEqual(
            parsed["tasks"]["check"]["nbt"]["title"],
            "NBT title must stay English",
        )
        self.assertEqual(parsed["rewards"]["run"]["command"], "/say Hello player")
        self.assertIn(
            '<task task="check" quest="demo"/>',
            parsed["display"]["description"][1],
        )
        self.assertIn(
            "[руководство](guide.md)",
            parsed["display"]["description"][1],
        )
        self.assertTrue(result.validation.ok)

    def test_noop_is_byte_exact_and_unknown_unit_is_rejected(self) -> None:
        source = _source_quest().replace("\n", "\r\n") + "\r\n"
        plan = self.registry.plan(
            "config/heracles/quests/main/demo.json",
            source,
            "ru_ru",
        )
        self.assertEqual(plan.apply({}).text, source)
        with self.assertRaises(FormatValidationError):
            plan.apply({"not-a-real-unit": "Повреждение"})

    def test_real_translation_keys_are_not_exposed_but_literal_translate_is(self) -> None:
        source = json.dumps(
            {
                "display": {
                    "title": {"translate": "pack.quest.actual_key"},
                    "subtitle": {"translate": "Visible English subtitle"},
                    "description": [],
                    "groups": {},
                }
            }
        )
        plan = self.registry.plan(
            "config/heracles/quests/main/components.json",
            source,
            "ru_ru",
        )
        payloads = [unit.payload for unit in plan.units]
        self.assertIn("Visible English subtitle", payloads)
        self.assertNotIn("pack.quest.actual_key", payloads)

    def test_task_translation_keys_remain_immutable(self) -> None:
        source = json.dumps(
            {
                "display": {"description": [], "groups": {}},
                "tasks": {
                    "keyed": {
                        "type": "heracles:dummy",
                        "title": "pack.task.title",
                        "description": "pack.task.description",
                        "value": "keyed",
                    },
                    "literal": {
                        "type": "heracles:dummy",
                        "title": "Readable title",
                        "description": "Readable description",
                        "value": "literal",
                    },
                },
            }
        )
        payloads = [
            unit.payload
            for unit in self.registry.plan(
                "config/heracles/quests/main/tasks.json",
                source,
                "ru_ru",
            ).units
        ]
        self.assertEqual(payloads, ["Readable title", "Readable description"])

    def test_groups_and_tutorial_round_trip_without_markup_changes(self) -> None:
        groups = "Main Group\r\nTechnology\r\n"
        group_plan = self.registry.plan(
            "config/heracles/groups.txt",
            groups,
            "ru_ru",
        )
        group_result = group_plan.apply({
            group_plan.units[0].id: "Главная группа",
            group_plan.units[1].id: "Технологии",
        })
        self.assertEqual(group_result.text, "Главная группа\r\nТехнологии\r\n")

        tutorial = (
            '<h1 class="quest-title">Welcome</h1>\n'
            '<p>Open the <b>quest book</b>.</p>\n'
        )
        tutorial_plan = self.registry.plan(
            "config/heracles/tutorial.html",
            tutorial,
            "ru_ru",
        )
        self.assertNotIn("quest-title", "\n".join(u.payload for u in tutorial_plan.units))
        translated = {
            unit.id: unit.payload
            .replace("Welcome", "Добро пожаловать")
            .replace("Open the", "Откройте")
            .replace("quest book", "книгу квестов")
            for unit in tutorial_plan.units
        }
        tutorial_result = tutorial_plan.apply(translated)
        self.assertIn('<h1 class="quest-title">', tutorial_result.text)
        self.assertIn("<b>книгу квестов</b>", tutorial_result.text)
        self.assertTrue(tutorial_result.validation.ok)


class HeraclesDiscoveryTests(unittest.TestCase):
    def test_discovers_only_live_heracles_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "config" / "heracles"
            quest = root / "quests" / "chapter" / "one.json"
            backup = root / "quests" / "chapter" / "old.json.bak"
            groups = root / "groups.txt"
            tutorial = root / "tutorial.html"
            unrelated = root / "theme.json"
            quest.parent.mkdir(parents=True)
            for path in (quest, backup, groups, tutorial, unrelated):
                path.write_text("{}", encoding="utf-8")

            self.assertEqual(
                discover_heracles_files(temp_dir),
                [str(groups), str(quest), str(tutorial)],
            )


class _Service:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def translate_dict(self, strings, _target_lang, _callbacks, **_kwargs):
        self.calls.append(dict(strings))
        return {
            key: value.replace("Quest Title", "Название квеста")
            for key, value in strings.items()
        }

    def discard_cached_translation(self, *_args, **_kwargs) -> None:
        return None


class HeraclesProcessorTests(unittest.TestCase):
    def test_writes_atomically_with_english_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config" / "heracles" / "quests" / "demo.json"
            path.parent.mkdir(parents=True)
            source = _source_quest()
            path.write_text(source, encoding="utf-8")
            state = JobState(is_running=True)
            service = _Service()
            callbacks = EngineCallbacks(
                should_run=state.should_run,
                wait_if_paused=state.wait_if_paused,
                on_log=lambda *_args: None,
                on_status=lambda *_args: None,
            )

            changed = HeraclesProcessor(service, state, callbacks).process(
                str(path),
                target_lang=TARGET_LANG,
                mode="append",
            )

            self.assertEqual(changed, str(path))
            self.assertEqual(Path(str(path) + ".bak").read_text(encoding="utf-8"), source)
            output = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(output["display"]["title"], "Название квеста")
            self.assertEqual(output["rewards"]["run"]["command"], "/say Hello player")
            self.assertTrue(Path(str(path) + ".bak.mineai").exists())

    def test_force_quarantines_structurally_stale_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config" / "heracles" / "quests" / "demo.json"
            path.parent.mkdir(parents=True)
            current = _source_quest()
            stale = json.dumps({
                "display": {
                    "title": "Old title",
                    "description": [],
                    "groups": {},
                }
            })
            path.write_text(current, encoding="utf-8")
            backup = Path(str(path) + ".bak")
            backup.write_text(stale, encoding="utf-8")
            state = JobState(is_running=True)
            callbacks = EngineCallbacks(
                should_run=state.should_run,
                wait_if_paused=state.wait_if_paused,
                on_log=lambda *_args: None,
                on_status=lambda *_args: None,
            )

            HeraclesProcessor(_Service(), state, callbacks).process(
                str(path),
                target_lang=TARGET_LANG,
                mode="force",
            )

            self.assertEqual(backup.read_text(encoding="utf-8"), current)
            stale_files = list(path.parent.glob("demo.json.bak.stale-*"))
            self.assertEqual(len(stale_files), 1)
            self.assertEqual(stale_files[0].read_text(encoding="utf-8"), stale)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["display"]["title"],
                "Название квеста",
            )

    def test_estimator_and_analyzer_use_the_same_formatkit_units(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config" / "heracles" / "quests" / "demo.json"
            path.parent.mkdir(parents=True)
            path.write_text(_source_quest(), encoding="utf-8")
            state = JobState(is_running=True)
            expected = len(
                FormatRegistry.default()
                .plan(str(path), _source_quest(), "ru_ru")
                .units
            )

            estimator = StringEstimator(state)
            self.assertEqual(
                estimator._estimate_heracles(
                    str(path),
                    "append",
                    TARGET_LANG,
                ),
                expected,
            )

            items = []
            total, translated = ModpackAnalyzer(state).analyze(
                temp_dir,
                target_lang=TARGET_LANG,
                translate_mods=False,
                translate_books=False,
                translate_quests=True,
                on_row=lambda *_args: None,
                on_item=items.append,
                on_log=lambda *_args: None,
                on_status=lambda *_args: None,
            )
            self.assertEqual((total, translated), (expected, 0))
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].kind, "Heracles / Odyssey")
            self.assertEqual(items[0].path, str(path))

    def test_append_preserves_existing_translation_and_translates_new_unit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config" / "heracles" / "quests" / "demo.json"
            path.parent.mkdir(parents=True)
            source = _source_quest()
            path.write_text(source, encoding="utf-8")
            state = JobState(is_running=True)
            callbacks = EngineCallbacks(
                should_run=state.should_run,
                wait_if_paused=state.wait_if_paused,
                on_log=lambda *_args: None,
                on_status=lambda *_args: None,
            )
            first = _Service()
            HeraclesProcessor(first, state, callbacks).process(
                str(path), target_lang=TARGET_LANG, mode="append"
            )
            before = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(before["display"]["title"], "Название квеста")

            second = _Service()
            HeraclesProcessor(second, state, callbacks).process(
                str(path), target_lang=TARGET_LANG, mode="append"
            )
            requested = {
                value
                for call in second.calls
                for value in call.values()
            }
            self.assertNotIn("Quest Title", requested)
            after = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(after["display"]["title"], "Название квеста")


class _Config:
    def get(self, _section, _key, fallback=""):
        return fallback

    def getboolean(self, _section, _key, fallback=False):
        return fallback


class HeraclesRuntimeTests(unittest.TestCase):
    def test_runtime_honors_quest_selection_and_estimator_receives_same_files(self) -> None:
        selected = "C:/pack/config/heracles/quests/main/selected.json"
        unchecked = "C:/pack/config/heracles/quests/main/unchecked.json"
        options = TranslationOptions(
            mc_dir="C:/pack",
            language_label="Русский",
            mc_version="1.20.1",
            output_mode="inplace",
            pack_name="MineAI_Pack",
            engine="google",
            google_mode="single",
            ai_mode="safe",
            ai_batch=20,
            ai_provider="local",
            process_mode="append",
            translate_mods=False,
            translate_books=False,
            translate_quests=True,
            selected_items=frozenset({
                f"quests:{os.path.normcase(os.path.abspath(selected))}"
            }),
        )
        state = JobState(is_running=True)
        cache = mock.Mock()
        job = TranslationJob(
            _Config(),
            cache,
            cache,
            state,
            on_log=lambda *_args: None,
            on_status=lambda *_args: None,
            on_row=lambda *_args: None,
        )
        with (
            mock.patch("mineai.runtime.job.discover_jar_files", return_value=[]),
            mock.patch("mineai.runtime.job.discover_loose_lang_files", return_value=[]),
            mock.patch("mineai.runtime.job.discover_snbt_files", return_value=[]),
            mock.patch("mineai.runtime.job.discover_bq_files", return_value=[]),
            mock.patch(
                "mineai.runtime.job.discover_heracles_files",
                return_value=[selected, unchecked],
            ),
            mock.patch("mineai.runtime.job.StringEstimator.estimate", return_value=1) as estimate,
            mock.patch("mineai.runtime.job.HeraclesProcessor") as processor,
        ):
            job.run_translation(options)

        self.assertEqual(estimate.call_args.kwargs["heracles_files"], [selected])
        processed = [
            call.args[0]
            for call in processor.return_value.process.call_args_list
        ]
        self.assertEqual(processed, [selected])


if __name__ == "__main__":
    unittest.main()
