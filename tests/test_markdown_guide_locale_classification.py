import unittest

from mineai.processors.markdown_guides import is_source_markdown_guide


class MarkdownGuideLocaleClassificationTests(unittest.TestCase):
    def test_guideme_localized_subdirectories_are_not_sources(self) -> None:
        for locale_dir in ("_ru_ru", "ru_ru"):
            with self.subTest(locale_dir=locale_dir):
                self.assertFalse(
                    is_source_markdown_guide(
                        f"assets/ae2/ae2guide/{locale_dir}/getting-started.md"
                    )
                )

    def test_guideme_root_page_remains_a_source(self) -> None:
        self.assertTrue(
            is_source_markdown_guide(
                "assets/ae2/ae2guide/getting-started.md"
            )
        )


if __name__ == "__main__":
    unittest.main()
