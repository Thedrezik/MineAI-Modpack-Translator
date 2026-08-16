import json
import os
import shutil
import tempfile
import unittest
import zipfile
from types import SimpleNamespace

from mineai.constants import LANGUAGES
from mineai.formatkit_books_bridge import build_book_output, plan_book_work
from mineai.processors.formatkit_books_pilot import FormatKitBooksJarProcessor
from mineai_formatkit import PatchouliBookJsonAdapter

RU = LANGUAGES["Русский"]


class PatchouliTemplateAdapterV31Tests(unittest.TestCase):
    path = "assets/ars_nouveau/patchouli_books/worn_notebook/en_us/templates/glyph_recipe.json"

    def test_processor_variables_are_never_exposed(self):
        source = json.dumps({
            "processor": "example.Processor",
            "components": [
                {"type": "patchouli:text", "text": "#tier#", "x": 0, "y": 105},
                {"type": "patchouli:text", "text": "#mana_cost#", "x": 0, "y": 115},
                {"type": "patchouli:text", "text": "#schools#", "x": 0, "y": 125},
                {"type": "patchouli:header", "text": "block.ars_nouveau.scribes_table", "x": -1, "y": -1},
            ],
        }, separators=(",", ":"))
        adapter = PatchouliBookJsonAdapter()
        plan = adapter.prepare(self.path, source)
        self.assertEqual(plan.units, ())
        self.assertTrue(plan.metadata.get("patchouli_template_immutable"))
        self.assertEqual(adapter.apply(plan, {}), source)
        self.assertEqual(
            adapter.target_path(self.path, "ru_ru"),
            "assets/ars_nouveau/patchouli_books/worn_notebook/ru_ru/templates/glyph_recipe.json",
        )

    def test_current_sdk_keeps_even_literal_template_text_immutable(self):
        path = "data/silentgear/patchouli_books/gears_guide/en_us/templates/compounding_recipe.json"
        source = json.dumps({
            "processor": "example.Processor",
            "components": [
                {"type": "header", "text": "#title", "x": 4, "y": 0},
                {"type": "item", "item": "#item1", "x": 10, "y": 10},
                {"type": "text", "text": "Result:", "x": 70, "y": 20},
            ],
        }, separators=(",", ":"))
        adapter = PatchouliBookJsonAdapter()
        plan = adapter.prepare(path, source)
        self.assertEqual(plan.units, ())
        self.assertEqual(adapter.apply(plan, {}), source)

    def test_corrupted_existing_template_is_never_reused(self):
        source = json.dumps({
            "processor": "example.Processor",
            "components": [
                {"type": "patchouli:text", "text": "#tier#", "x": 0, "y": 105},
                {"type": "patchouli:text", "text": "#mana_cost#", "x": 0, "y": 115},
            ],
        }, separators=(",", ":"))
        corrupted = json.dumps({
            "processor": "example.Processor",
            "components": [
                {"type": "patchouli:text", "text": "#ярус#", "x": 0, "y": 105},
                {"type": "patchouli:text", "text": "#стоимость_маны#", "x": 0, "y": 115},
            ],
        }, ensure_ascii=False, separators=(",", ":"))
        work = plan_book_work(self.path, source, "ru_ru", RU["regex"], corrupted, "append")
        assert work is not None
        self.assertEqual(work.adapter_name, "patchouli-template-json")
        self.assertEqual(work.total_translatable, 0)
        self.assertTrue(work.emit_structural_copy)
        self.assertEqual(build_book_output(work, {}), source)

    def test_mixed_variable_text_is_copied_instead_of_guessed(self):
        source = json.dumps({
            "components": [{"type": "patchouli:text", "text": "Cost: #cost", "x": 0, "y": 0}]
        })
        adapter = PatchouliBookJsonAdapter()
        plan = adapter.prepare(self.path, source)
        self.assertEqual(plan.units, ())
        self.assertEqual(adapter.apply(plan, {}), source)


class PatchouliTemplateProcessorV31Tests(unittest.TestCase):
    def test_zero_unit_template_is_still_copied_to_target_locale(self):
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp_dir)
        jar = os.path.join(temp_dir, "templates.jar")
        path = "assets/ars_nouveau/patchouli_books/worn_notebook/en_us/templates/glyph_recipe.json"
        source = json.dumps({
            "processor": "example.Processor",
            "components": [{"type": "patchouli:text", "text": "#tier#", "x": 0, "y": 105}],
        }, separators=(",", ":"))
        with zipfile.ZipFile(jar, "w") as z:
            z.writestr(path, source)

        class Service:
            config = SimpleNamespace(getboolean=lambda *_args, **_kwargs: True)
            def translate_dict(self, *_args, **_kwargs):
                raise AssertionError("zero-unit template must not call the LLM")

        class Writer:
            def __init__(self): self.writes = {}
            def write(self, path, payload): self.writes[path] = payload

        writer = Writer()
        processor = FormatKitBooksJarProcessor(
            Service(),
            SimpleNamespace(should_run=lambda: True),
            SimpleNamespace(on_log=lambda *_args: None),
        )
        with zipfile.ZipFile(jar) as z:
            locale = {e.filename.lower(): e for e in z.infolist()}
            ok = processor._process_book_json(
                z, None, z.getinfo(path), locale, RU, "append",
                "resourcepack", writer, "Ars Nouveau", set(),
            )
        self.assertTrue(ok)
        target = path.replace("/en_us/", "/ru_ru/")
        self.assertEqual(writer.writes[target].decode("utf-8"), source)


if __name__ == "__main__":
    unittest.main()
