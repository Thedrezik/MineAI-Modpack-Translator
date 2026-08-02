import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from mineai.glossary_harvester import (
    GlossaryHarvester,
    is_safe_candidate,
    scope_from_locale_path,
)


class GlossaryHarvesterTests(unittest.TestCase):
    def test_safe_candidate_filter(self) -> None:
        self.assertTrue(is_safe_candidate("Mechanical Press", "Механический пресс"))
        self.assertFalse(is_safe_candidate("Value: %s", "Значение: %s"))
        self.assertFalse(is_safe_candidate("<item:create:press>", "<item:create:press>"))
        self.assertFalse(is_safe_candidate("A long complete sentence has too many words", "Длинная фраза"))
        self.assertFalse(is_safe_candidate("Create", "Create"))
        self.assertFalse(is_safe_candidate("Machine", "Machine"))

    def test_scope_from_path(self) -> None:
        self.assertEqual("create", scope_from_locale_path("assets/create/lang/en_us.json"))
        self.assertEqual("ftbquests", scope_from_locale_path("config/ftbquests/lang/en_us.json"))

    def test_collects_jar_locale_pairs_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            jar = Path(temp) / "create.jar"
            output = Path(temp) / "generated.json"
            with zipfile.ZipFile(jar, "w") as archive:
                archive.writestr(
                    "assets/create/lang/en_us.json",
                    json.dumps({"press": "Mechanical Press", "bad": "Value: %s"}),
                )
                archive.writestr(
                    "assets/create/lang/ru_ru.json",
                    json.dumps({"press": "Механический пресс", "bad": "Значение: %s"}),
                )
                archive.writestr(
                    "assets/create/book/en_us.json",
                    json.dumps({"ignored": "Book Name"}),
                )
                archive.writestr(
                    "assets/create/book/ru_ru.json",
                    json.dumps({"ignored": "Название книги"}),
                )

            harvester = GlossaryHarvester()
            harvester.collect_from_jars([str(jar)])
            stats = harvester.save_generated(output)
            payload = json.loads(output.read_text(encoding="utf-8"))

            self.assertEqual(1, stats.entries)
            self.assertEqual("Mechanical Press", payload["entries"][0]["source"])
            self.assertEqual(["create"], payload["entries"][0]["scope"])
            self.assertEqual("phrase", payload["entries"][0]["apply"])

    def test_collects_loose_pairs_by_matching_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lang_dir = root / "kubejs" / "assets" / "quests" / "lang"
            lang_dir.mkdir(parents=True)
            source = lang_dir / "en_us.json"
            target = lang_dir / "ru_ru.json"
            source.write_text(json.dumps({"a": "Quest Pin", "only_en": "Machine"}), encoding="utf-8")
            target.write_text(json.dumps({"a": "Метка задания", "only_ru": "Механизм"}), encoding="utf-8")

            harvester = GlossaryHarvester()
            harvester.collect_from_loose_json([str(source)], str(root))
            output = root / "generated.json"
            stats = harvester.save_generated(output)
            payload = json.loads(output.read_text(encoding="utf-8"))

            self.assertEqual(1, stats.entries)
            self.assertEqual(["quests"], payload["entries"][0]["scope"])

    def test_conflicting_variants_are_recorded_but_not_applied(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lang_dir = root / "assets" / "electronics" / "lang"
            lang_dir.mkdir(parents=True)
            source = lang_dir / "en_us.json"
            target = lang_dir / "ru_ru.json"
            source.write_text(json.dumps({"a": "Pin", "b": "Pin"}), encoding="utf-8")
            target.write_text(json.dumps({"a": "Контакт", "b": "Штифт"}), encoding="utf-8")

            harvester = GlossaryHarvester()
            harvester.collect_from_loose_json([str(source)], str(root))
            output = root / "generated.json"
            stats = harvester.save_generated(output)
            payload = json.loads(output.read_text(encoding="utf-8"))

            self.assertEqual(0, stats.entries)
            self.assertEqual(1, stats.conflicts)
            self.assertEqual([], payload["entries"])
            self.assertEqual("Pin", payload["conflicts"][0]["source"])
            self.assertEqual(2, len(payload["conflicts"][0]["variants"]))


if __name__ == "__main__":
    unittest.main()
