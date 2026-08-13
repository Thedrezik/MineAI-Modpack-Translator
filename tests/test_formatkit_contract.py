import unittest

from formatkit import FormatRegistry, FormatValidationError


class FormatKitContractTests(unittest.TestCase):
    def test_existing_target_fragments_become_immutable_anchors(self) -> None:
        source = "Existing paragraph\nNew paragraph\n"
        target = "Сохранённый абзац\nNew paragraph\n"
        source_plan = self.registry.plan("assets/demo/guide/en_us/a.md", source, "ru_ru")
        target_plan = self.registry.plan("assets/demo/guide/ru_ru/a.md", target, "ru_ru")

        merged, pending = source_plan.merge_existing(
            target_plan,
            r"[А-Яа-яЁё]",
        )

        self.assertEqual(pending, frozenset({source_plan.units[0].id}))
        self.assertNotIn("Сохранённый абзац", merged.units[0].payload)
        translated = merged.units[0].payload.replace(
            "New paragraph",
            "Новый абзац",
        )
        result = merged.apply({merged.units[0].id: translated})
        self.assertEqual(result.text, "Сохранённый абзац\nНовый абзац\n")

    def setUp(self) -> None:
        self.registry = FormatRegistry.default()

    def test_noop_round_trip_is_byte_exact(self) -> None:
        source = "# Heading\r\n\r\nPlain English text.\r\n"
        plan = self.registry.plan(
            "assets/example/manual/en_us/page.md",
            source,
            "ru_ru",
        )

        result = plan.apply({})

        self.assertEqual(result.text, source)
        self.assertTrue(result.validation.ok)
        self.assertTrue(result.validation.source_fingerprint)
        self.assertEqual(
            result.validation.source_fingerprint,
            result.validation.target_fingerprint,
        )

    def test_noop_preserves_trailing_space_before_protected_newline(self) -> None:
        source = "First wrapped line \nsecond wrapped line.\n"
        plan = self.registry.plan(
            "assets/ae2/ae2guide/wrapped.md",
            source,
            "ru_ru",
        )

        result = plan.apply({})

        self.assertEqual(result.text, source)

    def test_apply_rejects_missing_protected_anchor(self) -> None:
        source = 'The <ItemLink id="ae2:chest" /> is useful.\n'
        plan = self.registry.plan(
            "assets/ae2/ae2guide/getting-started.md",
            source,
            "ru_ru",
        )
        unit = plan.units[0]
        self.assertIn("FK", unit.payload)

        with self.assertRaises(FormatValidationError):
            plan.apply({unit.id: "Это полезно."})

    def test_unit_ranges_are_ordered_and_non_overlapping(self) -> None:
        source = "# First\n\nOne paragraph.\n\n# Second\n"
        plan = self.registry.plan(
            "assets/example/manual/en_us/page.md",
            source,
            "ru_ru",
        )

        previous_end = 0
        for unit in plan.units:
            self.assertGreaterEqual(unit.start, previous_end)
            self.assertGreater(unit.end, unit.start)
            previous_end = unit.end

    def test_resilient_apply_keeps_good_units_and_restores_only_bad_unit(self) -> None:
        source = "First paragraph.\n\nSecond paragraph.\n"
        plan = self.registry.plan(
            "assets/example/manual/en_us/page.md",
            source,
            "ru_ru",
        )
        translations = {
            plan.units[0].id: "Первый абзац.",
            plan.units[1].id: "[Повреждённая ссылка](wrong.md)",
        }

        result, rejected = plan.apply_resilient(translations)

        self.assertEqual(result.text, "Первый абзац.\n\nSecond paragraph.\n")
        self.assertEqual(set(rejected), {plan.units[1].id})
        self.assertTrue(result.validation.ok)

    def test_apply_restores_source_whitespace_at_protected_newlines(self) -> None:
        source = (
            "Tunnels move [channels](channels.md)\n"
            "without interacting with the network.\n"
        )
        plan = self.registry.plan(
            "assets/ae2/ae2guide/items/p2p.md",
            source,
            "ru_ru",
        )
        unit = plan.units[0]
        translated = unit.payload.replace(
            "Tunnels move ",
            "Туннели перемещают ",
        ).replace(
            "channels",
            "каналы",
        ).replace(
            "without interacting with the network.",
            " без прямого взаимодействия с сетью.",
        )

        result = plan.apply({unit.id: translated})

        self.assertEqual(
            result.text,
            "Туннели перемещают [каналы](channels.md)\n"
            "без прямого взаимодействия с сетью.\n",
        )
        self.assertTrue(result.validation.ok)


if __name__ == "__main__":
    unittest.main()
