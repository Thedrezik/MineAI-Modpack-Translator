"""Regression tests for Puffish Skills datapack support in Beta45."""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from mineai.processors.discovery import discover_puffish_skills_files
from mineai.processors.puffish_skills import (
    PuffishSkillsProcessor,
    apply_skill_translations,
    count_translated_skill_units,
    extract_skill_units,
    skill_datapack_target_path,
)
from mineai.engines.base import EngineCallbacks
from mineai.output.pack_writer import PackWriter
from mineai.processors.estimator import StringEstimator
from mineai.runtime.state import JobState


class PuffishSkillsAdapterTests(unittest.TestCase):
    def test_discovery_finds_paxi_and_regular_datapacks_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            mc_dir = Path(temporary)
            paxi_file = (
                mc_dir
                / "config"
                / "paxi"
                / "datapacks"
                / "PotA_Progression"
                / "data"
                / "pota"
                / "puffish_skills"
                / "categories"
                / "class_tree"
                / "definitions.json"
            )
            normal_file = (
                mc_dir
                / "datapacks"
                / "extra"
                / "data"
                / "example"
                / "puffish_skills"
                / "skills.json"
            )
            excluded_file = (
                mc_dir
                / "config"
                / "paxi"
                / "datapacks"
                / "PotA_Progression"
                / "data"
                / "levelz"
                / "not_a_skill.json"
            )
            save_file = (
                mc_dir
                / "saves"
                / "World"
                / "data"
                / "example"
                / "puffish_skills"
                / "live.json"
            )
            for path in (paxi_file, normal_file, excluded_file, save_file):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}", encoding="utf-8")

            self.assertEqual(
                discover_puffish_skills_files(str(mc_dir)),
                sorted((str(paxi_file), str(normal_file)), key=str.casefold),
            )

    def test_units_are_text_only_and_technical_graph_is_not_translated(self) -> None:
        source = {
            "title": "The Sixfold Path",
            "root_defence": {
                "title": "§9Path of the Bulwark§r",
                "description": "Plant your feet and endure.",
                "icon": {"type": "item", "data": {"item": "minecraft:shield"}},
                "frame": {"type": "advancement", "data": {"frame": "challenge"}},
                "x": 12.5,
                "y": -3,
                "definition": "root_defence",
                "rewards": [{"type": "puffish_skills:attribute", "attribute": "minecraft:generic.armor"}],
            },
        }

        units = extract_skill_units(source)

        self.assertEqual(
            units,
            {
                "json:/title": "The Sixfold Path",
                "json:/root_defence/title": "§9Path of the Bulwark§r",
                "json:/root_defence/description": "Plant your feet and endure.",
            },
        )

    def test_apply_preserves_skill_graph_and_replaces_only_selected_text(self) -> None:
        source = {
            "title": "The Sixfold Path",
            "root": {
                "title": "Path of the Blade",
                "description": "Commit to the blade.",
                "x": 4,
                "y": 9,
                "connections": ["left", "right"],
                "icon": "rogues:netherite_glaive",
            },
        }
        original_graph = json.loads(json.dumps(source["root"], sort_keys=True))

        translated = apply_skill_translations(
            source,
            {
                "json:/title": "Шесть путей",
                "json:/root/title": "Путь клинка",
            },
        )

        self.assertEqual(translated["title"], "Шесть путей")
        self.assertEqual(translated["root"]["title"], "Путь клинка")
        self.assertEqual(translated["root"]["description"], source["root"]["description"])
        self.assertEqual(
            json.loads(json.dumps(translated["root"], sort_keys=True)),
            {
                **original_graph,
                "title": "Путь клинка",
            },
        )

    def test_target_path_is_a_datapack_resource_path(self) -> None:
        source = (
            "C:/game/minecraft/config/paxi/datapacks/PotA_Progression/"
            "data/pota/puffish_skills/categories/class_tree/definitions.json"
        )
        self.assertEqual(
            skill_datapack_target_path(source, "C:/game/minecraft"),
            "data/pota/puffish_skills/categories/class_tree/definitions.json",
        )

    def test_processor_writes_only_a_datapack_overlay(self) -> None:
        class Service:
            def translate_dict(self, strings, target_lang, callbacks, **kwargs):
                return {
                    key: value.replace("The Sixfold Path", "Шесть путей")
                    .replace("Path of the Blade", "Путь клинка")
                    for key, value in strings.items()
                }

        class Writer:
            def __init__(self):
                self.writes = []

            def write(self, path, payload):
                self.writes.append((path, json.loads(payload.decode("utf-8"))))

        with tempfile.TemporaryDirectory() as temporary:
            mc_dir = Path(temporary)
            source_path = (
                mc_dir
                / "config"
                / "paxi"
                / "datapacks"
                / "PotA_Progression"
                / "data"
                / "pota"
                / "puffish_skills"
                / "categories"
                / "class_tree"
                / "definitions.json"
            )
            source_path.parent.mkdir(parents=True)
            source_path.write_text(
                json.dumps(
                    {
                        "root": {
                            "title": "Path of the Blade",
                            "description": "Commit to the blade.",
                            "x": 3,
                            "connections": ["a", "b"],
                        }
                    }
                ),
                encoding="utf-8",
            )
            writer = Writer()
            callbacks = EngineCallbacks(
                should_run=lambda: True,
                wait_if_paused=lambda: None,
                on_log=lambda *_args: None,
                on_status=lambda *_args: None,
            )
            result = PuffishSkillsProcessor(
                Service(),
                JobState(is_running=True),
                callbacks,
            ).process(
                str(source_path),
                str(mc_dir),
                target_lang={"api": "ru", "file": "ru_ru", "regex": "[А-Яа-яЁё]"},
                mode="append",
                output_mode="resourcepack",
                pack_writer=writer,
            )

            self.assertIsNone(result)
            self.assertEqual(len(writer.writes), 1)
            target_path, output = writer.writes[0]
            self.assertEqual(
                target_path,
                "data/pota/puffish_skills/categories/class_tree/definitions.json",
            )
            self.assertEqual(output["root"]["title"], "Путь клинка")
            self.assertEqual(output["root"]["x"], 3)
            self.assertEqual(output["root"]["connections"], ["a", "b"])

    def test_processor_reuses_valid_overlay_and_repairs_only_invalid_units(self) -> None:
        class Service:
            def __init__(self):
                self.calls = []

            def translate_dict(self, strings, target_lang, callbacks, **kwargs):
                self.calls.append(dict(strings))
                return {
                    key: value.replace(
                        "Commit to the blade.",
                        "Посвятите себя клинку.",
                    )
                    for key, value in strings.items()
                }

        class Writer:
            def __init__(self):
                self.writes = []

            def write(self, path, payload):
                self.writes.append((path, json.loads(payload.decode("utf-8"))))

        with tempfile.TemporaryDirectory() as temporary:
            mc_dir = Path(temporary)
            source_path = (
                mc_dir
                / "config"
                / "paxi"
                / "datapacks"
                / "PotA"
                / "data"
                / "pota"
                / "puffish_skills"
                / "categories"
                / "class_tree"
                / "definitions.json"
            )
            source_path.parent.mkdir(parents=True)
            source_path.write_text(
                json.dumps(
                    {
                        "root": {
                            "title": "Path of the Blade",
                            "description": "Commit to the blade.",
                            "x": 3,
                        }
                    }
                ),
                encoding="utf-8",
            )
            overlay = {
                "root": {
                    "title": "Путь клинка",
                    "description": "Commit to the blade.",
                    "x": 3,
                }
            }
            archive_dir = mc_dir / "MineAI_Datapacks"
            archive_dir.mkdir()
            archive_path = archive_dir / "previous.zip"
            target_path = "data/pota/puffish_skills/categories/class_tree/definitions.json"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(target_path, json.dumps(overlay))

            service = Service()
            writer = Writer()
            callbacks = EngineCallbacks(
                should_run=lambda: True,
                wait_if_paused=lambda: None,
                on_log=lambda *_args: None,
                on_status=lambda *_args: None,
            )
            PuffishSkillsProcessor(
                service,
                JobState(is_running=True),
                callbacks,
            ).process(
                str(source_path),
                str(mc_dir),
                target_lang={"api": "ru", "file": "ru_ru", "regex": "[А-Яа-яЁё]"},
                mode="append",
                output_mode="resourcepack",
                pack_writer=writer,
            )

            self.assertEqual(
                service.calls,
                [{"json:/root/description": "Commit to the blade."}],
            )
            self.assertEqual(len(writer.writes), 1)
            output = writer.writes[0][1]
            self.assertEqual(output["root"]["title"], "Путь клинка")
            self.assertEqual(output["root"]["description"], "Посвятите себя клинку.")
            self.assertEqual(output["root"]["x"], 3)
            self.assertEqual(
                count_translated_skill_units(
                    str(source_path),
                    str(mc_dir),
                    {"api": "ru", "file": "ru_ru", "regex": "[А-Яа-яЁё]"},
                ),
                1,
            )

    def test_real_pack_writer_places_skill_data_in_datapack_archive(self) -> None:
        class Service:
            def translate_dict(self, strings, target_lang, callbacks, **kwargs):
                return {
                    key: value.replace("The Sixfold Path", "Шесть путей")
                    for key, value in strings.items()
                }

        with tempfile.TemporaryDirectory() as temporary:
            mc_dir = Path(temporary)
            source_path = (
                mc_dir
                / "config"
                / "paxi"
                / "datapacks"
                / "PotA"
                / "data"
                / "pota"
                / "puffish_skills"
                / "categories"
                / "class_tree"
                / "category.json"
            )
            source_path.parent.mkdir(parents=True)
            original = json.dumps({"title": "The Sixfold Path", "x": 1})
            source_path.write_text(original, encoding="utf-8")
            writer = PackWriter(str(mc_dir), "Beta45", "1.20.1", "Russian")
            callbacks = EngineCallbacks(
                should_run=lambda: True,
                wait_if_paused=lambda: None,
                on_log=lambda *_args: None,
                on_status=lambda *_args: None,
            )
            PuffishSkillsProcessor(
                Service(),
                JobState(is_running=True),
                callbacks,
            ).process(
                str(source_path),
                str(mc_dir),
                target_lang={"api": "ru", "file": "ru_ru", "regex": "[А-Яа-яЁё]"},
                mode="append",
                output_mode="resourcepack",
                pack_writer=writer,
            )
            resourcepack_path, datapack_path = writer.close()

            self.assertIsNone(resourcepack_path)
            self.assertIsNotNone(datapack_path)
            self.assertEqual(source_path.read_text(encoding="utf-8"), original)
            with zipfile.ZipFile(datapack_path) as archive:
                self.assertIn(
                    "data/pota/puffish_skills/categories/class_tree/category.json",
                    archive.namelist(),
                )
                output = json.loads(
                    archive.read(
                        "data/pota/puffish_skills/categories/class_tree/category.json"
                    )
                )
            self.assertEqual(output, {"title": "Шесть путей", "x": 1})

    def test_estimator_excludes_valid_overlay_units_on_incremental_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            mc_dir = Path(temporary)
            source_path = (
                mc_dir
                / "config"
                / "paxi"
                / "datapacks"
                / "PotA"
                / "data"
                / "pota"
                / "puffish_skills"
                / "categories"
                / "class_tree"
                / "category.json"
            )
            source_path.parent.mkdir(parents=True)
            source_path.write_text(
                json.dumps({"title": "The Sixfold Path"}),
                encoding="utf-8",
            )
            archive_dir = mc_dir / "MineAI_Datapacks"
            archive_dir.mkdir()
            with zipfile.ZipFile(archive_dir / "previous.zip", "w") as archive:
                archive.writestr(
                    "data/pota/puffish_skills/categories/class_tree/category.json",
                    json.dumps({"title": "Шесть путей"}),
                )

            total = StringEstimator(JobState(is_running=True)).estimate(
                [],
                [],
                [],
                [],
                target_lang={"api": "ru", "file": "ru_ru", "regex": "[А-Яа-яЁё]"},
                mode="append",
                translate_mods=False,
                translate_books=False,
                translate_quests=True,
                smart_glue=False,
                mc_dir=str(mc_dir),
                puffish_files=[str(source_path)],
            )
            self.assertEqual(total, 0)


if __name__ == "__main__":
    unittest.main()
