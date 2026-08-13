import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from formatkit import FormatRegistry
from mineai.engines.base import EngineCallbacks
from mineai.processors.book_paths import MarkdownBookLocator
from mineai.processors.analyzer import ModpackAnalyzer
from mineai.processors.estimator import StringEstimator
from mineai.processors.jar import JarProcessor
from mineai.runtime.state import JobState


TARGET_LANG = {
    "file": "ru_ru",
    "api": "ru",
    "name": "Russian",
    "regex": r"[\u0410-\u042f\u0430-\u044f\u0401\u0451]",
}
HERBALIST = "\u0421\u0442\u043e\u043b \u0442\u0440\u0430\u0432\u043d\u0438\u043a\u0430"
FINGERS = "\u0411\u0435\u0440\u0435\u0433\u0438\u0442\u0435 \u043f\u0430\u043b\u044c\u0446\u044b!"
CUT_HERBS = "\u041d\u0430\u0440\u0435\u0436\u044c\u0442\u0435 \u0442\u0440\u0430\u0432\u044b."
PAGAN_GUIDE = (
    "\u042f\u0437\u044b\u0447\u0435\u0441\u043a\u043e\u0435 "
    "\u0440\u0443\u043a\u043e\u0432\u043e\u0434\u0441\u0442\u0432\u043e"
)


class _Config:
    def getboolean(self, _section: str, _key: str) -> bool:
        return False


class _Service:
    def __init__(self) -> None:
        self.config = _Config()
        self.calls: list[dict[str, str]] = []

    def translate_dict(self, strings, _target_lang, _callbacks, **_kwargs):
        self.calls.append(dict(strings))
        replacements = {
            "Herbalist Bench": HERBALIST,
            "Watch your fingers!": FINGERS,
            "Cut the herbs.": CUT_HERBS,
            "Pagan Guide": PAGAN_GUIDE,
        }
        return {
            key: replacements.get(value, "Translation: " + value)
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


class SharedMarkdownBookTests(unittest.TestCase):
    def test_locator_uses_locale_convention_from_another_jar(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            base = root / "base.jar"
            addon = root / "addon.jar"
            with zipfile.ZipFile(base, "w") as archive:
                archive.writestr(
                    "assets/modern_industrialization/mi_guidebook/"
                    "_ru_ru/steam_age/coke_oven.md",
                    "# Existing translation",
                )
            with zipfile.ZipFile(addon, "w") as archive:
                archive.writestr(
                    "assets/modern_industrialization/mi_guidebook/"
                    "io_guide/pyrolyse_oven.md",
                    "# Pyrolyse Oven",
                )

            locator = MarkdownBookLocator.from_archives(
                [str(base), str(addon)],
                "ru_ru",
            )
            writer = _Writer()
            JarProcessor(_Service(), _state(), _callbacks()).process(
                str(addon),
                target_lang=TARGET_LANG,
                mode="force",
                output_mode="resourcepack",
                translate_mods=False,
                translate_books=True,
                pack_writer=writer,
                book_locator=locator,
            )

        self.assertEqual(
            locator.target_path(
                "assets/modern_industrialization/mi_guidebook/"
                "io_guide/pyrolyse_oven.md"
            ),
            "assets/modern_industrialization/mi_guidebook/"
            "_ru_ru/io_guide/pyrolyse_oven.md",
        )
        self.assertIn(
            "assets/modern_industrialization/mi_guidebook/"
            "_ru_ru/io_guide/pyrolyse_oven.md",
            writer.files,
        )


class ModonomiconAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = FormatRegistry.default()

    def test_pagan_multiline_json_is_planned_losslessly(self) -> None:
        source = (
            "{\n"
            '  "name": "Herbalist Bench",\n'
            '  "category": "paganbless:features",\n'
            '  "pages": [{\n'
            '    "type": "modonomicon:text",\n'
            '    "text": "Cut the herbs.   \\\\\n+      \\\\\n+      Keep your fingers safe."\n'
            "  }]\n"
            "}\n"
        )
        path = (
            "data/paganbless/modonomicon/books/pagan_guide/"
            "entries/features/herbalist_bench.json"
        )

        plan = self.registry.plan(path, source, "ru_ru")

        self.assertEqual(plan.adapter_id, "modonomicon-json-v1")
        self.assertEqual(plan.target_path, path)
        self.assertEqual(plan.apply({}).text, source)
        payloads = [unit.payload for unit in plan.units]
        self.assertTrue(any("Herbalist Bench" in value for value in payloads))
        self.assertTrue(any("Cut the herbs" in value for value in payloads))
        self.assertFalse(any("paganbless:features" in value for value in payloads))
        self.assertFalse(any("modonomicon:text" in value for value in payloads))

    def test_localization_keys_are_protected_and_reported(self) -> None:
        source = json.dumps(
            {
                "name": "book.occultism.dictionary.entry.name",
                "description": "book.occultism.dictionary.entry.description",
                "pages": [
                    {
                        "type": "modonomicon:text",
                        "text": "book.occultism.dictionary.entry.text",
                    }
                ],
            }
        )
        path = (
            "data/occultism/modonomicon/books/dictionary/entries/entry.json"
        )

        plan = self.registry.plan(path, source, "ru_ru")

        self.assertEqual(plan.units, ())
        self.assertEqual(
            self.registry.companion_lang_keys([(path, source)]),
            {
                "book.occultism.dictionary.entry.name",
                "book.occultism.dictionary.entry.description",
                "book.occultism.dictionary.entry.text",
            },
        )

    def test_localization_keys_with_resource_path_are_not_translated(self) -> None:
        key = (
            "book.geneticsresequenced.guide.genes."
            "geneticsresequenced/explosive_exit.page0.text"
        )
        source = json.dumps({"text": key})
        path = (
            "data/geneticsresequenced/modonomicon/books/guide/"
            "entries/genes/explosive_exit.json"
        )

        plan = self.registry.plan(path, source, "ru_ru")

        self.assertEqual(plan.units, ())
        self.assertEqual(
            self.registry.companion_lang_keys([(path, source)]),
            {key},
        )

    def test_malformed_document_does_not_hide_valid_companion_keys(self) -> None:
        valid_path = (
            "data/occultism/modonomicon/books/dictionary/entries/valid.json"
        )
        broken_path = (
            "data/occultism/modonomicon/books/dictionary/entries/broken.json"
        )
        documents = [
            (broken_path, '{"text": "unfinished'),
            (valid_path, '{"text": "book.occultism.valid.text"}'),
        ]

        self.assertEqual(
            self.registry.companion_lang_keys(documents),
            {"book.occultism.valid.text"},
        )


class ModonomiconJarIntegrationTests(unittest.TestCase):
    def test_lenient_multiline_source_is_written_as_strict_json(self) -> None:
        entry_path = (
            "data/paganbless/modonomicon/books/pagan_guide/"
            "entries/features/herbalist_bench.json"
        )
        source = (
            "{\n"
            '  "name": "Herbalist Bench",\n'
            '  "pages": [{"type": "modonomicon:text", '
            '"text": "Cut the herbs.   \\\\\n+      \\\\\n+      Keep your fingers safe."}]\n'
            "}\n"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            jar_path = Path(temp_dir) / "paganbless.jar"
            with zipfile.ZipFile(jar_path, "w") as archive:
                archive.writestr(entry_path, source)
            writer = _Writer()
            JarProcessor(_Service(), _state(), _callbacks()).process(
                str(jar_path),
                target_lang=TARGET_LANG,
                mode="force",
                output_mode="resourcepack",
                translate_mods=False,
                translate_books=True,
                pack_writer=writer,
            )

        output = json.loads(writer.files[entry_path])
        self.assertEqual(output["name"], HERBALIST)
        self.assertIn("Keep your fingers safe", output["pages"][0]["text"])

    def test_literal_pages_go_to_datapack_and_book_lang_keys_to_resourcepack(
        self,
    ) -> None:
        book_path = "data/paganbless/modonomicon/books/pagan_guide/book.json"
        entry_path = (
            "data/paganbless/modonomicon/books/pagan_guide/"
            "entries/features/herbalist_bench.json"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            jar_path = Path(temp_dir) / "paganbless.jar"
            with zipfile.ZipFile(jar_path, "w") as archive:
                archive.writestr(
                    book_path,
                    json.dumps(
                        {"name": "book.paganbless.pagan_guide.name"}
                    ),
                )
                archive.writestr(
                    entry_path,
                    json.dumps(
                        {
                            "name": "Herbalist Bench",
                            "description": "Watch your fingers!",
                            "pages": [
                                {
                                    "type": "modonomicon:text",
                                    "text": "Cut the herbs.",
                                }
                            ],
                        }
                    ),
                )
                archive.writestr(
                    "assets/paganbless/lang/en_us.json",
                    json.dumps(
                        {
                            "book.paganbless.pagan_guide.name": "Pagan Guide",
                            "item.paganbless.herb": "Herb",
                        }
                    ),
                )
            writer = _Writer()
            state = _state()
            estimated = StringEstimator(state).estimate(
                [str(jar_path)],
                [],
                [],
                [],
                target_lang=TARGET_LANG,
                mode="force",
                translate_mods=False,
                translate_books=True,
                translate_quests=False,
                smart_glue=False,
            )
            analyzed, _translated = ModpackAnalyzer(state)._analyze_jar(
                str(jar_path),
                "ru_ru.json",
                TARGET_LANG["regex"],
                False,
                True,
                lambda *_args: None,
                "Pagan Blessing",
            )
            JarProcessor(_Service(), state, _callbacks()).process(
                str(jar_path),
                target_lang=TARGET_LANG,
                mode="force",
                output_mode="resourcepack",
                translate_mods=False,
                translate_books=True,
                pack_writer=writer,
            )

        self.assertEqual(estimated, 4)
        self.assertEqual(analyzed, estimated)
        self.assertIn(entry_path, writer.files)
        output = json.loads(writer.files[entry_path])
        self.assertEqual(output["name"], HERBALIST)
        self.assertEqual(output["description"], FINGERS)
        self.assertEqual(output["pages"][0]["text"], CUT_HERBS)
        lang_path = "assets/paganbless/lang/ru_ru.json"
        self.assertIn(lang_path, writer.files)
        lang = json.loads(writer.files[lang_path])
        self.assertEqual(
            lang,
            {"book.paganbless.pagan_guide.name": PAGAN_GUIDE},
        )


if __name__ == "__main__":
    unittest.main()
