import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

from mineai.cache import TranslationCache, _CACHE_VALIDATION_VERSION
from mineai.io_utils import atomic_write_bytes
from mineai.output.pack_writer import PackWriter


class AtomicWriteTests(unittest.TestCase):
    def test_corrupt_ai_cache_is_replaced_immediately_after_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "ai_cache.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{not-json")

            TranslationCache(path)

            self.assertTrue(os.path.exists(path + ".corrupt"))
            with open(path, encoding="utf-8") as handle:
                repaired = json.load(handle)
            self.assertIsInstance(repaired, dict)

    def test_incomplete_ai_cache_entry_is_removed_during_load(self):
        source = (
            "The grid fills itself when the items are ready, meaning you do "
            "not have to keep checking if the items are available."
        )
        incomplete = (
            "Сетка заполняется автоматически, meaning you do not have to "
            "keep checking if the items are available."
        )

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "ai_cache.json")
            cache = TranslationCache(path)
            cache.set("ru", source, incomplete)
            cache.save()

            repaired = TranslationCache(path)

            self.assertEqual(repaired.get("ru", source), (None, False))
            self.assertTrue(os.path.exists(path + ".pre-auto-repair"))
            with open(path + ".pre-auto-repair", encoding="utf-8") as handle:
                self.assertIn("ru_" + source, json.load(handle))
            with open(path, encoding="utf-8") as handle:
                self.assertNotIn("ru_" + source, json.load(handle))

    def test_old_ai_cache_discards_individually_invalid_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "ai_cache.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(
                    {"ru_When fully upgraded.": "Экспорт"},
                    handle,
                    ensure_ascii=False,
                )

            cache = TranslationCache(path)

            self.assertEqual(cache.get("ru", "When fully upgraded."), (None, False))
            self.assertTrue(os.path.exists(path + ".pre-auto-repair"))
            cache.set("ru", "Hello", "Привет")
            cache.save()
            reloaded = TranslationCache(path)
            self.assertEqual(reloaded.get("ru", "Hello"), ("Привет", False))

    def test_ai_cache_repairs_changed_numbers_and_format_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "ai_cache.json")
            source = "Use &6Energy&r: 256 E/t"
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "ru_" + source: "Используйте &6 Энергию&r: 512 E/t"
                    },
                    handle,
                    ensure_ascii=False,
                )

            cache = TranslationCache(path)

            self.assertGreaterEqual(cache.polish_changes, 1)
            self.assertEqual(cache.get("ru", source), (None, False))
            self.assertTrue(os.path.exists(path + ".pre-auto-repair"))

    def test_beta33_ai_cache_is_upgraded_without_losing_valid_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "ai_cache.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "__mineai_ai_cache_validation_version__": "27",
                        "ru_Description": "Описание",
                    },
                    handle,
                    ensure_ascii=False,
                )

            cache = TranslationCache(path)

            self.assertEqual(cache.get("ru", "Description"), ("Описание", False))
            self.assertTrue(os.path.exists(path + ".pre-auto-repair"))
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(
                    json.load(handle)["__mineai_ai_cache_validation_version__"],
                    _CACHE_VALIDATION_VERSION,
                )

    def test_failed_replace_preserves_original_and_removes_temp_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "data.json")
            with open(path, "wb") as handle:
                handle.write(b"original")

            with mock.patch("mineai.io_utils.os.replace", side_effect=OSError("locked")):
                with self.assertRaisesRegex(OSError, "locked"):
                    atomic_write_bytes(path, b"replacement")

            with open(path, "rb") as handle:
                self.assertEqual(handle.read(), b"original")
            self.assertEqual(os.listdir(directory), ["data.json"])

    def test_corrupt_cache_is_backed_up_before_next_save(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "cache.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{not-json")

            cache = TranslationCache(path)
            cache.set("ru", "Hello", "Привет")
            cache.save()

            with open(path + ".corrupt", encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "{not-json")
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["ru_Hello"], "Привет")


class PackWriterTests(unittest.TestCase):
    def test_datapack_is_installed_in_every_world_without_touching_kubejs(self):
        with tempfile.TemporaryDirectory() as directory:
            for world_name in ("World One", "World Two"):
                world = os.path.join(directory, "saves", world_name)
                os.makedirs(world)
                with open(os.path.join(world, "level.dat"), "wb") as handle:
                    handle.write(b"world")
            os.makedirs(os.path.join(directory, "saves", "Not A World"))
            kubejs_file = os.path.join(
                directory,
                "kubejs",
                "data",
                "paganbless",
                "sentinel.json",
            )
            os.makedirs(os.path.dirname(kubejs_file))
            with open(kubejs_file, "wb") as handle:
                handle.write(b"original")

            writer = PackWriter(directory, "Pagan Guide", "1.21.1", "Russian")
            entry = "data/paganbless/modonomicon/books/pagan_guide/book.json"
            writer.write(entry, b'{"name":"mineai.book.paganbless.name"}')

            resourcepack, master_datapack = writer.close()

            self.assertIsNone(resourcepack)
            self.assertEqual(writer.datapack_install_mode, "worlds")
            self.assertIsNotNone(master_datapack)
            with open(master_datapack, "rb") as handle:
                master_bytes = handle.read()
            expected = [
                os.path.join(
                    directory,
                    "saves",
                    world_name,
                    "datapacks",
                    os.path.basename(master_datapack),
                )
                for world_name in ("World One", "World Two")
            ]
            self.assertEqual(
                writer.datapack_installed_paths,
                [str(Path(path).resolve()) for path in expected],
            )
            for path in expected:
                with open(path, "rb") as handle:
                    self.assertEqual(handle.read(), master_bytes)
                with zipfile.ZipFile(path) as archive:
                    self.assertIn(entry, archive.namelist())
            self.assertFalse(
                os.path.exists(
                    os.path.join(
                        directory,
                        "saves",
                        "Not A World",
                        "datapacks",
                    )
                )
            )
            with open(kubejs_file, "rb") as handle:
                self.assertEqual(handle.read(), b"original")

    def test_world_datapack_install_rolls_back_every_world_on_copy_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            for world_name in ("A", "B"):
                world = os.path.join(directory, "saves", world_name)
                os.makedirs(world)
                with open(os.path.join(world, "level.dat"), "wb") as handle:
                    handle.write(b"world")

            writer = PackWriter(directory, "Rollback", "1.21.1", "Russian")
            writer.write("data/example/books/page.json", b"translated")
            archive_name = os.path.basename(writer.dp_zip_path)
            first_target = os.path.join(
                directory, "saves", "A", "datapacks", archive_name
            )
            second_target = os.path.join(
                directory, "saves", "B", "datapacks", archive_name
            )
            os.makedirs(os.path.dirname(first_target))
            with open(first_target, "wb") as handle:
                handle.write(b"previous")

            calls = 0

            def fail_second_copy(path, payload):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("copy failed")
                atomic_write_bytes(path, payload)

            with mock.patch(
                "mineai.output.pack_writer.atomic_write_bytes",
                side_effect=fail_second_copy,
            ):
                with self.assertRaises(OSError):
                    writer.close()

            with open(first_target, "rb") as handle:
                self.assertEqual(handle.read(), b"previous")
            self.assertFalse(os.path.exists(second_target))
            self.assertEqual(writer.datapack_installed_paths, [])

    def test_kubejs_is_never_used_as_a_datapack_target(self):
        with tempfile.TemporaryDirectory() as directory:
            mods = os.path.join(directory, "mods")
            os.makedirs(mods)
            with zipfile.ZipFile(
                os.path.join(mods, "renamed-script-engine.jar"), "w"
            ) as archive:
                archive.writestr(
                    "META-INF/neoforge.mods.toml",
                    '[[mods]]\nmodId = "kubejs"\n',
                )

            writer = PackWriter(directory, "Pagan Guide", "1.21.1", "Russian")
            writer.write(
                "data/paganbless/modonomicon/books/pagan_guide/book.json",
                '{"name":"Языческое руководство"}'.encode("utf-8"),
            )

            resourcepack, datapack = writer.close()

            forbidden_target = os.path.join(
                directory,
                "kubejs",
                "data",
                "paganbless",
                "modonomicon",
                "books",
                "pagan_guide",
                "book.json",
            )
            self.assertIsNone(resourcepack)
            self.assertIsNotNone(datapack)
            self.assertEqual(writer.datapack_install_mode, "manual")
            self.assertEqual(writer.datapack_installed_paths, [])
            self.assertFalse(os.path.exists(forbidden_target))
            with zipfile.ZipFile(datapack) as archive:
                self.assertIn(
                    "data/paganbless/modonomicon/books/pagan_guide/book.json",
                    archive.namelist(),
                )
            self.assertFalse(
                os.path.exists(os.path.join(directory, "config", "openloader"))
            )

    def test_openloader_is_not_used_as_a_datapack_target(self):
        with tempfile.TemporaryDirectory() as directory:
            mods = os.path.join(directory, "mods")
            os.makedirs(mods)
            with zipfile.ZipFile(
                os.path.join(mods, "renamed-data-loader.jar"), "w"
            ) as archive:
                archive.writestr(
                    "META-INF/mods.toml",
                    '[[mods]]\nmodId = "openloader"\n',
                )

            writer = PackWriter(directory, "Book", "1.20.1", "Russian")
            writer.write("data/example/books/page.json", b"{}")

            _resourcepack, datapack = writer.close()

            self.assertEqual(writer.datapack_install_mode, "manual")
            self.assertIsNotNone(datapack)
            self.assertTrue(
                os.path.normpath(datapack).startswith(
                    os.path.normpath(
                        os.path.join(directory, "MineAI_Datapacks")
                    )
                )
            )
            self.assertFalse(
                os.path.exists(os.path.join(directory, "config", "openloader"))
            )
            with zipfile.ZipFile(datapack) as archive:
                self.assertIn("data/example/books/page.json", archive.namelist())

    def test_datapack_without_loader_is_kept_for_manual_world_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = PackWriter(directory, "Book", "1.21.1", "Russian")
            writer.write("data/example/books/page.json", b"{}")

            _resourcepack, datapack = writer.close()

            self.assertEqual(writer.datapack_install_mode, "manual")
            self.assertIsNotNone(datapack)
            self.assertTrue(
                os.path.normpath(datapack).startswith(
                    os.path.normpath(
                        os.path.join(directory, "MineAI_Datapacks")
                    )
                )
            )
            self.assertFalse(
                os.path.exists(os.path.join(directory, "config", "openloader"))
            )

    def test_kubejs_files_stay_unchanged_if_final_pack_validation_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            mods = os.path.join(directory, "mods")
            os.makedirs(mods)
            with zipfile.ZipFile(os.path.join(mods, "kubejs.jar"), "w") as archive:
                archive.writestr(
                    "META-INF/neoforge.mods.toml",
                    '[[mods]]\nmodId = "kubejs"\n',
                )
            target = os.path.join(
                directory, "kubejs", "data", "example", "books", "page.json"
            )
            os.makedirs(os.path.dirname(target))
            with open(target, "wb") as handle:
                handle.write(b"original")
            writer = PackWriter(directory, "Book", "1.21.1", "Russian")
            writer.write("data/example/books/page.json", b"translated")
            writer.write("assets/example/lang/ru_ru.json", b"{}")

            with mock.patch.object(
                writer, "_validate_zip", side_effect=zipfile.BadZipFile("broken")
            ):
                with self.assertRaises(zipfile.BadZipFile):
                    writer.close()

            with open(target, "rb") as handle:
                self.assertEqual(handle.read(), b"original")
            self.assertEqual(writer.datapack_installed_paths, [])

    def test_pack_rejects_data_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            mods = os.path.join(directory, "mods")
            os.makedirs(mods)
            with zipfile.ZipFile(os.path.join(mods, "kubejs.jar"), "w") as archive:
                archive.writestr(
                    "META-INF/neoforge.mods.toml",
                    '[[mods]]\nmodId = "kubejs"\n',
                )
            writer = PackWriter(directory, "Unsafe", "1.21.1", "Russian")
            with self.assertRaises(ValueError):
                writer.write("data/../../outside.json", b"unsafe")

            writer.abort()

            self.assertFalse(os.path.exists(os.path.join(directory, "outside.json")))

    def test_embedded_and_shorthand_lang_paths_become_resourcepack_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = PackWriter(directory, "Paths", "1.21.1", "Russian")
            writer.write(
                "packs/tooltips/assets/aether/lang/ru_ru.json",
                '{"aether.tip": "Подсказка"}'.encode("utf-8"),
            )
            writer.write(
                "data/codechickenlib/lang/ru_ru.json",
                '{"ccl.info": "Информация"}'.encode("utf-8"),
            )
            writer.write(
                "ae2ct/lang/ru_ru.json",
                '{"ae2ct.tree": "Дерево"}'.encode("utf-8"),
            )

            resourcepack, datapack = writer.close()

            self.assertIsNone(datapack)
            with zipfile.ZipFile(resourcepack) as archive:
                self.assertIn("assets/aether/lang/ru_ru.json", archive.namelist())
                self.assertIn(
                    "assets/codechickenlib/lang/ru_ru.json",
                    archive.namelist(),
                )
                self.assertIn("assets/ae2ct/lang/ru_ru.json", archive.namelist())

    def test_duplicate_locale_outputs_merge_without_losing_unique_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = PackWriter(directory, "Combined", "1.21.1", "Russian")
            target = "assets/minecraft/lang/ru_ru.json"
            writer.write(
                target,
                json.dumps({"menu.play": "Играть", "shared": "Первый"}).encode(
                    "utf-8"
                ),
            )
            writer.write(
                target,
                json.dumps({"menu.quit": "Выйти", "shared": "Второй"}).encode(
                    "utf-8"
                ),
            )

            resourcepack, _datapack = writer.close()

            with zipfile.ZipFile(resourcepack) as archive:
                merged = json.loads(archive.read(target))
            self.assertEqual(
                merged,
                {
                    "menu.play": "Играть",
                    "shared": "Второй",
                    "menu.quit": "Выйти",
                },
            )

    def test_created_resource_pack_is_enabled_with_highest_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            options = os.path.join(directory, "options.txt")
            with open(options, "w", encoding="utf-8", newline="") as handle:
                handle.write(
                    'resourcePacks:["vanilla","file/Older.zip"]\r\n'
                    "incompatibleResourcePacks:[]\r\n"
                )
            writer = PackWriter(directory, "MineAI New", "1.21.1", "Russian")
            writer.write("assets/example/lang/ru_ru.json", b"{}")

            resourcepack, _datapack = writer.close()

            self.assertIsNotNone(resourcepack)
            with open(options, encoding="utf-8", newline="") as handle:
                saved = handle.read()
            self.assertIn(
                'resourcePacks:["vanilla", "file/Older.zip", "file/MineAI New.zip"]',
                saved,
            )
            self.assertNotIn("\n", saved.replace("\r\n", ""))
            self.assertTrue(writer.resourcepack_enabled)

    def test_datapack_only_output_does_not_change_resource_pack_options(self):
        with tempfile.TemporaryDirectory() as directory:
            options = os.path.join(directory, "options.txt")
            original = 'resourcePacks:["vanilla"]\n'
            with open(options, "w", encoding="utf-8") as handle:
                handle.write(original)
            writer = PackWriter(directory, "Quest Pack", "1.21.1", "Russian")
            writer.write("data/example/quests/ru_ru.snbt", b"{}")

            writer.close()

            with open(options, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), original)
            self.assertFalse(writer.resourcepack_enabled)

    def test_empty_new_packs_are_removed_without_touching_existing_pack(self):
        with tempfile.TemporaryDirectory() as directory:
            resourcepacks = os.path.join(directory, "resourcepacks")
            os.makedirs(resourcepacks)
            existing = os.path.join(resourcepacks, "MineAI_Pack.zip")
            with open(existing, "wb") as handle:
                handle.write(b"existing-pack")

            writer = PackWriter(directory, "MineAI_Pack", "1.20.1", "Russian")
            outputs = writer.close()

            with open(existing, "rb") as handle:
                self.assertEqual(handle.read(), b"existing-pack")
            self.assertEqual(outputs, (None, None))
            self.assertFalse(
                os.path.exists(os.path.join(resourcepacks, "MineAI_Pack_1.zip"))
            )

    def test_close_keeps_only_pack_with_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = PackWriter(directory, "MineAI_Pack", "1.20.1", "Russian")
            writer.write("assets/example/lang/ru_ru.json", b"{}")

            resourcepack, datapack = writer.close()

            self.assertIsNotNone(resourcepack)
            self.assertIsNone(datapack)
            self.assertTrue(os.path.exists(resourcepack))
            with zipfile.ZipFile(resourcepack) as archive:
                self.assertEqual(
                    set(archive.namelist()),
                    {"pack.mcmeta", "assets/example/lang/ru_ru.json"},
                )


if __name__ == "__main__":
    unittest.main()
