import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from mineai_formatkit import (
    FormatRegistry,
    JarContainer,
    LocaleMergePlanner,
    MinecraftLangJsonAdapter,
    OracleIndexMdxAdapter,
    OracleIndexMetaJsonAdapter,
)
from mineai_formatkit.core import ValidationError


class LiveLocalePartOneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = "assets/demo/lang/en_us.json"

    def test_malformed_optional_target_falls_back_to_canonical_source(self) -> None:
        source = '{\n  "a": "Alpha",\n  "b": "Beta"\n}'
        broken_target = '{\n  // invalid target comment\n  "a": "Альфа"\n}'
        planner = LocaleMergePlanner()
        plan = planner.plan(
            self.path,
            source,
            "ru_ru",
            target_text=broken_target,
            mode="append",
        )
        self.assertIsNotNone(plan.target_parse_error)
        self.assertEqual(plan.existing_values, {})
        self.assertEqual(set(plan.pending_ids), {"key:a", "key:b"})
        output = planner.build(plan, {"key:a": "Альфа", "key:b": "Бета"})
        self.assertEqual(json.loads(output), {"a": "Альфа", "b": "Бета"})

    def test_force_mode_never_parses_optional_target(self) -> None:
        source = '{"a":"Alpha"}'
        plan = LocaleMergePlanner().plan(
            self.path,
            source,
            "ru_ru",
            target_text="this is not json",
            mode="force",
        )
        self.assertEqual(plan.pending_ids, ("key:a",))
        self.assertIsNone(plan.target_parse_error)

    def test_malum_codex_markup_is_protected_and_critical_for_reuse(self) -> None:
        source = '{"book":"Read $iimportant/$ text. $m0.8/$Scaled"}'
        adapter = MinecraftLangJsonAdapter()
        plan = adapter.prepare(self.path, source)
        self.assertEqual(len(plan.units), 1)
        unit = plan.units[0]
        protected = {fragment.value for fragment in unit.protected}
        self.assertIn("$i", protected)
        self.assertIn("/$", protected)
        self.assertIn("$m0.8/$", protected)
        self.assertNotIn("$i", unit.text)
        self.assertNotIn("$m0.8/$", unit.text)
        self.assertEqual(adapter.apply(plan, {unit.id: unit.text}), source)

        damaged_target = '{"book":"Читайте важный текст. Scaled"}'
        merge = LocaleMergePlanner().plan(
            self.path,
            source,
            "ru_ru",
            target_text=damaged_target,
            mode="append",
        )
        self.assertEqual(merge.invalid_existing_keys, ("book",))
        self.assertEqual(merge.pending_ids, ("key:book",))


class NestedJarPartOneTests(unittest.TestCase):
    def test_one_level_jar_in_jar_candidates_are_discoverable(self) -> None:
        inner_bytes = io.BytesIO()
        with zipfile.ZipFile(inner_bytes, "w", zipfile.ZIP_DEFLATED) as nested:
            nested.writestr("assets/demo/lang/en_us.json", '{"demo":"Demo"}')

        registry = FormatRegistry()
        registry.register(MinecraftLangJsonAdapter())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bundle.jar"
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as outer:
                outer.writestr("META-INF/jarjar/demo.jar", inner_bytes.getvalue())
                outer.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")

            container = JarContainer()
            shallow = container.inspect(path, registry)
            self.assertEqual(shallow.candidate_entries, ())
            self.assertEqual(shallow.nested_jar_entries, ("META-INF/jarjar/demo.jar",))

            nested = container.inspect_nested(path, registry)
            self.assertEqual(len(nested), 1)
            self.assertEqual(nested[0].entry_path, "META-INF/jarjar/demo.jar")
            self.assertEqual(
                nested[0].inspection.candidate_entries,
                ("assets/demo/lang/en_us.json",),
            )

    def test_invalid_nested_jar_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bundle.jar"
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as outer:
                outer.writestr("META-INF/jarjar/broken.jar", b"not a zip")
            with self.assertRaisesRegex(ValueError, "Nested JAR entry"):
                JarContainer().inspect_nested(path)


class OracleIndexPartOneTests(unittest.TestCase):
    def test_new_nested_meta_shape_translates_only_name_fields(self) -> None:
        adapter = OracleIndexMetaJsonAdapter()
        path = "assets/oracle_index/books/oracle-index/docs/_meta.json"
        source = '''{
  "user_guide.mdx": {"name": "User Guide", "icon": null},
  "search.mdx": {"name": "Smart Search", "icon": "oracle:index"},
  "developer": {"name": "Mod Dev Guide"}
}'''
        plan = adapter.prepare(path, source)
        self.assertEqual(len(plan.units), 3)
        self.assertEqual(adapter.apply(plan, {unit.id: unit.text for unit in plan.units}), source)
        translated = adapter.apply(
            plan,
            {unit.id: "RU " + unit.text for unit in plan.units},
        )
        parsed = json.loads(translated)
        self.assertEqual(parsed["user_guide.mdx"]["name"], "RU User Guide")
        self.assertIsNone(parsed["user_guide.mdx"]["icon"])
        self.assertEqual(parsed["search.mdx"]["icon"], "oracle:index")
        self.assertIn('"user_guide.mdx"', translated)

    def test_legacy_meta_shape_remains_supported(self) -> None:
        adapter = OracleIndexMetaJsonAdapter()
        source = '{"machines":"Machines","start.mdx":"Getting Started"}'
        plan = adapter.prepare("oracle_index/books/demo/.content/_meta.json", source)
        self.assertEqual(len(plan.units), 2)
        output = adapter.apply(plan, {unit.id: "RU " + unit.text for unit in plan.units})
        self.assertEqual(
            json.loads(output),
            {"machines": "RU Machines", "start.mdx": "RU Getting Started"},
        )

    def test_oracle_mdx_inherits_star_emphasis_safety(self) -> None:
        adapter = OracleIndexMdxAdapter()
        path = "assets/oracle_index/books/oracle-index/docs/page.mdx"
        source = "Use the *unlock* section and the **Oracle Progress API**.\n"
        plan = adapter.prepare(path, source)
        protected = [fragment.value for unit in plan.units for fragment in unit.protected]
        self.assertGreaterEqual(protected.count("*"), 2)
        self.assertGreaterEqual(protected.count("**"), 2)
        self.assertEqual(adapter.apply(plan, {unit.id: unit.text for unit in plan.units}), source)

        unit = plan.units[0]
        damaged = unit.text
        for fragment in unit.protected:
            damaged = damaged.replace(fragment.placeholder, "")
        with self.assertRaises(ValidationError):
            adapter.apply(plan, {unit.id: damaged})


if __name__ == "__main__":
    unittest.main()
