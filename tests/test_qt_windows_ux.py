import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication, QToolButton
    from mineai.config import settings
    from mineai.gui_qt.dialogs import SettingsDialog
    from mineai.gui_qt.log_model import entry_from_message
    from mineai.gui_qt.main_window import TranslatorQtWindow
    from mineai.gui_qt.widgets import ScrollSafeComboBox, ScrollSafeSpinBox
except ImportError:
    Qt = None
    QApplication = None
    QToolButton = None
    settings = None
    SettingsDialog = None
    entry_from_message = None
    TranslatorQtWindow = None
    ScrollSafeComboBox = None
    ScrollSafeSpinBox = None


@unittest.skipIf(QApplication is None, "PyQt6 is not installed")
class WheelSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_combo_ignores_wheel(self):
        combo = ScrollSafeComboBox()
        event = _FakeWheelEvent()
        combo.wheelEvent(event)
        self.assertTrue(event.ignored)

    def test_spinbox_ignores_wheel(self):
        spin = ScrollSafeSpinBox()
        event = _FakeWheelEvent()
        spin.wheelEvent(event)
        self.assertTrue(event.ignored)

    def test_settings_numeric_fields_use_scroll_safe_spinbox(self):
        dialog = SettingsDialog(settings, lambda: None)
        try:
            self.assertIsInstance(dialog.ai_retries, ScrollSafeSpinBox)
            self.assertIsInstance(dialog.google_workers, ScrollSafeSpinBox)
        finally:
            dialog.close()

    def test_log_autoscroll_checkbox_preserves_manual_scroll_position(self):
        window = TranslatorQtWindow()
        try:
            window.resize(1240, 760)
            window.show()
            self.app.processEvents()
            for index in range(250):
                window.log_view.appendPlainText(f"existing line {index}")
            self.app.processEvents()
            bar = window.log_view.verticalScrollBar()
            self.assertGreater(bar.maximum(), 0)

            window.log_autoscroll.setChecked(False)
            manual_position = max(0, bar.maximum() // 3)
            bar.setValue(manual_position)
            self.app.processEvents()
            window._append_entry_to_view(
                entry_from_message("white", "new line while autoscroll is disabled", "#E2E8F0")
            )
            self.app.processEvents()
            self.assertEqual(bar.value(), manual_position)

            window.log_autoscroll.setChecked(True)
            window._append_entry_to_view(
                entry_from_message("white", "new line while autoscroll is enabled", "#E2E8F0")
            )
            self.app.processEvents()
            self.assertEqual(bar.value(), bar.maximum())
        finally:
            window.close()

    def test_spinbox_paints_both_step_indicators(self):
        spin = _ProbeSpinBox()
        try:
            spin.resize(180, 36)
            spin.show()
            self.app.processEvents()
            spin.indicator_calls.clear()
            spin.repaint()
            self.app.processEvents()

            directions = {upward for _, upward in spin.indicator_calls}
            self.assertEqual(directions, {True, False})
            self.assertTrue(all(rect.isValid() and not rect.isEmpty() for rect, _ in spin.indicator_calls))
        finally:
            spin.close()

    def test_locale_rebuild_keeps_google_and_ai_panels_synchronized(self):
        window = TranslatorQtWindow()
        try:
            window.engine_combo.setCurrentText("Google")
            window._rebuild_ui_for_locale()
            self.assertFalse(window.google_options.isHidden())
            self.assertTrue(window.ai_options.isHidden())

            ai_index = next(
                i for i in range(window.engine_combo.count())
                if window.engine_combo.itemText(i) in ("Локальный ИИ", "Local AI")
            )
            window.engine_combo.setCurrentIndex(ai_index)
            window._rebuild_ui_for_locale()
            self.assertTrue(window.google_options.isHidden())
            self.assertFalse(window.ai_options.isHidden())
        finally:
            window.close()

    def test_primary_translation_choices_are_restored_and_saved(self):
        original = {
            key: settings.get("GENERAL", key)
            if settings._config.has_option("GENERAL", key)
            else ""
            for key in ("minecraft_version", "target_language", "translation_engine")
        }
        settings.set_many(
            "GENERAL",
            {
                "minecraft_version": "1.21.1",
                "target_language": "Deutsch",
                "translation_engine": "LM Studio",
            },
        )
        window = TranslatorQtWindow()
        try:
            self.assertEqual(window.version_combo.currentText(), "1.21.1")
            self.assertEqual(window.language_combo.currentText(), "Deutsch")
            self.assertEqual(window.engine_combo.currentText(), "LM Studio")

            window.version_combo.setCurrentText("1.20.1")
            window.language_combo.setCurrentText("Русский")
            window.engine_combo.setCurrentText("Google")
            self.app.processEvents()

            self.assertEqual(settings.get("GENERAL", "minecraft_version"), "1.20.1")
            self.assertEqual(settings.get("GENERAL", "target_language"), "Русский")
            self.assertEqual(settings.get("GENERAL", "translation_engine"), "Google")
        finally:
            window.close()
            settings.set_many("GENERAL", original)

    def test_cache_recovery_checkbox_disables_modes_and_uses_local_ai(self):
        previous = settings.get("GENERAL", "cache_recovery_mode")
        settings.set("GENERAL", "cache_recovery_mode", False)
        window = TranslatorQtWindow()
        try:
            window.engine_combo.setCurrentText("Google")
            window.cache_recovery_checkbox.setChecked(True)
            self.app.processEvents()

            self.assertTrue(window.cache_recovery_checkbox.isChecked())
            self.assertEqual(
                window._translation_options().cache_recovery_mode,
                True,
            )
            self.assertEqual(
                window._translation_options().ai_provider,
                "local",
            )
            self.assertTrue(window.ai_fallback.isChecked())
            self.assertFalse(window.ai_fallback.isEnabled())
            self.assertTrue(
                all(not button.isEnabled() for button in window.mode_buttons.values())
            )
            self.assertTrue(
                settings.getboolean("GENERAL", "cache_recovery_mode")
            )
        finally:
            window.close()
            settings.set("GENERAL", "cache_recovery_mode", previous)

    def test_log_toolbar_has_only_clear_and_export_actions(self):
        window = TranslatorQtWindow()
        try:
            buttons = [
                button for button in window.findChildren(QToolButton)
                if button.objectName() == "LogToolButton"
            ]
            self.assertEqual(len(buttons), 2)
            tooltips = {button.toolTip() for button in buttons}
            self.assertIn("Экспорт лога" if window._ui_language == "ru" else "Export log", tooltips)
            self.assertNotIn("Открыть лог", tooltips)
            self.assertNotIn("Open log", tooltips)
        finally:
            window.close()

    def test_language_control_is_compact_toggle(self):
        window = TranslatorQtWindow()
        try:
            self.assertIsInstance(window.interface_language, QToolButton)
            self.assertIn(window.interface_language.text(), {"RU", "EN"})
            self.assertEqual(window.interface_language.width(), 46)
        finally:
            window.close()

    def test_analysis_items_use_compact_dialog_launcher_and_can_be_excluded(self):
        window = TranslatorQtWindow()
        try:
            self.assertTrue(hasattr(window, "analysis_configure_button"))
            self.assertFalse(hasattr(window, "analysis_tree"))

            target = SimpleNamespace(
                key="mods:C:/modpack/mods/example.jar",
                path="C:/modpack/mods/example.jar",
                icon="📦",
                name="Example Mod",
                kind="Интерфейс",
                translated=2,
                total=10,
                percent=20,
                parent_key=None,
                is_group=False,
            )
            window._append_analysis_item(target)

            self.assertEqual(
                window._selected_analysis_items(),
                frozenset({target.key}),
            )
            self.assertIn("1", window.analysis_summary.text())

            window._set_all_analysis_items(False)
            self.assertEqual(window._selected_analysis_items(), frozenset())

            window._worker = SimpleNamespace(is_alive=lambda: True)
            window._refresh_analysis_summary()
            self.assertFalse(window.analysis_configure_button.isEnabled())
            window._worker = None
        finally:
            window.close()


class _ProbeSpinBox(ScrollSafeSpinBox if ScrollSafeSpinBox is not None else object):
    def __init__(self):
        super().__init__()
        self.indicator_calls = []

    def _draw_step_chevron(self, painter, rect, upward):
        self.indicator_calls.append((rect, upward))


class _FakeWheelEvent:
    def __init__(self):
        self.ignored = False

    def ignore(self):
        self.ignored = True


if __name__ == "__main__":
    unittest.main()
