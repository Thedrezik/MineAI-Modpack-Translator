import unittest

from mineai_formatkit import ImmersiveEngineeringManualAdapter
from mineai_formatkit.core import TranslationPlan, TranslationUnit


class TranslationPlanInvariantTests(unittest.TestCase):
    @staticmethod
    def _unit(unit_id: str, start: int, end: int) -> TranslationUnit:
        return TranslationUnit(unit_id, "text", start, end, "test")

    def test_invalid_and_out_of_bounds_ranges_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid translation unit range"):
            TranslationPlan(
                path="demo.txt",
                source_text="abcdef",
                units=(self._unit("empty", 2, 2),),
            )
        with self.assertRaisesRegex(ValueError, "exceeds source"):
            TranslationPlan(
                path="demo.txt",
                source_text="abcdef",
                units=(self._unit("too-far", 4, 7),),
            )

    def test_alias_ids_and_overlapping_spans_remain_supported(self) -> None:
        plan = TranslationPlan(
            path="demo.txt",
            source_text="abcdef",
            units=(
                self._unit("alias", 0, 3),
                self._unit("alias", 2, 5),
            ),
        )
        self.assertEqual(len(plan.units), 2)
        self.assertEqual(plan.by_id()["alias"].start, 2)


class IeResetBoundaryTests(unittest.TestCase):
    def test_reset_code_owns_following_sentence_separator(self) -> None:
        adapter = ImmersiveEngineeringManualAdapter()
        source = "Use §2graphite electrodes§r. However, keep them charged.\n"
        plan = adapter.prepare(
            "assets/immersiveengineering/manual/en_us/railgun.txt",
            source,
        )
        unit = plan.units[0]
        self.assertIn("§r. ", [fragment.value for fragment in unit.protected])

        translated = unit.text.replace("Use ", "Используйте ").replace(
            "However, keep them charged.",
            "Однако держите их заряженными.",
        )
        output = adapter.apply(plan, {unit.id: translated})
        self.assertIn("§r. Однако", output)
        self.assertNotIn("§r.Однако", output)

    def test_reset_comma_boundary_is_source_owned(self) -> None:
        adapter = ImmersiveEngineeringManualAdapter()
        source = "Use §agreen§r, then continue.\n"
        plan = adapter.prepare(
            "assets/immersiveengineering/manual/en_us/demo.txt",
            source,
        )
        self.assertIn(
            "§r, ",
            [fragment.value for fragment in plan.units[0].protected],
        )


if __name__ == "__main__":
    unittest.main()
