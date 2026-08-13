import hashlib
import json
import os
import unittest
import zipfile

from mineai_formatkit.core import ValidationError
from mineai_formatkit.ftb_quests import FtbQuestsChapterAdapter, FtbQuestsLangAdapter


FTB_EVOLUTION_SHA256 = "55a553fe73a7003ae6e80228192f238acd8593a127e6818b923824e7fcdf3956"


class FtbQuestsLangAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = FtbQuestsLangAdapter()
        self.path = "config/ftbquests/quests/lang/en_us.snbt"
        self.source = r'''{
	chapter.001.title: "Welcome"
	quest.002.quest_desc: [
		"&aBuild&r a machine"
		""
		"{@pagebreak}"
		"[\"Use \", {\"text\": \"Orange\", \"color\": \"#FCA645\"}, \" Chalk\", {\"text\": \"Docs\", \"clickEvent\": {\"action\": \"open_url\", \"value\": \"https://example.invalid/docs\"}}]"
	]
	quest.002.quest_subtitle: "Press Ctrl + T and run /home for <username>"
}'''

    def test_matches_and_target_path(self) -> None:
        self.assertTrue(self.adapter.matches(self.path))
        self.assertFalse(self.adapter.matches("config/ftbquests/quests/data.snbt"))
        self.assertEqual(
            self.adapter.target_path(self.path, "ru_ru"),
            "config/ftbquests/quests/lang/ru_ru.snbt",
        )

    def test_identity_is_byte_exact(self) -> None:
        plan = self.adapter.prepare(self.path, self.source)
        output = self.adapter.apply(plan, {unit.id: unit.text for unit in plan.units})
        self.assertEqual(output, self.source)

    def test_description_structure_and_json_component_are_safe(self) -> None:
        plan = self.adapter.prepare(self.path, self.source)
        ids = {unit.id for unit in plan.units}
        self.assertIn("key:chapter.001.title", ids)
        self.assertIn("key:quest.002.quest_desc[0]", ids)
        self.assertNotIn("key:quest.002.quest_desc[1]", ids)
        self.assertNotIn("key:quest.002.quest_desc[2]", ids)
        json_ids = sorted(unit.id for unit in plan.units if "#json:" in unit.id)
        self.assertGreaterEqual(len(json_ids), 3)

        translations = {unit.id: "ТЕСТ " + unit.text for unit in plan.units}
        output = self.adapter.apply(plan, translations)
        self.assertEqual(self.adapter.fingerprint(output), self.adapter.fingerprint(self.source))
        self.assertIn("{@pagebreak}", output)
        self.assertIn("https://example.invalid/docs", output)
        self.assertIn("#FCA645", output)

    def test_protected_ftb_tokens_must_survive(self) -> None:
        plan = self.adapter.prepare(self.path, self.source)
        unit = next(unit for unit in plan.units if unit.id == "key:quest.002.quest_desc[0]")
        self.assertTrue(unit.protected)
        with self.assertRaises(ValidationError):
            self.adapter.apply(plan, {unit.id: "Перевод без плейсхолдера"})

    def test_translation_cannot_merge_physical_description_lines(self) -> None:
        plan = self.adapter.prepare(self.path, self.source)
        unit = next(unit for unit in plan.units if unit.id == "key:chapter.001.title")
        with self.assertRaises(ValidationError):
            self.adapter.apply(plan, {unit.id: "Добро\nпожаловать"})

    def test_duplicate_locale_keys_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self.adapter.prepare(self.path, '{\n\ta.title: "A"\n\ta.title: "B"\n}')


class FtbQuestsChapterAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = FtbQuestsChapterAdapter()
        self.path = "config/ftbquests/quests/chapters/example.snbt"
        self.source = r'''{
	filename: "example"
	quests: [{
		id: "0011223344556677"
		name: "technical_name"
		rewards: [{
			feedback_message: "Skills Have Been Reset"
			description: "Fed The Beast"
			item: {
				components: {
					"minecraft:custom_name": "\"Sugar Rush\""
					"minecraft:lore": ["{\"extra\":[{\"text\":\"Used to Activate The Pyramid\",\"italic\":false}],\"text\":\"\"}"]
				}
				id: "minecraft:sugar"
			}
		}]
	}]
}'''

    def test_chapter_adapter_only_extracts_allowlisted_visible_fields(self) -> None:
        plan = self.adapter.prepare(self.path, self.source)
        self.assertEqual(len(plan.units), 4)
        self.assertNotIn("technical_name", {unit.text for unit in plan.units})
        self.assertFalse(any("minecraft:sugar" in unit.text for unit in plan.units))

    def test_chapter_identity_and_synthetic_reconstruction(self) -> None:
        plan = self.adapter.prepare(self.path, self.source)
        identity = self.adapter.apply(plan, {unit.id: unit.text for unit in plan.units})
        self.assertEqual(identity, self.source)
        output = self.adapter.apply(
            plan, {unit.id: "ТЕСТ " + unit.text for unit in plan.units}
        )
        self.assertEqual(self.adapter.fingerprint(output), self.adapter.fingerprint(self.source))
        self.assertIn('id: "minecraft:sugar"', output)
        self.assertIn('name: "technical_name"', output)


class FtbEvolutionFullCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pack = os.environ.get("MINEAI_FORMATKIT_FTB_EVOLUTION_PACK")
        if not cls.pack:
            raise unittest.SkipTest("MINEAI_FORMATKIT_FTB_EVOLUTION_PACK is not set")
        with open(cls.pack, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        if digest != FTB_EVOLUTION_SHA256:
            raise unittest.SkipTest("FTB Evolution corpus SHA-256 does not match 1.41.1")

    def test_full_locale_identity_and_synthetic_reconstruction(self) -> None:
        adapter = FtbQuestsLangAdapter()
        path = "overrides/config/ftbquests/quests/lang/en_us.snbt"
        with zipfile.ZipFile(self.pack) as archive:
            source = archive.read(path).decode("utf-8")
        plan = adapter.prepare(path, source)
        self.assertEqual(len(adapter._parse_entries(source)), 4499)
        self.assertEqual(sum(len(entry.values) for entry in adapter._parse_entries(source)), 10263)
        self.assertEqual(len(plan.units), 7615)
        self.assertEqual(adapter.apply(plan, {u.id: u.text for u in plan.units}), source)
        translated = adapter.apply(plan, {u.id: "ТЕСТ " + u.text for u in plan.units})
        self.assertEqual(adapter.fingerprint(translated), adapter.fingerprint(source))

    def test_existing_ru_is_not_a_structural_source_of_truth(self) -> None:
        adapter = FtbQuestsLangAdapter()
        base = "overrides/config/ftbquests/quests/lang/"
        with zipfile.ZipFile(self.pack) as archive:
            en = archive.read(base + "en_us.snbt").decode("utf-8")
            ru = archive.read(base + "ru_ru.snbt").decode("utf-8")
        en_entries = {entry.key: entry for entry in adapter._parse_entries(en)}
        ru_entries = {entry.key: entry for entry in adapter._parse_entries(ru)}
        self.assertEqual(len(en_entries), 4499)
        self.assertEqual(len(ru_entries), 4198)
        self.assertEqual(len(set(en_entries) - set(ru_entries)), 302)
        self.assertEqual(len(set(ru_entries) - set(en_entries)), 1)
        shape_mismatches = [
            key
            for key in set(en_entries) & set(ru_entries)
            if (en_entries[key].kind, len(en_entries[key].values))
            != (ru_entries[key].kind, len(ru_entries[key].values))
        ]
        self.assertEqual(len(shape_mismatches), 6)

    def test_all_chapters_identity_round_trip(self) -> None:
        adapter = FtbQuestsChapterAdapter()
        prefix = "overrides/config/ftbquests/quests/chapters/"
        total_units = 0
        files = 0
        with zipfile.ZipFile(self.pack) as archive:
            for path in sorted(
                name for name in archive.namelist() if name.startswith(prefix) and name.endswith(".snbt")
            ):
                source = archive.read(path).decode("utf-8")
                plan = adapter.prepare(path, source)
                self.assertEqual(
                    adapter.apply(plan, {u.id: u.text for u in plan.units}),
                    source,
                    path,
                )
                translated = adapter.apply(
                    plan, {u.id: "ТЕСТ " + u.text for u in plan.units}
                )
                self.assertEqual(adapter.fingerprint(translated), adapter.fingerprint(source), path)
                total_units += len(plan.units)
                files += 1
        self.assertEqual(files, 40)
        self.assertEqual(total_units, 60)


if __name__ == "__main__":
    unittest.main()
