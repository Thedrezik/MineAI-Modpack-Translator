import hashlib
import json
import re
import unittest
from pathlib import Path

from mineai.constants import LANGUAGES
from mineai.formatkit_books_bridge import (
    build_book_output,
    plan_book_work,
    target_path_for_book,
)
from mineai.formatkit_bridge import (
    build_locale_output,
    locale_adapter_for,
    modonomicon_locale_adapter,
    plan_locale_work,
)
from mineai_formatkit import (
    FORMATKIT_SOURCE_SHA,
    FORMATKIT_VENDOR_BLOBS,
    ImmersiveEngineeringManualAdapter,
    ModonomiconBookJsonAdapter,
    PatchouliBookJsonAdapter,
)

RU = LANGUAGES["Русский"]
ROOT = Path(__file__).resolve().parents[1]


def _git_blob_sha(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload).hexdigest()


def _canonical_git_text_bytes(path: Path) -> bytes:
    # Git may materialize text files with CRLF on Windows even though the
    # repository blob is canonical LF. Verify the repository-equivalent text,
    # not checkout-specific line endings.
    text = path.read_text(encoding="utf-8")
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


class FormatKitSdkSyncV34Tests(unittest.TestCase):
    def test_active_sdk_modules_are_exactly_pinned_to_formatkit_commit(self):
        self.assertEqual(FORMATKIT_SOURCE_SHA, "5cfd1e28c1581caf144f9a5ef767c631d9199f8c")
        sdk_root = ROOT / "mineai_formatkit"
        for filename, expected_blob in FORMATKIT_VENDOR_BLOBS.items():
            payload = _canonical_git_text_bytes(sdk_root / filename)
            self.assertEqual(
                _git_blob_sha(payload),
                expected_blob,
                f"vendored SDK module drifted: {filename}",
            )
        self.assertFalse((sdk_root / "patchouli_template.py").exists())

    def test_book_bridge_does_not_reach_into_sdk_private_parsers(self):
        source = (ROOT / "mineai" / "formatkit_books_bridge.py").read_text(encoding="utf-8")
        self.assertNotIn("._parse(", source)
        self.assertNotIn("._protect(", source)
        self.assertNotIn("PatchouliTemplateJsonAdapter", source)
        self.assertNotIn("validate_translation_candidate", source)

    def test_normal_locale_path_uses_one_modonomicon_aware_sdk_adapter(self):
        adapter = locale_adapter_for("assets/demo/lang/en_us.json")
        self.assertIs(adapter, modonomicon_locale_adapter())
        source = json.dumps(
            {"book.demo": "&7Open [Guide](entry://demo/guide) with %s"},
            separators=(",", ":"),
        )
        plan = adapter.prepare("assets/demo/lang/en_us.json", source)
        unit = plan.by_id()["key:book.demo"]
        marker_order = [m.group(0) for m in re.finditer(r"\[#\d+#\]", unit.text)]
        self.assertEqual(marker_order, [fragment.placeholder for fragment in unit.protected])

    def test_unsafe_existing_modonomicon_value_cannot_drop_the_whole_locale(self):
        path = "assets/occultism/lang/en_us.json"
        source = json.dumps(
            {
                "book.safe": "Visible safe text",
                "book.legacy": "Read **carefully** now",
                "screen.label": "Settings",
            },
            separators=(",", ":"),
        )
        target = json.dumps(
            {
                "book.safe": "Безопасный перевод",
                # Legacy target prose lost Modonomicon's source-owned emphasis.
                # The SDK planner can initially consider the value reusable,
                # but full reconstruction must reject it.
                "book.legacy": "Читайте внимательно",
                "screen.label": "Настройки",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        work = plan_locale_work(
            path,
            source,
            "ru_ru",
            target,
            "append",
            adapter=modonomicon_locale_adapter(),
            key_filter=lambda key: key.startswith("book."),
        )
        assert work is not None

        # Reproduces the runtime v3.4 failure: direct SDK build rejects one
        # reused legacy value and would otherwise make MineAI omit ru_ru.json.
        with self.assertRaisesRegex(ValueError, "Modonomicon lang markup changed"):
            work.planner.build(work.plan, {})

        output = json.loads(build_locale_output(work, {}))
        self.assertEqual(output["book.safe"], "Безопасный перевод")
        self.assertEqual(output["screen.label"], "Настройки")
        # Only the unsafe reused unit falls back to canonical source text.
        self.assertEqual(output["book.legacy"], "Read **carefully** now")


class PatchouliSemanticOwnershipV34Tests(unittest.TestCase):
    path = "assets/apotheosis/patchouli_books/apoth_chronicle/en_us/entries/adventure/affixes.json"

    def _source(self):
        return json.dumps(
            {
                "pages": [
                    {
                        "type": "patchouli:text",
                        "text": "An $(6)Affix$() is the driving force behind Affix Items.",
                    }
                ]
            },
            separators=(",", ":"),
        )

    def test_style_payload_is_a_semantic_child_and_old_flat_cache_shape_cannot_hit(self):
        work = plan_book_work(self.path, self._source(), "ru_ru", RU["regex"], None, "force")
        assert work is not None
        outer = next(unit for unit in work.source_plan.units if unit.kind == "patchouli-text")
        child = next(unit for unit in work.source_plan.units if unit.kind == "patchouli-semantic-child")
        self.assertEqual(child.text, "Affix")
        self.assertTrue(work.target_reuse_disabled)
        old_v33_cache_source = "An [#0#]Affix[#1#] is the driving force behind Affix Items."
        self.assertNotIn(old_v33_cache_source, work.pending.values())

        translated = {
            outer.id: outer.text.replace("An ", "").replace(
                " is the driving force behind Affix Items.",
                " — основа предметов с аффиксами.",
            ),
            child.id: "Аффикс",
        }
        output = json.loads(build_book_output(work, translated))
        text = output["pages"][0]["text"]
        self.assertIn("$(6)Аффикс$()", text)
        self.assertNotIn("$(6) — основа", text)

    def test_old_semantically_drifted_append_target_is_quarantined(self):
        bad_target = json.dumps(
            {
                "pages": [
                    {
                        "type": "patchouli:text",
                        "text": "Аффикс $(6) — это движущая сила предметов с аффиксами.$()",
                    }
                ]
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        work = plan_book_work(
            self.path,
            self._source(),
            "ru_ru",
            RU["regex"],
            bad_target,
            "append",
        )
        assert work is not None
        self.assertTrue(work.target_reuse_disabled)
        self.assertEqual(work.preserved, {})
        self.assertGreater(len(work.pending), 0)


class IeSemanticOwnershipV34Tests(unittest.TestCase):
    path = "assets/immersiveengineering/manual/en_us/wires.txt"

    def test_section_style_owns_only_the_original_visible_payload(self):
        source = "The §2left port§r is for inputting energy.\n"
        work = plan_book_work(self.path, source, "ru_ru", RU["regex"], None, "force")
        assert work is not None
        outer = next(unit for unit in work.source_plan.units if unit.kind == "ie-manual-prose")
        child = next(unit for unit in work.source_plan.units if unit.kind == "ie-format-semantic-child")
        translated = {
            outer.id: outer.text.replace("The ", "").replace(
                " is for inputting energy.", " используется для ввода энергии."
            ),
            child.id: "левый порт",
        }
        output = build_book_output(work, translated)
        self.assertIn("§2левый порт§r", output)
        self.assertNotIn("§2 используется", output)
        ImmersiveEngineeringManualAdapter().validate(source, output)


class ConservativeTemplateAndModonomiconV34Tests(unittest.TestCase):
    template_path = "assets/ars_nouveau/patchouli_books/worn_notebook/en_us/templates/glyph_recipe.json"

    def test_patchouli_template_is_owned_but_never_sent_to_llm(self):
        source = json.dumps(
            {
                "processor": "example.Processor",
                "components": [
                    {"type": "patchouli:text", "text": "#tier#"},
                    {"type": "patchouli:text", "text": "Result:"},
                ],
            },
            separators=(",", ":"),
        )
        adapter = PatchouliBookJsonAdapter()
        self.assertTrue(adapter.matches(self.template_path))
        plan = adapter.prepare(self.template_path, source)
        self.assertEqual(plan.units, ())
        self.assertTrue(plan.metadata.get("patchouli_template_immutable"))
        work = plan_book_work(
            self.template_path, source, "ru_ru", RU["regex"], None, "append"
        )
        assert work is not None
        self.assertEqual(work.adapter_name, "patchouli-template-json")
        self.assertTrue(work.emit_structural_copy)
        self.assertEqual(build_book_output(work, {}), source)

    def test_modonomicon_target_path_is_host_output_policy_not_sdk_parser_policy(self):
        path = "data/demo/modonomicon/books/guide/entries/start.json"
        adapter = ModonomiconBookJsonAdapter()
        with self.assertRaises(ValueError):
            adapter.target_path(path, "ru_ru")
        self.assertEqual(target_path_for_book(path, "ru_ru"), path)

    def test_gson_lenient_modonomicon_identity_is_preserved(self):
        path = "data/demo/modonomicon/books/guide/entries/start.json"
        source = '{"name":"Visible title","description":"First line\n\tSecond line"}'
        adapter = ModonomiconBookJsonAdapter()
        plan = adapter.prepare(path, source)
        identity = {unit.id: unit.text for unit in plan.units}
        self.assertEqual(adapter.apply(plan, identity), source)


if __name__ == "__main__":
    unittest.main()
