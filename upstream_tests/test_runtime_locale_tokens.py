import unittest

from mineai_formatkit import LocaleMergePlanner, MinecraftLangJsonAdapter, ValidationError


class RuntimeLocaleTokenTests(unittest.TestCase):
    def test_corpus_proven_runtime_tokens_are_protected(self) -> None:
        source = (
            '{"demo":"Use $$old_pos_x, %kkey.hexerei.glasses_zoom%, '
            '/create and https://docs.example.test/page; on/off stays prose"}'
        )
        adapter = MinecraftLangJsonAdapter()
        plan = adapter.prepare("assets/demo/lang/en_us.json", source)
        self.assertEqual(len(plan.units), 1)
        unit = plan.units[0]
        protected = {fragment.value for fragment in unit.protected}
        self.assertIn("$$old_pos_x", protected)
        self.assertIn("%kkey.hexerei.glasses_zoom%", protected)
        self.assertIn("/create", protected)
        self.assertIn("https://docs.example.test/page", protected)
        self.assertIn("on/off", unit.text)
        self.assertEqual(adapter.apply(plan, {unit.id: unit.text}), source)

        for fragment in unit.protected:
            with self.subTest(fragment=fragment.value):
                with self.assertRaises(ValidationError):
                    adapter.apply(
                        plan,
                        {unit.id: unit.text.replace(fragment.placeholder, "", 1)},
                    )

    def test_slash_boundary_does_not_mask_prose_or_paths(self) -> None:
        source = '{"demo":"on/off and/or ./config/demo https://example.test/a/b /track help"}'
        plan = MinecraftLangJsonAdapter().prepare("assets/demo/lang/en_us.json", source)
        unit = plan.units[0]
        protected = {fragment.value for fragment in unit.protected}
        self.assertIn("https://example.test/a/b", protected)
        self.assertIn("/track", protected)
        self.assertNotIn("/off", protected)
        self.assertNotIn("/or", protected)
        self.assertNotIn("/config", protected)

    def test_append_rejects_damaged_fancymenu_and_hexerei_tokens(self) -> None:
        source = (
            '{"fancy":"Move $$old_pos_x",'
            '"hex":"Press %kkey.hexerei.book_hovering_uses%",'
            '"command":"Run /create overlay reset"}'
        )
        target = (
            '{"fancy":"Переместить $old_pos_x",'
            '"hex":"Нажмите %kkey.hexerei.book_hovering_uses",'
            '"command":"Запустите /создать overlay reset"}'
        )
        plan = LocaleMergePlanner().plan(
            "assets/demo/lang/en_us.json",
            source,
            "ru_ru",
            target_text=target,
            mode="append",
        )
        self.assertEqual(
            set(plan.invalid_existing_keys),
            {"fancy", "hex", "command"},
        )
        self.assertEqual(set(plan.pending_ids), {"key:fancy", "key:hex", "key:command"})

    def test_clean_runtime_tokens_can_be_reused(self) -> None:
        source = '{"fancy":"Move $$old_pos_x","command":"Run /create overlay reset"}'
        target = '{"fancy":"Переместить $$old_pos_x","command":"Запустите /create overlay reset"}'
        plan = LocaleMergePlanner().plan(
            "assets/demo/lang/en_us.json",
            source,
            "ru_ru",
            target_text=target,
            mode="append",
        )
        self.assertEqual(plan.invalid_existing_keys, ())
        self.assertEqual(plan.pending_ids, ())


if __name__ == "__main__":
    unittest.main()
