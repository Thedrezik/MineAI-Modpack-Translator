import json
import os
import tempfile
import unittest
from unittest import mock
import zipfile

from mineai.cache import TranslationCache
from mineai.io_utils import atomic_write_bytes
from mineai.output.pack_writer import PackWriter


class AtomicWriteTests(unittest.TestCase):
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
    def test_existing_pack_is_preserved_and_new_pack_gets_unique_name(self):
        with tempfile.TemporaryDirectory() as directory:
            resourcepacks = os.path.join(directory, "resourcepacks")
            os.makedirs(resourcepacks)
            existing = os.path.join(resourcepacks, "MineAI_Pack.zip")
            with open(existing, "wb") as handle:
                handle.write(b"existing-pack")

            writer = PackWriter(directory, "MineAI_Pack", "1.20.1", "Russian")
            writer.close()

            with open(existing, "rb") as handle:
                self.assertEqual(handle.read(), b"existing-pack")
            self.assertEqual(
                writer.rp_zip_path,
                os.path.join(resourcepacks, "MineAI_Pack_1.zip"),
            )
            with zipfile.ZipFile(writer.rp_zip_path) as archive:
                self.assertIn("pack.mcmeta", archive.namelist())


if __name__ == "__main__":
    unittest.main()
