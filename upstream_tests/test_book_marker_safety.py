import unittest

from mineai_formatkit import ImmersiveEngineeringManualAdapter, PatchouliBookJsonAdapter, ValidationError


class BookMarkerSafetyTests(unittest.TestCase):
    def test_patchouli_chat_box_literal_dollar_is_protected(self) -> None:
        adapter = PatchouliBookJsonAdapter()
        path = "assets/advancedperipherals/patchouli_books/manual/en_us/entries/chat_box.json"
        source = (
            '{"name":"Chat Box","pages":[{"type":"patchouli:text",'
            '"text":"Start your message with a `$`.$(p)Read more."}]}'
        )
        plan = adapter.prepare(path, source)
        unit = next(unit for unit in plan.units if unit.id == "json:/pages/0/text")
        self.assertIn("`$`", [fragment.value for fragment in unit.protected])
        translated = unit.text.replace("Start your message with a ", "Начните сообщение с ").replace(
            "Read more.", "Подробнее."
        )
        output = adapter.apply(plan, {unit.id: translated})
        self.assertIn("`$`", output)
        self.assertNotIn("`$$`", output)

    def test_patchouli_reordered_protected_markers_fail(self) -> None:
        adapter = PatchouliBookJsonAdapter()
        path = "assets/demo/patchouli_books/guide/en_us/entries/a.json"
        source = '{"name":"A","pages":[{"text":"One $(9)two$() three"}]}'
        plan = adapter.prepare(path, source)
        unit = next(unit for unit in plan.units if unit.id == "json:/pages/0/text")
        markers = [fragment.placeholder for fragment in unit.protected]
        self.assertGreaterEqual(len(markers), 2)
        bad = unit.text.replace(markers[0], "TMP", 1).replace(markers[1], markers[0], 1).replace("TMP", markers[1], 1)
        with self.assertRaises(ValidationError):
            adapter.apply(plan, {unit.id: bad})

    def test_ie_reordered_protected_markers_fail(self) -> None:
        adapter = ImmersiveEngineeringManualAdapter()
        path = "assets/immersiveengineering/manual/en_us/demo.txt"
        source = "Hello §aGreen §bBlue\n"
        plan = adapter.prepare(path, source)
        unit = plan.units[0]
        markers = [fragment.placeholder for fragment in unit.protected]
        self.assertEqual(len(markers), 2)
        bad = unit.text.replace(markers[0], "TMP", 1).replace(markers[1], markers[0], 1).replace("TMP", markers[1], 1)
        with self.assertRaises(ValidationError):
            adapter.apply(plan, {unit.id: bad})


if __name__ == "__main__":
    unittest.main()
