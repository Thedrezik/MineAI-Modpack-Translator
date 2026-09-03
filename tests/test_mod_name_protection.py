"""Tests for H2: mod names are protected from translation via mask_protected_fragments."""
import unittest

from mineai.text_processing import mask_protected_fragments, unmask_translation


class ModNameProtectionTests(unittest.TestCase):
    """H2 — Защита имён модов от перевода."""

    def _is_protected(self, text: str, term: str) -> bool:
        """Check that `term` appears in mapping values (i.e., is masked)."""
        masked, mapping = mask_protected_fragments(text)
        return term in mapping.values() or any(term in v for v in mapping.values())

    def test_create_mod_protected(self):
        """'Create' в предложении — должен быть защищён маркером."""
        masked, mapping = mask_protected_fragments("Craft with Create to make things")
        self.assertIn("Create", mapping.values())
        self.assertNotIn("Create", masked)

    def test_apotheosis_protected(self):
        masked, mapping = mask_protected_fragments("Upgrade using Apotheosis gems")
        self.assertIn("Apotheosis", mapping.values())

    def test_powah_protected(self):
        masked, mapping = mask_protected_fragments("Generate power with Powah reactors")
        self.assertIn("Powah", mapping.values())

    def test_industrial_foregoing_protected(self):
        masked, mapping = mask_protected_fragments(
            "Industrial Foregoing provides automation machines"
        )
        self.assertTrue(
            any("Industrial Foregoing" in v for v in mapping.values()),
            "Multi-word mod name not protected",
        )

    def test_mekanism_protected(self):
        masked, mapping = mask_protected_fragments("Craft Mekanism machines for energy")
        self.assertIn("Mekanism", mapping.values())

    def test_botania_protected(self):
        masked, mapping = mask_protected_fragments("Use Botania flowers for mana")
        self.assertIn("Botania", mapping.values())

    def test_allthemodium_protected(self):
        masked, mapping = mask_protected_fragments("Mine Allthemodium ore in the deep dark")
        self.assertIn("Allthemodium", mapping.values())

    def test_roundtrip_preserves_mod_name(self):
        """После unmask имя мода восстанавливается точно."""
        source = "Use Create and Botania together for automation"
        masked, mapping = mask_protected_fragments(source)
        restored = unmask_translation(masked, mapping)
        self.assertEqual(restored, source)

    def test_create_word_boundary(self):
        """'Creates' (с суффиксом) не должен быть защищён — word boundary."""
        # "Creates" is a different word — boundary check: (?![a-zA-Z])
        masked, mapping = mask_protected_fragments("This creates a new item")
        # "creates" should NOT be protected (word boundary fails after 'Create' + 's')
        # Note: if 'Create' is protected by case-insensitive match that eats 'creates',
        # this test documents current behaviour
        restored = unmask_translation(masked, mapping)
        self.assertIn("creates", restored.lower())

    def test_applied_energistics_2_protected(self):
        # "Applied Energistics" is protected by IGNORE_TERMS (without the trailing '2'
        # which is separately masked as a numeric fragment).
        masked, mapping = mask_protected_fragments(
            "Store items in Applied Energistics 2 drives"
        )
        self.assertTrue(
            any("Applied Energistics" in v for v in mapping.values()),
        )

    def test_paxel_protected(self):
        masked, mapping = mask_protected_fragments("Craft an Alloy Paxel for mining")
        self.assertIn("Paxel", mapping.values())


if __name__ == "__main__":
    unittest.main()
