import unittest

from mineai.config import ConfigManager
from mineai.gui_qt.i18n import translator, t
from mineai.gui_qt.i18n_runtime import tr
from mineai.gui_qt.log_model import entry_from_message, matches_entry
from mineai.gui_qt.theme import theme_qss
from mineai.gui_qt.view_model import engine_readiness


class _ConfigStub:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, section, key):
        return self.values.get((section, key), "")


class QtUxHardeningTests(unittest.TestCase):
    def setUp(self):
        self.previous_language = translator.language
        translator.set_language("ru")

    def tearDown(self):
        translator.set_language(self.previous_language)

    def test_config_has_persisted_ui_language_default(self):
        self.assertEqual(ConfigManager._DEFAULTS["GENERAL"]["ui_language"], "ru")
        self.assertEqual(ConfigManager._DEFAULTS["GENERAL"]["theme"], "Dark")
        self.assertEqual(ConfigManager._DEFAULTS["GENERAL"].get("minecraft_version"), "1.20.1")
        self.assertEqual(ConfigManager._DEFAULTS["GENERAL"].get("target_language"), "Русский")
        self.assertEqual(ConfigManager._DEFAULTS["GENERAL"].get("translation_engine"), "Google")
        self.assertEqual(
            ConfigManager._DEFAULTS["GENERAL"].get("cache_recovery_mode"),
            "False",
        )

    def test_interface_dictionary_switches_between_ru_and_en(self):
        self.assertEqual(t("button.analysis"), "Анализ")
        translator.set_language("en")
        self.assertEqual(t("button.analysis"), "Analyze")
        self.assertEqual(tr("engine.local"), "Local AI")

    def test_mode_labels_and_batch_guidance_are_localized(self):
        self.assertEqual(t("mode.append"), "Дополнить")
        self.assertEqual(t("mode.skip"), "Пропустить")
        self.assertEqual(t("mode.force"), "Заново")
        self.assertEqual(t("output.resourcepack"), "Ресурс-пак")
        self.assertEqual(t("output.inplace"), "Прямо в JAR")
        self.assertIn("макс. 40", t("field.ai_batch_limit"))
        self.assertIn("15 строк или меньше", t("tooltip.ai_batch"))
        self.assertIn("90%", t("tooltip.mode_skip"))
        self.assertIn("AI-кэш", t("mode.cache_recovery"))

        translator.set_language("en")
        self.assertEqual(t("mode.append"), "Append")
        self.assertEqual(t("output.resourcepack"), "Resource Pack")
        self.assertIn("max. 40", t("field.ai_batch_limit"))
        self.assertIn("15 lines or fewer", t("tooltip.ai_batch"))
        self.assertIn("AI cache", t("mode.cache_recovery"))

    def test_engine_readiness_uses_current_interface_language(self):
        config = _ConfigStub()
        self.assertEqual(engine_readiness(config, "Google"), (True, "Google готов"))
        translator.set_language("en")
        self.assertEqual(engine_readiness(config, "Google"), (True, "Google ready"))
        self.assertEqual(engine_readiness(config, "Local AI"), (False, "Local GGUF model is not selected"))

    def test_theme_qss_has_live_dark_and_light_palettes(self):
        dark = theme_qss("Dark")
        light = theme_qss("Light")
        self.assertIn("#12131C", dark)
        self.assertIn("#E4E8EE", light)
        self.assertIn("QPushButton#PrimaryButton { background-color: #7652D6", light)
        self.assertIn("QToolButton#HelpMarker", dark)
        self.assertIn("QPlainTextEdit#LogView", light)

    def test_analysis_selection_has_explicit_dark_and_light_styling(self):
        dark = theme_qss("Dark")
        light = theme_qss("Light")

        self.assertIn("QTreeWidget#TranslationSelectionTree", dark)
        self.assertGreater(
            light.count("QTreeWidget#TranslationSelectionTree"),
            dark.count("QTreeWidget#TranslationSelectionTree"),
        )

    def test_selection_dialog_distinguishes_hover_from_checked_state(self):
        dark = theme_qss("Dark")
        light = theme_qss("Light")

        for stylesheet in (dark, light):
            self.assertIn("QTreeWidget#TranslationSelectionTree::item:hover", stylesheet)
            self.assertIn("QTreeWidget#TranslationSelectionTree::indicator:checked", stylesheet)
        self.assertIn("#20C7B7", dark)
        self.assertIn("#0F9F92", light)

    def test_semantic_filters_do_not_treat_all_yellow_lines_as_errors(self):
        progress = entry_from_message("yellow", "📦 Чтение ресурс-пака pack.zip...", "#fff")
        error = entry_from_message("red", "Ошибка: connection timeout", "#fff")
        skipped = entry_from_message("yellow", "Пропущено: уже переведено", "#fff")
        translated = entry_from_message("green", " > Apple -> Яблоко", "#fff")
        analysis = entry_from_message("cyan", "Анализ сборки: найдено 14 модов", "#fff")

        self.assertEqual(progress.category, "other")
        self.assertEqual(error.category, "issues")
        self.assertEqual(skipped.category, "issues")
        self.assertEqual(translated.category, "translated")
        self.assertEqual(analysis.category, "analysis")

    def test_semantic_filter_combines_category_and_casefolded_search(self):
        entry = entry_from_message("green", " > Quantic Math -> Квантовая Математика", "#fff")
        self.assertTrue(matches_entry(entry, "translated", "quantic"))
        self.assertTrue(matches_entry(entry, "all", "МАТЕМАТИКА"))
        self.assertFalse(matches_entry(entry, "issues", "quantic"))
        self.assertFalse(matches_entry(entry, "translated", "apotheosis"))


if __name__ == "__main__":
    unittest.main()
