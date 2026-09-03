import unittest

from mineai_formatkit import (
    AdapterCapabilities,
    FormatKit,
    FormatRegistry,
    TranslationPlan,
    TranslationUnit,
    ValidationError,
)


class FormatKitFailClosedTests(unittest.TestCase):
    def test_target_path_error_makes_analysis_not_ready_and_blocks_apply(self) -> None:
        class BrokenTargetAdapter:
            name = "broken-target"

            def matches(self, path: str) -> bool:
                return path.endswith(".demo")

            def prepare(self, path: str, source_text: str) -> TranslationPlan:
                return TranslationPlan(
                    path=path,
                    source_text=source_text,
                    units=(
                        TranslationUnit(
                            id="text:0",
                            text=source_text,
                            start=0,
                            end=len(source_text),
                            kind="demo",
                        ),
                    ),
                )

            def target_path(self, path: str, target_locale: str) -> str:
                raise ValueError("cannot derive target path")

            def apply(self, plan: TranslationPlan, translations):
                return translations.get("text:0", plan.source_text)

        registry = FormatRegistry()
        registry.register(
            BrokenTargetAdapter(),
            AdapterCapabilities(
                name="broken-target",
                format_name="test",
                supports_target_path=True,
            ),
        )
        kit = FormatKit(registry)

        analysis = kit.analyze("sample.demo", "Hello", target_locale="ru_ru")

        self.assertTrue(analysis.supported)
        self.assertTrue(analysis.has_errors)
        self.assertFalse(analysis.ready)
        self.assertEqual(analysis.diagnostics[-1].code, "target_path_failed")
        with self.assertRaises(ValidationError):
            kit.apply(analysis, {"text:0": "Привет"})


if __name__ == "__main__":
    unittest.main()
