import json
import os
import shutil
import tempfile
import unittest
import zipfile
from types import SimpleNamespace

from mineai.constants import LANGUAGES
from mineai.engines.base import EngineCallbacks, TranslationEngine
from mineai.engines.service import TranslationService
from mineai.formatkit_books_bridge import plan_book_work, validate_book_candidate
from mineai.formatkit_bridge import (
    modonomicon_locale_adapter,
    plan_locale_work,
    validate_locale_candidate,
)
from mineai.processors.formatkit_books_pilot import (
    FormatKitBooksJarProcessor,
    FormatKitBooksStringEstimator,
    FormatKitModpackAnalyzer,
)
from mineai.runtime.state import JobState
from mineai_formatkit import ModonomiconBookJsonAdapter, ValidationError

RU = LANGUAGES["Русский"]


class _Config:
    def getboolean(self, section, key):
        if (section, key) == ("GENERAL", "smart_glue"):
            return True
        if (section, key) == ("AI", "fallback_google"):
            return False
        raise AssertionError((section, key))

    def getint(self, _section, _key, fallback=0):
        return fallback


class _MemoryCache:
    def __init__(self):
        self.values = {}
        self.identities = set()
        self.discarded = []

    def get(self, api_code, source):
        key = (api_code, source)
        if key in self.values:
            return self.values[key], False
        if key in self.identities:
            return source, False
        return None, False

    def set(self, api_code, source, translated):
        self.values[(api_code, source)] = translated

    def set_identity(self, api_code, source):
        self.identities.add((api_code, source))

    def discard(self, api_code, source, *, include_imported=False):
        self.values.pop((api_code, source), None)
        self.identities.discard((api_code, source))
        self.discarded.append((api_code, source, include_imported))

    def save_if_threshold(self):
        pass


class _Engine(TranslationEngine):
    def __init__(self, responses):
        self.responses = responses

    def translate_batch(self, items, target_lang, callbacks):
        return {
            key: self.responses.get(item.original, item.original)
            for key, item in items.items()
        }


class _Service(TranslationService):
    def __init__(self, responses):
        self.cache_for_test = _MemoryCache()
        super().__init__("ai", self.cache_for_test, _Config(), ai_batch=20)
        self.engine_for_test = _Engine(responses)

    def _build_engine(self, context="", prompt_type="mods"):
        return self.engine_for_test


class _Writer:
    def __init__(self):
        self.writes = {}

    def write(self, path, payload):
        self.writes[path] = payload


def _callbacks(logs):
    return EngineCallbacks(
        should_run=lambda: True,
        wait_if_paused=lambda: None,
        on_log=lambda message, tag: logs.append((message, tag)),
        on_status=lambda _message: None,
        on_progress=lambda _count: None,
    )


class FormatKitPilotV33Tests(unittest.TestCase):
    book_path = "data/demo/modonomicon/books/guide/entries/start.json"
    lang_path = "assets/demo/lang/en_us.json"

    def _make_jar(self, files):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root)
        jar = os.path.join(root, "demo.jar")
        with zipfile.ZipFile(jar, "w") as archive:
            for path, payload in files.items():
                archive.writestr(path, payload)
        return jar

    def test_modonomicon_book_translates_only_literal_prose(self):
        source = json.dumps(
            {
                "id": "demo:start",
                "name": "book.demo.start.name",
                "description": "Visible literal description",
                "pages": [
                    {
                        "type": "modonomicon:text",
                        "title": "Literal title",
                        "text": "Read [the next entry](entry://demo/next).",
                        "recipe": "demo:machine",
                    }
                ],
            },
            separators=(",", ":"),
        )
        adapter = ModonomiconBookJsonAdapter()
        plan = adapter.prepare(self.book_path, source)
        self.assertEqual(
            {unit.id for unit in plan.units},
            {"json:/description", "json:/pages/0/title", "json:/pages/0/text"},
        )
        text_unit = plan.by_id()["json:/pages/0/text"]
        self.assertIn("the next entry", text_unit.text)
        self.assertNotIn("entry://demo/next", text_unit.text)
        translated = {
            "json:/description": "Видимое описание",
            "json:/pages/0/title": "Заголовок",
            "json:/pages/0/text": text_unit.text.replace("Read", "Читайте").replace(
                "the next entry", "следующую запись"
            ),
        }
        output = json.loads(adapter.apply(plan, translated))
        self.assertEqual(output["id"], "demo:start")
        self.assertEqual(output["name"], "book.demo.start.name")
        self.assertEqual(output["pages"][0]["recipe"], "demo:machine")
        self.assertEqual(
            output["pages"][0]["text"],
            "Читайте [следующую запись](entry://demo/next).",
        )

    def test_modonomicon_candidate_rejects_marker_reorder_and_newline(self):
        source = json.dumps(
            {
                "pages": [
                    {
                        "type": "modonomicon:text",
                        "text": "Open [Guide](entry://demo/guide) and [Next](entry://demo/next).",
                    }
                ]
            },
            separators=(",", ":"),
        )
        work = plan_book_work(self.book_path, source, "ru_ru", RU["regex"], None, "force")
        self.assertIsNotNone(work)
        unit_id, masked = next(iter(work.pending.items()))
        markers = [fragment.placeholder for fragment in work.units_by_id[unit_id].protected]
        self.assertGreaterEqual(len(markers), 2)
        bad_order = masked.replace(markers[0], "__A__", 1)
        bad_order = bad_order.replace(markers[1], markers[0], 1)
        bad_order = bad_order.replace("__A__", markers[1], 1)
        ok, _reason = validate_book_candidate(work, unit_id, bad_order)
        self.assertFalse(ok)
        ok, reason = validate_book_candidate(work, unit_id, masked + "\nBAD")
        self.assertFalse(ok)
        self.assertIn("line-break", reason)

    def test_modonomicon_lenient_raw_newline_identity_is_lexically_preserved(self):
        source = '{"name":"Improved Anvil Smashing","pages":[{"type":"modonomicon:text","text":"First line\\\n\t\\\n\tSecond line"}],"id":"demo:anvil"}'
        adapter = ModonomiconBookJsonAdapter()
        plan = adapter.prepare(self.book_path, source)
        identity = {unit.id: unit.text for unit in plan.units}
        self.assertEqual(adapter.apply(plan, identity), source)
        text_unit = next(unit for unit in plan.units if unit.id == "json:/pages/0/text")
        with self.assertRaises(ValidationError):
            adapter.validate_candidate(plan, text_unit.id, text_unit.text + "\nBAD")

    def test_modonomicon_locale_preserves_runtime_markdown_and_marker_order(self):
        source = json.dumps(
            {
                "book.demo.start": "Open [Guide](entry://demo/guide) with %s",
                "ui.demo": "General UI",
            },
            separators=(",", ":"),
        )
        work = plan_locale_work(
            self.lang_path,
            source,
            "ru_ru",
            None,
            "force",
            adapter=modonomicon_locale_adapter(),
            key_filter=lambda key: key.startswith("book."),
        )
        self.assertIsNotNone(work)
        self.assertEqual(set(work.pending), {"key:book.demo.start"})
        masked = work.pending["key:book.demo.start"]
        placeholders = [fragment.placeholder for fragment in work.plan.source_plan.by_id()["key:book.demo.start"].protected]
        self.assertGreaterEqual(len(placeholders), 2)
        translated = masked.replace("Open", "Откройте").replace("Guide", "Руководство")
        ok, reason = validate_locale_candidate(work, "key:book.demo.start", translated)
        self.assertTrue(ok, reason)
        swapped = translated.replace(placeholders[0], "__A__", 1).replace(placeholders[1], placeholders[0], 1).replace("__A__", placeholders[1], 1)
        ok, _reason = validate_locale_candidate(work, "key:book.demo.start", swapped)
        self.assertFalse(ok)

    def test_layered_locale_protection_manifest_follows_source_occurrence_order(self):
        adapter = modonomicon_locale_adapter()
        source = json.dumps(
            {"book.demo.layered": "&7Before %s [Guide](entry://demo/guide) after"},
            separators=(",", ":"),
        )
        plan = adapter.prepare(self.lang_path, source)
        unit = plan.by_id()["key:book.demo.layered"]
        marker_order = [m.group(0) for m in __import__("re").finditer(r"\[#\d+#\]", unit.text)]
        self.assertEqual(marker_order, [fragment.placeholder for fragment in unit.protected])
        adapter.validate_candidate(plan, unit.id, unit.text.replace("Before", "До").replace("after", "после"))

    def test_books_only_processor_emits_modonomicon_locale_and_datapack_overlay(self):
        lang = json.dumps(
            {
                "book.demo.start.name": "Getting Started",
                "book.demo.start.text": "Open [Guide](entry://demo/guide).",
                "ui.demo": "Do not translate in books-only mode",
            },
            separators=(",", ":"),
        )
        book = json.dumps(
            {
                "name": "book.demo.start.name",
                "description": "Literal description",
                "id": "demo:start",
            },
            separators=(",", ":"),
        )
        jar = self._make_jar({self.lang_path: lang, self.book_path: book})
        service = _Service(
            {
                "Getting Started": "Начало работы",
                "Open [#0#]Guide[#1#].": "Откройте [#0#]Руководство[#1#].",
                "Literal description": "Литеральное описание",
            }
        )
        writer = _Writer()
        logs = []
        state = SimpleNamespace(should_run=lambda: True, wait_if_paused=lambda: None)
        processor = FormatKitBooksJarProcessor(service, state, _callbacks(logs))
        processor.process(
            jar,
            target_lang=RU,
            mode="force",
            output_mode="resourcepack",
            translate_mods=False,
            translate_books=True,
            pack_writer=writer,
        )
        self.assertIn("assets/demo/lang/ru_ru.json", writer.writes)
        self.assertIn(self.book_path, writer.writes)
        locale_out = json.loads(writer.writes["assets/demo/lang/ru_ru.json"].decode("utf-8"))
        data_out = json.loads(writer.writes[self.book_path].decode("utf-8"))
        self.assertEqual(locale_out["book.demo.start.name"], "Начало работы")
        self.assertEqual(locale_out["ui.demo"], "Do not translate in books-only mode")
        self.assertEqual(data_out["description"], "Литеральное описание")
        self.assertEqual(data_out["id"], "demo:start")
        self.assertTrue(any("Modonomicon Locale/FormatKit" in message for message, _ in logs))
        self.assertTrue(any("Modonomicon/FormatKit" in message for message, _ in logs))

    def test_modonomicon_analyzer_and_estimator_share_the_same_plan(self):
        lang = json.dumps(
            {
                "book.demo.name": "Guide Name",
                "book.demo.text": "Guide Text",
                "ui.demo": "UI Text",
            },
            separators=(",", ":"),
        )
        book = json.dumps(
            {"name": "book.demo.name", "description": "Literal page description", "id": "demo:start"},
            separators=(",", ":"),
        )
        jar = self._make_jar({self.lang_path: lang, self.book_path: book})
        state = JobState()
        state.start()
        estimator = FormatKitBooksStringEstimator(state)
        estimated = estimator._estimate_jar(
            jar, "ru_ru.json", RU, "force", False, True, False
        )
        rows = []
        analyzer = FormatKitModpackAnalyzer(state)
        analyzed = analyzer._analyze_jar(
            jar,
            "ru_ru.json",
            RU["regex"],
            False,
            True,
            lambda *row: rows.append(row),
            "Demo",
        )
        self.assertEqual(estimated, 3)
        self.assertEqual(analyzed, (3, 0))
        self.assertTrue(any("Modonomicon Locale/FormatKit" in row[2] for row in rows))
        self.assertTrue(any("Modonomicon/FormatKit" in row[2] for row in rows))


if __name__ == "__main__":
    unittest.main()
