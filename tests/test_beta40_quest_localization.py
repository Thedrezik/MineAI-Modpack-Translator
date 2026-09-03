import json
import inspect
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from mineai.cache import TranslationCache
from mineai.engines.base import EngineCallbacks, EngineItem
from mineai.engines.service import _validate_candidate
from mineai.output.pack_writer import PackWriter
from mineai.processors.analyzer import ModpackAnalyzer
from mineai.processors.discovery import discover_snbt_files
from mineai.processors.estimator import StringEstimator
from mineai.processors.quest_locales import build_quest_locale_plan
from mineai.processors.snbt_extract import extract_snbt_strings
from mineai.runtime.job import TranslationJob, TranslationOptions
from mineai.runtime.state import JobState
from mineai.text_processing import (
    is_translation_key,
    is_technical_term,
    mask_protected_fragments,
    numeric_fragments,
)


TARGET_LANG = {
    "file": "ru_ru",
    "api": "ru",
    "name": "Russian",
    "regex": r"[А-Яа-яЁё]",
}


def _write_atm9_quest_fixture(root: Path) -> tuple[Path, Path]:
    chapters = root / "config" / "ftbquests" / "quests" / "chapters"
    chapters.mkdir(parents=True)
    quest_file = chapters / "getting_started.snbt"
    quest_file.write_text(
        "{\n"
        '\tid: "1111111111111111"\n'
        '\ttitle: "{atm9.quest.getting_started}"\n'
        '\tdescription: ["Literal quest hint"]\n'
        "}\n",
        encoding="utf-8-sig",
    )

    locale = root / "kubejs" / "assets" / "kubejs" / "lang" / "en_us.json"
    locale.parent.mkdir(parents=True)
    locale.write_text(
        json.dumps(
            {
                "atm9.quest.getting_started": "Getting Started",
                "atm9.unrelated.tooltip": "Unrelated tooltip",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8-sig",
    )
    return quest_file, locale


class Beta40QuestLocalizationTests(unittest.TestCase):
    def test_ftb_discovery_covers_chapters_rewards_and_split_english_locale(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "config" / "ftbquests" / "quests"
            expected = {
                root / "chapters" / "chapter.snbt",
                root / "reward_tables" / "reward.snbt",
                root / "lang" / "en_us" / "chapter.snbt",
            }
            for path in expected:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}", encoding="utf-8-sig")
            (root / "lang" / "en_us.snbt").write_text(
                "{}",
                encoding="utf-8-sig",
            )
            foreign = root / "lang" / "de_de" / "chapter.snbt"
            foreign.parent.mkdir(parents=True)
            foreign.write_text("{}", encoding="utf-8-sig")

            discovered = set(discover_snbt_files(temp_dir))

        self.assertEqual(discovered, {str(path) for path in expected})

    def test_wrapped_ftb_translation_key_is_not_sent_to_translator(self) -> None:
        reference = "{atm9.quest.getting_started}"

        self.assertTrue(is_translation_key(reference))
        self.assertEqual(
            extract_snbt_strings(
                '{title: "{atm9.quest.getting_started}" '
                'subtitle: "Literal quest hint"}'
            ),
            ["Literal quest hint"],
        )

    def test_minecraft_color_digit_is_not_a_numeric_value(self) -> None:
        self.assertEqual(numeric_fragments("&6AllTheMods 9"), ("9",))
        self.assertEqual(numeric_fragments("&6 AllTheMods 9"), ("9",))
        self.assertEqual(numeric_fragments("§2Quest 42"), ("42",))

    def test_cache_auto_repair_removes_obsolete_quest_reference_identity(self) -> None:
        reference = "{atm9.quest.getting_started}"
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "ai_cache.json"
            cache_path.write_text(
                json.dumps(
                    {
                        "__mineai_ai_cache_validation_version__": "30",
                        "__mineai_identity__:ru_" + reference: "1",
                    }
                ),
                encoding="utf-8-sig",
            )

            cache = TranslationCache(str(cache_path))

        self.assertEqual(cache.get("ru", reference), (None, False))

    def test_chemical_element_label_is_intentionally_nontranslatable(self) -> None:
        for value in ("H (1)", "Cu (29)", "Fm (100)"):
            with self.subTest(value=value):
                self.assertTrue(is_technical_term(value))

    def test_embedded_json_translates_only_text_without_false_rejection(self) -> None:
        source = (
            r'{\"clickEvent\": {\"action\": \"change_page\", '
            r'\"value\": \"26E6ED94168A05C4\"}, '
            r'\"text\": \"Click here to checkout the Questline!\", '
            r'\"color\": \"#55FF55\", \"underlined\": \"true\"}'
        )
        candidate = source.replace(
            "Click here to checkout the Questline!",
            "Нажмите здесь, чтобы открыть цепочку заданий!",
        )
        masked, mapping = mask_protected_fragments(source)

        accepted, reason, _identity = _validate_candidate(
            EngineItem(
                key="quest-json",
                original=source,
                masked=masked,
                mapping=mapping,
            ),
            candidate,
            TARGET_LANG,
        )

        self.assertTrue(accepted, reason)

    def test_json_text_component_does_not_change_structural_format(self) -> None:
        source = r'[{\"text\":\"Sword of AlfredGG\",\"italic\":false}]'
        candidate = r'[{\"text\":\"Меч Альфреда\",\"italic\":false}]'
        masked, mapping = mask_protected_fragments(source)

        accepted, reason, _identity = _validate_candidate(
            EngineItem(
                key="quest-component",
                original=source,
                masked=masked,
                mapping=mapping,
            ),
            candidate,
            TARGET_LANG,
        )

        self.assertTrue(accepted, reason)

    def test_quest_only_analysis_includes_referenced_kubejs_dictionary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _quest_file, locale = _write_atm9_quest_fixture(root)
            state = JobState()
            state.start()
            items = []
            logs = []

            total, translated = ModpackAnalyzer(state).analyze(
                str(root),
                target_lang=TARGET_LANG,
                translate_mods=False,
                translate_books=False,
                translate_quests=True,
                on_row=lambda *_args: None,
                on_item=items.append,
                on_log=lambda message, *_args: logs.append(message),
                on_status=lambda *_args: None,
            )

        locale_items = [
            item
            for item in items
            if item.kind == "Квесты · словарь локализации"
        ]
        self.assertEqual(len(locale_items), 1)
        self.assertEqual(locale_items[0].path, str(locale))
        self.assertEqual(locale_items[0].total, 1)
        self.assertEqual(locale_items[0].translated, 0)
        self.assertEqual(total, 2)
        self.assertEqual(translated, 0)
        self.assertFalse(any("atm9.unrelated.tooltip" in line for line in logs))

    def test_quest_dictionary_processor_writes_only_referenced_keys_to_pack(self) -> None:
        from mineai.processors import quest_locales

        processor_type = getattr(quest_locales, "QuestLocaleProcessor", None)
        self.assertIsNotNone(processor_type)

        class _Service:
            def __init__(self) -> None:
                self.prompt_types = []

            def translate_dict(self, values, *_args, **kwargs):
                self.prompt_types.append(kwargs.get("prompt_type"))
                return {key: "RU:" + value for key, value in values.items()}

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            quest_file, locale = _write_atm9_quest_fixture(root)
            original_locale = locale.read_bytes()
            plan = build_quest_locale_plan(
                str(root),
                [str(quest_file)],
                "ru_ru",
            )
            service = _Service()
            callbacks = EngineCallbacks(
                should_run=lambda: True,
                wait_if_paused=lambda: None,
                on_log=lambda *_args: None,
                on_status=lambda *_args: None,
                on_progress=lambda *_args: None,
                on_metric=lambda *_args: None,
            )
            writer = PackWriter(
                str(root),
                "Beta40 Quest Test",
                "1.20.1",
                "Russian",
            )
            processor_type(
                service,
                JobState(is_running=True),
                callbacks,
            ).process(
                plan.dependencies[0],
                target_lang=TARGET_LANG,
                mode="force",
                pack_writer=writer,
            )
            resourcepack, datapack = writer.close()

            self.assertIsNone(datapack)
            with zipfile.ZipFile(resourcepack) as archive:
                translated = json.loads(
                    archive.read("assets/kubejs/lang/ru_ru.json")
                )
            self.assertEqual(
                translated,
                {"atm9.quest.getting_started": "RU:Getting Started"},
            )
            self.assertEqual(service.prompt_types, ["quests"])
            self.assertEqual(locale.read_bytes(), original_locale)

    def test_unresolved_quest_key_is_resolved_from_mod_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            chapters = root / "config" / "ftbquests" / "quests" / "chapters"
            chapters.mkdir(parents=True)
            quest_file = chapters / "archive_key.snbt"
            quest_file.write_text(
                '{title: "{example.quest.archive_title}"}',
                encoding="utf-8-sig",
            )
            mods = root / "mods"
            mods.mkdir()
            with zipfile.ZipFile(mods / "example.jar", "w") as archive:
                archive.writestr(
                    "assets/example/lang/en_us.json",
                    json.dumps(
                        {"example.quest.archive_title": "Archive Quest"}
                    ),
                )

            plan = build_quest_locale_plan(
                str(root),
                [str(quest_file)],
                "ru_ru",
            )

        self.assertEqual(plan.missing_keys, frozenset())
        self.assertEqual(plan.resolved_keys, {"example.quest.archive_title"})
        self.assertEqual(len(plan.dependencies), 1)
        self.assertEqual(
            plan.dependencies[0].target_path,
            "assets/example/lang/ru_ru.json",
        )
        self.assertEqual(
            plan.dependencies[0].source_entries,
            {"example.quest.archive_title": "Archive Quest"},
        )

    def test_missing_en_us_key_uses_consensus_from_other_locales(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            chapters = root / "config" / "ftbquests" / "quests" / "chapters"
            chapters.mkdir(parents=True)
            quest_file = chapters / "mystical_ag.snbt"
            quest_file.write_text(
                '{description: ["{atm9.quest.ma.desc.atm}"]}',
                encoding="utf-8-sig",
            )
            lang_dir = root / "kubejs" / "assets" / "kubejs" / "lang"
            lang_dir.mkdir(parents=True)
            (lang_dir / "en_us.json").write_text("{}", encoding="utf-8-sig")
            for locale in ("de_de", "it_it", "nb_no"):
                (lang_dir / f"{locale}.json").write_text(
                    json.dumps(
                        {
                            "atm9.quest.ma.desc.atm": (
                                "Requires a Crux (Next Quest)"
                            )
                        }
                    ),
                    encoding="utf-8-sig",
                )
            (lang_dir / "es_es.json").write_text(
                json.dumps(
                    {
                        "atm9.quest.ma.desc.atm": (
                            "Requiere un Crux (Próxima misión)"
                        )
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8-sig",
            )

            plan = build_quest_locale_plan(
                str(root),
                [str(quest_file)],
                "ru_ru",
            )

        self.assertEqual(plan.missing_keys, frozenset())
        self.assertEqual(plan.resolved_keys, {"atm9.quest.ma.desc.atm"})
        self.assertEqual(len(plan.dependencies), 1)
        self.assertEqual(
            plan.dependencies[0].target_path,
            "assets/kubejs/lang/ru_ru.json",
        )
        self.assertEqual(
            plan.dependencies[0].source_entries,
            {"atm9.quest.ma.desc.atm": "Requires a Crux (Next Quest)"},
        )

    def test_estimator_counts_referenced_dictionary_in_quest_only_mode(self) -> None:
        self.assertIn(
            "quest_locale_plan",
            inspect.signature(StringEstimator.estimate).parameters,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            quest_file, _locale = _write_atm9_quest_fixture(root)
            plan = build_quest_locale_plan(
                str(root),
                [str(quest_file)],
                "ru_ru",
            )
            total = StringEstimator(JobState(is_running=True)).estimate(
                [],
                [],
                [str(quest_file)],
                [],
                target_lang=TARGET_LANG,
                mode="force",
                translate_mods=False,
                translate_books=False,
                translate_quests=True,
                smart_glue=False,
                quest_locale_plan=plan,
            )

        self.assertEqual(total, 2)

    def test_translation_job_builds_quest_locale_resourcepack(self) -> None:
        class _Config:
            @staticmethod
            def getboolean(_section, _option):
                return False

            @staticmethod
            def get(_section, _option):
                return ""

        class _Service:
            prompt_types = []

            def __init__(self, *_args, **_kwargs) -> None:
                pass

            def translate_dict(self, values, *_args, **kwargs):
                self.prompt_types.append(kwargs.get("prompt_type"))
                return {key: "RU:" + value for key, value in values.items()}

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _quest_file, locale = _write_atm9_quest_fixture(root)
            original_locale = locale.read_bytes()
            state = JobState()
            state.start()
            logs = []
            job = TranslationJob(
                _Config(),
                TranslationCache(str(root / "google_cache.json")),
                TranslationCache(str(root / "ai_cache.json")),
                state,
                on_log=lambda message, *_args: logs.append(message),
                on_status=lambda *_args: None,
                on_row=lambda *_args: None,
            )
            options = TranslationOptions(
                mc_dir=str(root),
                language_label="Русский",
                mc_version="1.20.1",
                output_mode="resourcepack",
                pack_name="Beta40 Quest Job",
                engine="google",
                google_mode="normal",
                ai_mode="standard",
                ai_batch=10,
                ai_provider="local",
                process_mode="force",
                translate_mods=False,
                translate_books=False,
                translate_quests=True,
            )
            with mock.patch("mineai.runtime.job.TranslationService", _Service):
                job.run_translation(options)

            resourcepack = root / "resourcepacks" / "Beta40 Quest Job.zip"
            self.assertTrue(
                resourcepack.is_file(),
                "quest resource pack was not created",
            )
            with zipfile.ZipFile(resourcepack) as archive:
                translated = json.loads(
                    archive.read("assets/kubejs/lang/ru_ru.json")
                )
            self.assertEqual(
                translated,
                {"atm9.quest.getting_started": "RU:Getting Started"},
            )
            self.assertIn("quests", _Service.prompt_types)
            self.assertEqual(locale.read_bytes(), original_locale)
            self.assertFalse(any("Нечего переводить" in line for line in logs))


if __name__ == "__main__":
    unittest.main()
