from __future__ import annotations

import hashlib
import unittest
import zipfile
from pathlib import Path

from mineai_formatkit.core import ValidationError
from mineai_formatkit.guideme import GuideMeMarkdownAdapter


FIXTURES = Path(__file__).parent / "fixtures"
REFERENCE_ZIP = FIXTURES / "ae2guide-original-reference.zip"
REFERENCE_SHA = FIXTURES / "ae2guide-original-reference.sha256"
SAMPLES = FIXTURES / "original_samples"
BROKEN = FIXTURES / "broken_samples"


def _sample_paths() -> list[Path]:
    return sorted(SAMPLES.rglob("*.md"))


def _reference_names(suffix: str) -> list[str]:
    if not REFERENCE_ZIP.exists():
        return []
    with zipfile.ZipFile(REFERENCE_ZIP) as archive:
        return sorted(name for name in archive.namelist() if name.endswith(suffix))


def _reference_text(name: str) -> str:
    with zipfile.ZipFile(REFERENCE_ZIP) as archive:
        return archive.read("ae2guide/" + name).decode("utf-8")


class GuideMePathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = GuideMeMarkdownAdapter()

    def test_direct_guideme_page_is_source(self) -> None:
        self.assertTrue(self.adapter.matches("assets/ae2/ae2guide/getting-started.md"))

    def test_localized_guideme_subtrees_are_not_sources(self) -> None:
        self.assertFalse(self.adapter.matches("assets/ae2/ae2guide/_ru_ru/page.md"))
        self.assertFalse(self.adapter.matches("assets/ae2/ae2guide/ru_ru/page.md"))

    def test_guideme_target_uses_underscored_locale(self) -> None:
        self.assertEqual(
            self.adapter.target_path("assets/ae2/ae2guide/page.md", "ru_ru"),
            "assets/ae2/ae2guide/_ru_ru/page.md",
        )


class GuideMeExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = GuideMeMarkdownAdapter()

    def test_yaml_only_title_is_translatable(self) -> None:
        source = "---\nnavigation:\n  parent: root.md\n  title: Wireless Terminals\n  icon: terminal\n---\n"
        plan = self.adapter.prepare("ae2guide/page.md", source)
        self.assertEqual([unit.kind for unit in plan.units], ["yaml-title"])
        self.assertEqual(plan.units[0].text, "Wireless Terminals")

    def test_component_is_placeholder_but_sentence_stays_whole(self) -> None:
        source = '*   Use an <ItemLink id="interface" /> to store items.  \n'
        plan = self.adapter.prepare("ae2guide/page.md", source)
        self.assertEqual(len(plan.units), 1)
        unit = plan.units[0]
        self.assertEqual(unit.text, "Use an [#0#] to store items.")
        rebuilt = self.adapter.apply(
            plan,
            {unit.id: "Используйте [#0#] для хранения предметов."},
        )
        self.assertEqual(
            rebuilt,
            '*   Используйте <ItemLink id="interface" /> для хранения предметов.  \n',
        )

    def test_markdown_link_destination_is_protected(self) -> None:
        source = "See [Getting Started](getting-started.md) for details.\n"
        plan = self.adapter.prepare("ae2guide/page.md", source)
        unit = plan.units[0]
        self.assertIn("[#0#]", unit.text)
        rebuilt = self.adapter.apply(
            plan,
            {unit.id: "Смотрите [Начало работы]([#0#]) для подробностей."},
        )
        self.assertEqual(
            rebuilt,
            "Смотрите [Начало работы](getting-started.md) для подробностей.\n",
        )

    def test_table_is_translated_by_cells_not_by_row(self) -> None:
        source = "| Cell | Total Capacity | 8,128 |\n| --- | ---: | ---: |\n"
        plan = self.adapter.prepare("ae2guide/table.md", source)
        self.assertEqual([unit.text for unit in plan.units], ["Cell", "Total Capacity"])
        rebuilt = self.adapter.apply(
            plan,
            {plan.units[0].id: "Ячейка", plan.units[1].id: "Общая ёмкость"},
        )
        self.assertEqual(
            rebuilt,
            "| Ячейка | Общая ёмкость | 8,128 |\n| --- | ---: | ---: |\n",
        )

    def test_placeholder_loss_is_rejected(self) -> None:
        source = 'Use <ItemLink id="interface" /> here.\n'
        plan = self.adapter.prepare("ae2guide/page.md", source)
        with self.assertRaises(ValidationError):
            self.adapter.apply(plan, {plan.units[0].id: "Используйте интерфейс здесь."})

    def test_translator_cannot_merge_lines(self) -> None:
        source = "First paragraph\nSecond paragraph\n"
        plan = self.adapter.prepare("ae2guide/page.md", source)
        with self.assertRaises(ValidationError):
            self.adapter.apply(plan, {plan.units[0].id: "Первый\nВторой"})

    def test_literal_numeric_placeholder_cannot_collide_with_generated_one(self) -> None:
        source = 'Keep [#0#] and <ItemLink id="interface" /> safe.\n'
        plan = self.adapter.prepare("ae2guide/page.md", source)
        unit = plan.units[0]
        self.assertNotIn("[#0#]", unit.text)
        rebuilt = self.adapter.apply(plan, {unit.id: unit.text})
        self.assertEqual(rebuilt, source)

    def test_horizontal_rule_after_front_matter_does_not_reopen_yaml(self) -> None:
        source = "---\nnavigation:\n  title: Page title\n---\nBody text\n---\nMore body text\n"
        plan = self.adapter.prepare("ae2guide/page.md", source)
        self.assertEqual(
            [unit.text for unit in plan.units],
            ["Page title", "Body text", "More body text"],
        )

    def test_table_translation_cannot_add_a_pipe(self) -> None:
        source = "| Header |\n| --- |\n"
        plan = self.adapter.prepare("ae2guide/table.md", source)
        with self.assertRaises(ValidationError):
            self.adapter.apply(plan, {plan.units[0].id: "Заголовок | лишнее"})


class Ae2CuratedFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = GuideMeMarkdownAdapter()

    def test_curated_original_samples_identity_round_trip_byte_exact(self) -> None:
        samples = _sample_paths()
        self.assertGreaterEqual(len(samples), 5)
        for path in samples:
            rel = path.relative_to(SAMPLES).as_posix()
            with self.subTest(path=rel):
                text = path.read_text("utf-8")
                plan = self.adapter.prepare(rel, text)
                translations = {unit.id: unit.text for unit in plan.units}
                self.assertEqual(self.adapter.apply(plan, translations), text)

    def test_curated_original_samples_accept_synthetic_payload_changes_without_structure_drift(self) -> None:
        for path in _sample_paths():
            rel = path.relative_to(SAMPLES).as_posix()
            with self.subTest(path=rel):
                text = path.read_text("utf-8")
                plan = self.adapter.prepare(rel, text)
                translations = {unit.id: "RU " + unit.text for unit in plan.units}
                output = self.adapter.apply(plan, translations)
                self.assertEqual(
                    self.adapter.fingerprint(output),
                    self.adapter.fingerprint(text),
                )

    def test_known_broken_output_is_detected_as_structurally_different(self) -> None:
        original = (
            SAMPLES / "items-blocks-machines" / "wireless_terminals.md"
        ).read_text("utf-8")
        broken = (BROKEN / "wireless_terminals.md").read_text("utf-8")
        with self.assertRaises(ValidationError):
            self.adapter.validate(original, broken)


@unittest.skipUnless(
    REFERENCE_ZIP.exists(),
    "optional full AE2 reference corpus not present",
)
class Ae2FullCorpusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = GuideMeMarkdownAdapter()

    def test_reference_zip_matches_recorded_sha256(self) -> None:
        expected = REFERENCE_SHA.read_text("utf-8").split()[0]
        actual = hashlib.sha256(REFERENCE_ZIP.read_bytes()).hexdigest()
        self.assertEqual(actual, expected)

    def test_all_original_ae2_pages_identity_round_trip_byte_exact(self) -> None:
        pages = _reference_names(".md")
        self.assertEqual(len(pages), 125)
        for archive_name in pages:
            rel = archive_name.removeprefix("ae2guide/")
            with self.subTest(path=rel):
                text = _reference_text(rel)
                plan = self.adapter.prepare(rel, text)
                translations = {unit.id: unit.text for unit in plan.units}
                self.assertEqual(self.adapter.apply(plan, translations), text)

    def test_original_fixture_has_expected_service_assets(self) -> None:
        self.assertEqual(len(_reference_names(".md")), 125)
        self.assertEqual(len(_reference_names(".snbt")), 123)
        self.assertEqual(len(_reference_names(".png")), 40)

    def test_all_original_pages_accept_synthetic_payload_changes_without_structure_drift(self) -> None:
        for archive_name in _reference_names(".md"):
            rel = archive_name.removeprefix("ae2guide/")
            with self.subTest(path=rel):
                text = _reference_text(rel)
                plan = self.adapter.prepare(rel, text)
                translations = {unit.id: "RU " + unit.text for unit in plan.units}
                output = self.adapter.apply(plan, translations)
                self.assertEqual(
                    self.adapter.fingerprint(output),
                    self.adapter.fingerprint(text),
                )


if __name__ == "__main__":
    unittest.main()
