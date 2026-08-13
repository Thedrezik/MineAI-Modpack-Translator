import json
import tempfile
import unittest
import zipfile

from mineai.constants import MC_VERSIONS, PACK_FORMATS
from mineai.output.pack_writer import PackWriter


class BetaAll40VersionTests(unittest.TestCase):
    def test_top_100_minecraft_versions_have_pack_formats(self):
        expected = {
            "1.7.10": {"rp": 1, "dp": 1},
            "1.21.11": {"rp": 75, "dp": 94},
            "26.1.2": {"rp": 84, "dp": 101.1},
            "26.2": {"rp": 88, "dp": 107.1},
        }

        for version, formats in expected.items():
            self.assertIn(version, MC_VERSIONS)
            self.assertEqual(PACK_FORMATS[version], formats)

    def test_resource_pack_uses_selected_26_2_format(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            writer = PackWriter(temp_dir, "BetaAll40", "26.2", "Russian")
            writer.write("assets/example/lang/ru_ru.json", b'{"example.key":"\xd0\xa2\xd0\xb5\xd1\x81\xd1\x82"}')
            resourcepack, _datapack = writer.close()

            self.assertIsNotNone(resourcepack)
            with zipfile.ZipFile(resourcepack) as archive:
                metadata = json.loads(archive.read("pack.mcmeta"))

            self.assertEqual(metadata["pack"]["pack_format"], 88)
