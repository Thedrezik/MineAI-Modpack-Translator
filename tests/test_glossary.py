import json
import tempfile
import unittest
from pathlib import Path

from mineai.cache import TranslationCache
from mineai.glossary import GlossaryEntry, SmartGlossary
from mineai.text_processing import mask_protected_fragments, unmask_translation


def write_glossary(path: Path, entries: list[dict], **extra) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "language": "ru_ru",
        "entries": entries,
        **extra,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class SmartGlossaryTests(unittest.TestCase):
    def test_loads_files_and_user_rule_wins(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_glossary(
                root / "ru_ru" / "generated.json",
                [{"source": "Pin", "target": "Контакт", "scope": ["ftbquests"]}],
            )
            write_glossary(
                root / "ru_ru" / "user.json",
                [
                    {
                        "source": "Pin",
                        "target": "Моя кнопка",
                        "scope": ["*"],
                        "priority": 100,
                    }
                ],
            )

            glossary = SmartGlossary.load(root=root)

            self.assertEqual("Моя кнопка", glossary.exact_translation("Pin", "ftbquests"))
            self.assertEqual(1, glossary.source_counts["generated"])
            self.assertEqual(1, glossary.source_counts["user"])

    def test_higher_priority_wins_within_same_source(self) -> None:
        glossary = SmartGlossary(
            entries=[
                GlossaryEntry("Pin", "Контакт", priority=10, origin="user"),
                GlossaryEntry("Pin", "Закрепить", priority=20, origin="user"),
            ]
        )
        self.assertEqual("Закрепить", glossary.exact_translation("Pin", "unknown"))

    def test_duplicate_rules_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            duplicate = {
                "source": "Pin",
                "target": "Закрепить",
                "scope": ["ftbquests"],
            }
            write_glossary(
                root / "ru_ru" / "user.json",
                [duplicate, duplicate],
            )

            glossary = SmartGlossary.load(root=root)

            self.assertTrue(
                any("дублирующееся правило" in warning for warning in glossary.warnings)
            )

    def test_exact_respects_scope_case_and_whole_string(self) -> None:
        glossary = SmartGlossary(
            entries=[
                GlossaryEntry(
                    "Pin",
                    "Закрепить",
                    scope=("ftbquests",),
                    apply="exact",
                    case_sensitive=True,
                )
            ]
        )
        self.assertEqual("Закрепить", glossary.exact_translation("Pin", "ftbquests"))
        self.assertIsNone(glossary.exact_translation("pin", "ftbquests"))
        self.assertIsNone(glossary.exact_translation("Iron Pin", "ftbquests"))
        self.assertIsNone(glossary.exact_translation("Pin", "create"))

    def test_longest_phrase_is_masked_first(self) -> None:
        glossary = SmartGlossary(
            entries=[
                GlossaryEntry("Press", "Пресс", apply="phrase"),
                GlossaryEntry("Mechanical Press", "Механический пресс", apply="phrase"),
            ]
        )
        masked, mapping = mask_protected_fragments(
            "Use the Mechanical Press and Press",
        )
        masked, count = glossary.mask_terms(masked, mapping, "*")

        self.assertEqual(2, count)
        self.assertEqual(
            "Use the Механический пресс and Пресс",
            unmask_translation(masked, mapping),
        )

    def test_protect_restores_original_name(self) -> None:
        glossary = SmartGlossary(
            entries=[GlossaryEntry("Create", "Create", apply="protect", case_sensitive=True)]
        )
        masked, mapping = mask_protected_fragments("Install Create today")
        masked, _ = glossary.mask_terms(masked, mapping, "*")
        self.assertNotIn("Create", masked)
        self.assertEqual("Install Create today", unmask_translation(masked, mapping))

    def test_does_not_replace_inside_technical_fragments(self) -> None:
        glossary = SmartGlossary(
            entries=[GlossaryEntry("item", "предмет", apply="phrase")]
        )
        masked, mapping = mask_protected_fragments("Use <item:create:press> item %s $(item)")
        masked, count = glossary.mask_terms(masked, mapping, "*")
        restored = unmask_translation(masked, mapping)

        self.assertEqual(1, count)
        self.assertEqual("Use <item:create:press> предмет %s $(item)", restored)

    def test_corrupt_and_unsupported_files_do_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "ru_ru"
            root.mkdir(parents=True)
            (root / "generated.json").write_text("{broken", encoding="utf-8")
            (root / "user.json").write_text(
                json.dumps({"schema_version": 99, "language": "ru_ru", "entries": []}),
                encoding="utf-8",
            )

            glossary = SmartGlossary.load(root=root.parent)

            self.assertGreaterEqual(len(glossary.warnings), 2)
            self.assertGreater(len(glossary.entries), 0)

    def test_fingerprint_is_stable_and_changes_with_rules(self) -> None:
        first = SmartGlossary(entries=[GlossaryEntry("Pin", "Закрепить")])
        same = SmartGlossary(entries=[GlossaryEntry("Pin", "Закрепить")])
        changed = SmartGlossary(entries=[GlossaryEntry("Pin", "Контакт")])
        self.assertEqual(first.fingerprint, same.fingerprint)
        self.assertNotEqual(first.fingerprint, changed.fingerprint)


class TranslationCacheVariantTests(unittest.TestCase):
    def test_variant_namespaces_cache_and_empty_variant_is_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            cache = TranslationCache(str(Path(temp) / "cache.json"))
            cache.set("ru", "Pin", "Старый")
            cache.set("ru", "Pin", "Новый", variant="abcd1234")

            self.assertEqual("Старый", cache.get("ru", "Pin"))
            self.assertEqual("Новый", cache.get("ru", "Pin", variant="abcd1234"))
            self.assertIsNone(cache.get("ru", "Pin", variant="ffff0000"))
            self.assertEqual("ru_Pin", cache.make_key("ru", "Pin"))


if __name__ == "__main__":
    unittest.main()
