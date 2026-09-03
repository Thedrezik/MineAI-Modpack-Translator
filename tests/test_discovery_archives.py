import os
import tempfile
import unittest
import zipfile

from mineai.processors.discovery import (
    discover_jar_files,
    discover_loose_lang_files,
)
from mineai.mod_names import get_mod_name
from mineai.processors.analyzer import ModpackAnalyzer
from mineai.runtime.state import JobState


TARGET_LANG = {
    "file": "ru_ru",
    "api": "ru",
    "name": "Russian",
    "regex": r"[А-Яа-яЁё]",
}


class ArchiveDiscoveryTests(unittest.TestCase):
    def test_mod_jars_and_source_resourcepack_zips_are_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as mc_dir:
            mods_dir = os.path.join(mc_dir, "mods")
            packs_dir = os.path.join(mc_dir, "resourcepacks")
            os.makedirs(mods_dir)
            os.makedirs(packs_dir)
            mod_path = os.path.join(mods_dir, "Example.jar")
            pack_path = os.path.join(packs_dir, "English Sources.zip")
            with zipfile.ZipFile(mod_path, "w") as archive:
                archive.writestr("assets/example/lang/en_us.json", "{}")
            with zipfile.ZipFile(pack_path, "w") as archive:
                archive.writestr("assets/example/lang/en_us.json", "{}")
            with zipfile.ZipFile(
                os.path.join(packs_dir, "Russian Only.zip"),
                "w",
            ) as archive:
                archive.writestr("assets/example/lang/ru_ru.json", "{}")

            discovered = discover_jar_files(mc_dir)

            self.assertEqual(discovered, [mod_path, pack_path])
            self.assertEqual(get_mod_name(pack_path), "English Sources")

    def test_unpacked_resourcepack_locale_is_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as mc_dir:
            source = os.path.join(
                mc_dir,
                "resourcepacks",
                "Unpacked UI",
                "assets",
                "minecraft",
                "lang",
                "en_us.json",
            )
            os.makedirs(os.path.dirname(source))
            with open(source, "w", encoding="utf-8") as handle:
                handle.write('{"menu.play": "Play"}')

            self.assertEqual(discover_loose_lang_files(mc_dir), [source])

    def test_analyzer_includes_source_resourcepack_zip(self) -> None:
        with tempfile.TemporaryDirectory() as mc_dir:
            packs_dir = os.path.join(mc_dir, "resourcepacks")
            os.makedirs(packs_dir)
            pack_path = os.path.join(packs_dir, "English UI.zip")
            with zipfile.ZipFile(pack_path, "w") as archive:
                archive.writestr(
                    "assets/example/lang/en_us.json",
                    '{"menu.play": "Play game"}',
                )
            state = JobState()
            state.start()

            analyzed = ModpackAnalyzer(state).analyze(
                mc_dir,
                target_lang=TARGET_LANG,
                translate_mods=True,
                translate_books=False,
                translate_quests=False,
                on_row=lambda *_args: None,
                on_log=lambda *_args: None,
                on_status=lambda *_args: None,
            )

            self.assertEqual(analyzed, (1, 0))


if __name__ == "__main__":
    unittest.main()
