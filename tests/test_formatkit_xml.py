import unittest

from formatkit import FormatRegistry


class FormatKitXmlTests(unittest.TestCase):
    def test_only_xml_text_nodes_are_translated(self) -> None:
        source = '<page>\n  <text color="white">missing pages</text>\n</page>\n'
        plan = FormatRegistry.default().plan(
            "assets/ldlib/compass/pages/en_us/missing.xml",
            source,
            "ru_ru",
        )

        self.assertEqual(plan.adapter_id, "xml-text-v1")
        self.assertEqual(
            plan.target_path,
            "assets/ldlib/compass/pages/ru_ru/missing.xml",
        )
        self.assertEqual([unit.payload for unit in plan.units], ["missing pages"])
        output = plan.apply({plan.units[0].id: "отсутствующие страницы"}).text
        self.assertEqual(
            output,
            '<page>\n  <text color="white">отсутствующие страницы</text>\n</page>\n',
        )


if __name__ == "__main__":
    unittest.main()
