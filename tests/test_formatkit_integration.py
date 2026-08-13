"""Integration tests proving JarProcessor uses FormatKit plans."""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from mineai.engines.base import EngineCallbacks
from mineai.processors.jar import JarProcessor
from mineai.runtime.state import JobState


TARGET_LANG = {
    "file": "ru_ru",
    "api": "ru",
    "name": "Russian",
    "regex": r"[А-Яа-яЁё]",
}


class _Config:
    def getboolean(self, _section: str, _key: str) -> bool:
        return False


class _FormatKitService:
    def __init__(self) -> None:
        self.config = _Config()
        self.calls: list[dict[str, str]] = []

    def translate_dict(self, strings, _target_lang, _callbacks, **_kwargs):
        self.calls.append(dict(strings))
        return {
            key: value.replace("Spatial cells", "Пространственные ячейки")
            .replace("can store regions across", "могут хранить области между")
            .replace("dimensions.", "измерениями.")
            .replace("Setting", "Настройка")
            .replace("Description", "Описание")
            .replace("Engineer's Workbench", "Инженерном верстаке")
            .replace("All blueprints can be used in an", "Все чертежи используются в")
            .replace("to craft items.", "для создания предметов.")
            .replace("Resources", "Ресурсы")
            .replace("Engineered Schematics", "Инженерные схемы")
            .replace("multiblock machines", "многоблочные механизмы")
            for key, value in strings.items()
        }


class _Writer:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def write(self, path: str, payload: bytes) -> None:
        self.files[path] = payload


def _callbacks() -> EngineCallbacks:
    return EngineCallbacks(
        should_run=lambda: True,
        wait_if_paused=lambda: None,
        on_log=lambda _message, _tag: None,
        on_status=lambda _message: None,
        on_progress=lambda _count: None,
    )


def _state() -> JobState:
    state = JobState()
    state.start()
    return state


class FormatKitJarIntegrationTests(unittest.TestCase):
    @staticmethod
    def _jar(path: Path, files: dict[str, str]) -> None:
        with zipfile.ZipFile(path, "w") as archive:
            for logical_path, text in files.items():
                archive.writestr(logical_path, text.encode("utf-8"))

    def _process(self, files: dict[str, str]) -> tuple[_FormatKitService, _Writer]:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        jar_path = Path(temp.name) / "book.jar"
        self._jar(jar_path, files)
        service = _FormatKitService()
        writer = _Writer()
        JarProcessor(service, _state(), _callbacks()).process(
            str(jar_path),
            target_lang=TARGET_LANG,
            mode="force",
            output_mode="resourcepack",
            translate_mods=False,
            translate_books=True,
            pack_writer=writer,
        )
        return service, writer

    def test_guideme_paragraph_is_contextual_and_structure_is_restored(self) -> None:
        source_path = "assets/ae2/ae2guide/spatial.md"
        source = (
            "Spatial cells can store regions across\n"
            "dimensions. <ItemLink id=\"ae2:spatial_cell\" />\n\n"
            "| Setting | Description |\n"
            "| --- | --- |\n"
        )

        service, writer = self._process({source_path: source})

        calls = [payload for call in service.calls for payload in call.values()]
        paragraph = next(payload for payload in calls if "Spatial cells" in payload)
        self.assertIn("dimensions.", paragraph)
        self.assertIn("⟦FK", paragraph)
        output_path = "assets/ae2/ae2guide/_ru_ru/spatial.md"
        output = writer.files[output_path].decode("utf-8")
        self.assertIn('<ItemLink id="ae2:spatial_cell" />', output)
        self.assertIn("Пространственные ячейки", output)
        self.assertIn("| Настройка | Описание |", output)
        self.assertEqual(source.count("\n"), output.count("\n"))

    def test_legacy_lang_is_translated_as_mod_interface(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        jar_path = Path(temp.name) / "legacy.jar"
        self._jar(
            jar_path,
            {"assets/example/lang/en_US.lang": "example.resources=Resources\n"},
        )
        service = _FormatKitService()
        writer = _Writer()

        JarProcessor(service, _state(), _callbacks()).process(
            str(jar_path),
            target_lang=TARGET_LANG,
            mode="force",
            output_mode="resourcepack",
            translate_mods=True,
            translate_books=False,
            pack_writer=writer,
        )

        output = writer.files["assets/example/lang/ru_ru.lang"].decode("utf-8")
        self.assertEqual(output, "example.resources=Ресурсы\n")

    def test_json_lang_uses_sdk_units_and_preserves_source_serialization(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        jar_path = Path(temp.name) / "locale.jar"
        source = '{\n\t"item.example.name": "Resources %s"\n}\n'
        self._jar(
            jar_path,
            {"assets/example/lang/en_us.json": source},
        )
        service = _FormatKitService()
        writer = _Writer()

        JarProcessor(service, _state(), _callbacks()).process(
            str(jar_path),
            target_lang=TARGET_LANG,
            mode="force",
            output_mode="resourcepack",
            translate_mods=True,
            translate_books=False,
            pack_writer=writer,
        )

        output = writer.files["assets/example/lang/ru_ru.json"].decode("utf-8")
        self.assertEqual(
            output,
            '{\n\t"item.example.name": "Ресурсы %s"\n}\n',
        )
        payload = next(value for call in service.calls for value in call.values())
        self.assertIn("[#0#]", payload)

    def test_patchouli_json_uses_sdk_without_sending_markup_to_engine(self) -> None:
        source_path = (
            "data/example/patchouli_books/guide/en_us/entries/start.json"
        )
        source = (
            '{\n\t"name": "Resources",\n'
            '\t"pages": [{"type": "patchouli:text", '
            '"text": "Resources $(item)minecraft:stone$()"}]\n}\n'
        )

        service, writer = self._process({source_path: source})

        target_path = (
            "data/example/patchouli_books/guide/ru_ru/entries/start.json"
        )
        output = writer.files[target_path].decode("utf-8")
        self.assertTrue(output.startswith('{\n\t"name"'))
        self.assertIn('"name": "Ресурсы"', output)
        self.assertIn("$(item)minecraft:stone$()", output)
        payloads = [value for call in service.calls for value in call.values()]
        self.assertTrue(any("[#0#]" in value for value in payloads))
        self.assertTrue(all("$(item)" not in value for value in payloads))

    def test_oracle_index_mdx_is_discovered_and_written_to_locale_tree(self) -> None:
        source_path = "assets/demo/oracle_index/books/guide/index.mdx"
        source = "# Resources\n\nResources.\n"

        service, writer = self._process({source_path: source})

        target_path = (
            "assets/demo/oracle_index/books/guide/"
            ".translated/ru_ru/index.mdx"
        )
        output = writer.files[target_path].decode("utf-8")
        self.assertEqual(output, "# Ресурсы\n\nРесурсы.\n")
        self.assertTrue(service.calls)

    def test_conflicting_duplicate_locale_falls_back_to_beta36_parser(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        jar_path = Path(temp.name) / "duplicate-locale.jar"
        self._jar(
            jar_path,
            {
                "assets/example/lang/en_us.json": (
                    '{"example.name":"Old",'
                    '"example.name":"Resources"}'
                )
            },
        )
        service = _FormatKitService()
        writer = _Writer()

        JarProcessor(service, _state(), _callbacks()).process(
            str(jar_path),
            target_lang=TARGET_LANG,
            mode="force",
            output_mode="resourcepack",
            translate_mods=True,
            translate_books=False,
            pack_writer=writer,
        )

        output = json.loads(writer.files["assets/example/lang/ru_ru.json"])
        self.assertEqual(output, {"example.name": "Ресурсы"})

    def test_legacy_patchouli_string_pages_fall_back_to_beta36_parser(self) -> None:
        source_path = (
            "data/example/patchouli_books/guide/en_us/entries/legacy.json"
        )
        source = '{"name":"Resources","pages":["Resources"]}'

        _service, writer = self._process({source_path: source})

        target_path = (
            "data/example/patchouli_books/guide/ru_ru/entries/legacy.json"
        )
        output = json.loads(writer.files[target_path])
        self.assertEqual(output["name"], "Ресурсы")
        self.assertEqual(output["pages"], ["Ресурсы"])

    def test_guideme_relocated_document_copies_non_text_dependencies(self) -> None:
        source_path = "assets/ae2/ae2guide/getting-started.md"
        structure_path = (
            "assets/ae2/ae2guide/assets/assemblies/meteor_interior.snbt"
        )
        source = (
            "# Getting Started\n\n"
            '<ImportStructure src="assets/assemblies/meteor_interior.snbt" />\n'
        )

        _service, writer = self._process(
            {
                source_path: source,
                structure_path: "{DataVersion:3955}",
            }
        )

        target_dependency = (
            "assets/ae2/ae2guide/_ru_ru/assets/assemblies/"
            "meteor_interior.snbt"
        )
        self.assertEqual(
            writer.files[target_dependency],
            b"{DataVersion:3955}",
        )

    def test_ie_link_label_is_translated_without_touching_target(self) -> None:
        source_path = "assets/immersiveengineering/manual/en_us/blueprints.txt"
        source = (
            "Blueprints\nEngineering!\n"
            "All blueprints can be used in an "
            "<link;engineers_workbench;Engineer's Workbench> to craft items.<np>\n"
        )

        service, writer = self._process({source_path: source})

        payloads = [payload for call in service.calls for payload in call.values()]
        body = next(payload for payload in payloads if "blueprints" in payload)
        self.assertNotIn("engineers_workbench", body)
        output = writer.files[
            "assets/immersiveengineering/manual/ru_ru/blueprints.txt"
        ].decode("utf-8")
        self.assertIn(
            "<link;engineers_workbench;Инженерном верстаке>",
            output,
        )
        self.assertIn("<np>", output)

    def test_ie_manual_lang_metadata_is_included_when_only_books_are_selected(self) -> None:
        source_lang = {
            "manual.immersiveengineering.resources": "Resources",
            "block.immersiveengineering.test": "Test Block",
        }
        source_path = "assets/immersiveengineering/manual/en_us/index.txt"

        service, writer = self._process(
            {
                source_path: "Introduction\nEngineering\nManual body.\n",
                "assets/immersiveengineering/lang/en_us.json": json.dumps(source_lang),
            }
        )

        lang_path = "assets/immersiveengineering/lang/ru_ru.json"
        self.assertIn(lang_path, writer.files)
        output = json.loads(writer.files[lang_path])
        self.assertEqual(
            output["manual.immersiveengineering.resources"],
            "Ресурсы",
        )
        self.assertNotIn("block.immersiveengineering.test", output)

    def test_ie_addon_manual_translates_link_labels_and_own_metadata(self) -> None:
        source_path = "assets/engineered_schematics/manual/en_us/es.txt"
        service, writer = self._process(
            {
                source_path: (
                    "Introduction\nProjecting the Future\n"
                    "Engineered Schematics explains "
                    "<link;large_constructions;multiblock machines>.\n"
                ),
                "assets/engineered_schematics/lang/en_us.json": json.dumps(
                    {
                        "manual.engineered_schematics.main":
                            "Engineered Schematics",
                        "item.engineered_schematics.test": "Test Item",
                    }
                ),
            }
        )

        output = writer.files[
            "assets/engineered_schematics/manual/ru_ru/es.txt"
        ].decode("utf-8")
        self.assertIn(
            "<link;large_constructions;многоблочные механизмы>",
            output,
        )
        lang = json.loads(
            writer.files[
                "assets/engineered_schematics/lang/ru_ru.json"
            ]
        )
        self.assertEqual(
            lang,
            {"manual.engineered_schematics.main": "Инженерные схемы"},
        )
        self.assertTrue(service.calls)


if __name__ == "__main__":
    unittest.main()
