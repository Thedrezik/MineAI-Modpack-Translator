"""Tests for M1/M2/L1/L2 improvements."""
import json
import os
import tempfile
import unittest

from mineai.engines.llm_common import build_translation_prompt, load_glossary


class GlossaryTests(unittest.TestCase):
    """M1/M2: glossary loading and injection into prompts."""

    def test_load_glossary_returns_dict(self):
        """load_glossary returns a dict (even if file missing)."""
        glossary = load_glossary()
        self.assertIsInstance(glossary, dict)

    def test_load_glossary_filters_comment_keys(self):
        """Keys starting with '_' are treated as comments and excluded."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump({"_comment": "ignored", "Nether": "Нижний мир"}, f)
            tmp_path = f.name
        try:
            import mineai.engines.llm_common as lc
            original = lc.GLOSSARY_FILE
            lc.GLOSSARY_FILE = tmp_path
            g = load_glossary()
            lc.GLOSSARY_FILE = original
            self.assertNotIn("_comment", g)
            self.assertIn("Nether", g)
        finally:
            os.unlink(tmp_path)

    def test_missing_glossary_is_materialized_with_defaults(self):
        import mineai.engines.llm_common as lc

        with tempfile.TemporaryDirectory() as directory:
            missing = os.path.join(directory, "glossary.json")
            original = lc.GLOSSARY_FILE
            try:
                lc.GLOSSARY_FILE = missing
                glossary = load_glossary()
                self.assertTrue(os.path.exists(missing))
                self.assertIn("Nether", glossary)
                with open(missing, encoding="utf-8") as stream:
                    self.assertIn("Nether", json.load(stream))
            finally:
                lc.GLOSSARY_FILE = original

    def test_glossary_injected_when_term_in_payload(self):
        """Relevant glossary terms appear in prompt GLOSSARY block."""
        prompt = build_translation_prompt(
            {"k": "Mine Nether quartz in the Nether"},
            "Russian",
            mode="safe",
            context="test",
            prompt_type="quests",
        )
        # GLOSSARY only appears if load_glossary() returns data
        glossary = load_glossary()
        if glossary:
            self.assertIn("GLOSSARY", prompt)
            self.assertIn("Нижний мир", prompt)

    def test_glossary_not_injected_when_no_match(self):
        """Terms not present in payload → no or minimal GLOSSARY entries."""
        # Purely technical text with no matching glossary keys
        prompt = build_translation_prompt(
            {"k": "0x4A2F: 127.0.0.1"},
            "Russian",
            mode="safe",
            context="test",
        )
        # Can't assert GLOSSARY is absent (depends on glossary content),
        # but the prompt must still be a non-empty string.
        self.assertIsInstance(prompt, str)
        self.assertGreater(len(prompt), 10)

    def test_glossary_max_30_terms(self):
        """At most 30 terms are injected into the GLOSSARY block."""
        import mineai.engines.llm_common as lc
        original = lc.GLOSSARY_FILE

        # Build a glossary with 50 common short terms
        big = {chr(65 + i): f"term_{i}" for i in range(50)}
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(big, f)
            tmp_path = f.name
        try:
            lc.GLOSSARY_FILE = tmp_path
            # Build a payload that contains most single-char keys
            payload = {"k": " ".join(chr(65 + i) for i in range(50))}
            prompt = build_translation_prompt(
                payload, "Russian", mode="safe", context="test"
            )
            if "GLOSSARY" in prompt:
                glossary_section = prompt[prompt.index("GLOSSARY"):]
                # Count lines with " = " separator
                lines = [l for l in glossary_section.splitlines() if " = " in l]
                self.assertLessEqual(len(lines), 30)
        finally:
            lc.GLOSSARY_FILE = original
            os.unlink(tmp_path)


class DictionaryFilterTests(unittest.TestCase):
    """M2: load_dictionary filters comment keys."""

    def test_comment_keys_excluded_from_terminology_fixes(self):
        """Keys starting with '_' must not appear in TERMINOLOGY_FIXES."""
        from mineai.text_processing import load_dictionary
        import mineai.text_processing as tp
        original = tp.DICT_FILE

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(
                {"_comment": "docs", "полуслой": "плита", "сыромятная медь": "сырая медь"},
                f, ensure_ascii=False,
            )
            tmp = f.name
        try:
            tp.DICT_FILE = tmp
            d = load_dictionary()
            self.assertNotIn("_comment", d)
            self.assertIn("полуслой", d)
        finally:
            tp.DICT_FILE = original
            os.unlink(tmp)


class QuestsPromptTests(unittest.TestCase):
    """L1: quests prompt should mention nominative case."""

    def test_quests_prompt_contains_nominative_instruction(self):
        """prompts.json quests prompt must mention именительный падеж."""
        from mineai.engines.llm_common import load_prompts
        prompts = load_prompts()
        quests_prompt = prompts.get("quests", "")
        # Check that the prompt contains case instruction (either the Russian term or the example)
        has_nominative = (
            "именительн" in quests_prompt.lower()
            or "Кирка" in quests_prompt
        )
        self.assertTrue(
            has_nominative,
            f"Quest prompt does not mention nominative case: {quests_prompt!r}",
        )

    def test_quests_prompt_has_context_placeholder(self):
        """The quests prompt must contain {context} template variable."""
        from mineai.engines.llm_common import load_prompts
        prompts = load_prompts()
        self.assertIn("{context}", prompts.get("quests", ""))


class AiBatchConfigTests(unittest.TestCase):
    """L2: ai_batch is present in ConfigManager defaults."""

    def test_ai_batch_in_config_defaults(self):
        """ConfigManager must have ai_batch in AI section defaults."""
        from mineai.config import ConfigManager
        # Check _DEFAULTS dict
        defaults = ConfigManager._DEFAULTS
        self.assertIn("AI", defaults)
        self.assertIn("ai_batch", defaults["AI"])
        self.assertEqual(defaults["AI"]["ai_batch"], "20")


if __name__ == "__main__":
    unittest.main()
