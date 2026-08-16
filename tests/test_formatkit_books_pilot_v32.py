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
from mineai.formatkit_books_bridge import (
    plan_book_work,
    validate_book_candidate,
)
from mineai.processors.formatkit_books_pilot import FormatKitBooksJarProcessor
from mineai_formatkit import PatchouliBookJsonAdapter

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
        self.calls = []

    def translate_batch(self, items, target_lang, callbacks):
        self.calls.append(tuple(items))
        return {
            key: self.responses.get(item.original, item.original)
            for key, item in items.items()
        }


class _Service(TranslationService):
    def __init__(self, engine, cache):
        super().__init__("ai", cache, _Config(), ai_batch=20)
        self.engine = engine

    def _build_engine(self, context="", prompt_type="mods"):
        return self.engine


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


class FormatKitPerUnitFallbackV32Tests(unittest.TestCase):
    patchouli_path = (
        "assets/apotheosis/patchouli_books/apoth_chronicle/en_us/"
        "entries/adventure/attributes/cold_damage.json"
    )

    def _make_jar(self, path, source):
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp_dir)
        jar = os.path.join(temp_dir, "book.jar")
        with zipfile.ZipFile(jar, "w") as archive:
            archive.writestr(path, source)
        return jar

    def _run_processor(self, path, source, responses, cache=None):
        cache = cache or _MemoryCache()
        logs = []
        writer = _Writer()
        service = _Service(_Engine(responses), cache)
        processor = FormatKitBooksJarProcessor(
            service,
            SimpleNamespace(should_run=lambda: True),
            EngineCallbacks(
                should_run=lambda: True,
                wait_if_paused=lambda: None,
                on_log=lambda text, color: logs.append((text, color)),
                on_status=lambda _message: None,
                on_progress=lambda _count: None,
            ),
        )
        jar = self._make_jar(path, source)
        with zipfile.ZipFile(jar) as archive:
            locale = {entry.filename.lower(): entry for entry in archive.infolist()}
            item = archive.getinfo(path)
            if path.endswith(".json"):
                ok = processor._process_book_json(
                    archive, None, item, locale, RU, "force", "resourcepack",
                    writer, "TestMod", set(),
                )
            else:
                ok = processor._process_book_md(
                    archive, None, item, locale, RU, "force", "resourcepack",
                    writer, "TestMod", set(),
                )
        return ok, writer, cache, logs

    def test_bad_patchouli_newline_falls_back_per_unit_and_file_is_written(self):
        source = json.dumps(
            {
                "name": "Cold Damage",
                "pages": [
                    {
                        "type": "patchouli:text",
                        "text": "Cold Damage is bonus magic damage.",
                    }
                ],
            },
            separators=(",", ":"),
        )
        ok, writer, cache, logs = self._run_processor(
            self.patchouli_path,
            source,
            {
                "Cold Damage": "Холод\nПовреждение",
                "Cold Damage is bonus magic damage.": "Холодный урон — дополнительный магический урон.",
            },
        )
        self.assertTrue(ok)
        target = self.patchouli_path.replace("/en_us/", "/ru_ru/")
        self.assertIn(target, writer.writes)
        output = json.loads(writer.writes[target].decode("utf-8"))
        # Only the unsafe unit falls back; the rest of the file is retained.
        self.assertEqual(output["name"], "Cold Damage")
        self.assertEqual(
            output["pages"][0]["text"],
            "Холодный урон — дополнительный магический урон.",
        )
        self.assertNotIn(("ru", "Cold Damage"), cache.values)
        self.assertIn(
            ("ru", "Cold Damage is bonus magic damage."),
            cache.values,
        )
        self.assertTrue(any("FormatKit:" in message for message, _ in logs))
        self.assertFalse(any("отклонил реконструкцию книги" in message for message, _ in logs))

    def test_bad_formatkit_cache_entry_is_discarded_before_reuse(self):
        source = json.dumps(
            {
                "name": "Cold Damage",
                "pages": [{"type": "patchouli:text", "text": "Cold damage text."}],
            },
            separators=(",", ":"),
        )
        work = plan_book_work(
            self.patchouli_path, source, "ru_ru", RU["regex"], None, "force"
        )
        assert work is not None
        cache = _MemoryCache()
        cache.values[("ru", "Cold Damage")] = "Холод\nПовреждение"
        engine = _Engine(
            {
                "Cold Damage": "Холодный урон",
                "Cold damage text.": "Текст о холодном уроне.",
            }
        )
        service = _Service(engine, cache)
        logs = []
        result = service.translate_dict(
            dict(work.pending),
            RU,
            _callbacks(logs),
            candidate_validator=lambda unit_id, candidate: validate_book_candidate(
                work, unit_id, candidate
            ),
            preserve_source_structure=True,
        )
        self.assertEqual(result["json:/name"], "Холодный урон")
        self.assertIn(("ru", "Cold Damage", False), cache.discarded)
        self.assertEqual(cache.values[("ru", "Cold Damage")], "Холодный урон")
        self.assertTrue(any("кэша отброшена" in message for message, _ in logs))

    def test_ie_newline_falls_back_only_for_bad_line_and_manual_is_written(self):
        path = "assets/immersiveengineering/manual/en_us/circuit_breakers.txt"
        source = "Circuit Breakers\nClap on - Clap off\nKeep the network safe.\n"
        ok, writer, cache, logs = self._run_processor(
            path,
            source,
            {
                "Circuit Breakers": "Автоматические выключатели",
                "Clap on - Clap off": "Хлопок\nвыключает",
                "Keep the network safe.": "Поддерживайте безопасность сети.",
            },
        )
        self.assertTrue(ok)
        target = path.replace("/en_us/", "/ru_ru/")
        output = writer.writes[target].decode("utf-8")
        self.assertEqual(output.count("\n"), source.count("\n"))
        self.assertEqual(
            output.splitlines(),
            [
                "Автоматические выключатели",
                "Clap on - Clap off",
                "Поддерживайте безопасность сети.",
            ],
        )
        self.assertNotIn(("ru", "Clap on - Clap off"), cache.values)
        self.assertFalse(any("отклонил реконструкцию книги" in message for message, _ in logs))

    def test_introduced_literal_backslashes_are_rejected_before_cache(self):
        path = "assets/productivebees/patchouli_books/guide/en_us/entries/cubee.json"
        source = json.dumps(
            {"name": "CuBee", "pages": [{"type": "patchouli:text", "text": "A copper bee."}]},
            separators=(",", ":"),
        )
        ok, writer, cache, _logs = self._run_processor(
            path,
            source,
            {
                "CuBee": r"Ф\е\р\о",
                "A copper bee.": "Медная пчела.",
            },
        )
        self.assertTrue(ok)
        target = path.replace("/en_us/", "/ru_ru/")
        output = json.loads(writer.writes[target].decode("utf-8"))
        self.assertEqual(output["name"], "CuBee")
        self.assertEqual(output["pages"][0]["text"], "Медная пчела.")
        self.assertNotIn(("ru", "CuBee"), cache.values)


class PatchouliStableFingerprintV32Tests(unittest.TestCase):
    def test_fingerprint_is_schema_based_not_translation_prose_classification(self):
        path = "assets/example/patchouli_books/guide/en_us/entries/test.json"
        source = json.dumps(
            {
                "name": "Cold Damage",
                "description": "Long source description",
                "pages": [{"type": "patchouli:text", "text": "Visible prose here"}],
            },
            separators=(",", ":"),
        )
        from mineai.formatkit_books_bridge import book_adapter_for

        adapter = book_adapter_for(path)
        assert adapter is not None
        plan = adapter.prepare(path, source)
        # A one-character target does not satisfy the adapter's prose heuristic,
        # but that must not change which structural field locations exist.
        output = adapter.apply(plan, {"json:/name": "Я"})
        parsed = json.loads(output)
        self.assertEqual(parsed["name"], "Я")
        adapter.validate(source, output)


if __name__ == "__main__":
    unittest.main()
