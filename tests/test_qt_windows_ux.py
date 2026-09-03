import os
import unittest
from unittest import mock
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication, QMessageBox, QToolButton
    from mineai.config import settings
    from mineai.gui_qt.dialogs import PreviewDialog, SettingsDialog
    from mineai.gui_qt.log_model import entry_from_message
    from mineai.gui_qt.main_window import TranslatorQtWindow
    from mineai.gui_qt.widgets import ScrollSafeComboBox, ScrollSafeSpinBox
    from mineai.preview import PreviewBuilder, PreviewDocument, PreviewInput, PreviewPage, PreviewReport
except ImportError:
    Qt = None
    QApplication = None
    QMessageBox = None
    QToolButton = None
    settings = None
    SettingsDialog = None
    PreviewDialog = None
    entry_from_message = None
    TranslatorQtWindow = None
    ScrollSafeComboBox = None
    ScrollSafeSpinBox = None
    PreviewBuilder = None
    PreviewInput = None
    PreviewDocument = None
    PreviewPage = None
    PreviewReport = None


def _walk_tree(item):
    yield item
    for index in range(item.childCount()):
        yield from _walk_tree(item.child(index))


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

    def test_preview_is_available_in_header_and_opens_game_view(self):
        window = TranslatorQtWindow()
        try:
            self.assertTrue(hasattr(window, "preview_header_button"))
            self.assertIn("В игре" if window._ui_language == "ru" else "In-game", window.preview_header_button.text())
            self.assertTrue(window.preview_header_button.toolTip())
        finally:
            window.close()

    def test_preview_selection_rows_show_text_instead_of_internal_unit_ids(self):
        report = PreviewBuilder(target_regex=r"[А-Яа-яЁё]").build(
            [
                PreviewInput(
                    logical_path="config/ftbquests/quests/chapters/chapter.snbt",
                    source_text='{id: "AAAABBBBCCCCDDDD", title: "Quest"}',
                    target_text='{id: "AAAABBBBCCCCDDDD", title: "Квест"}',
                    kind="quest",
                )
            ]
        )
        dialog = PreviewDialog(report)
        try:
            visible = " ".join(
                item.text(0)
                for tree in dialog._selection_trees
                for index in range(tree.topLevelItemCount())
                for item in _walk_tree(tree.topLevelItem(index))
            )
            self.assertIn("Квест", visible)
            self.assertNotIn("snbt/", visible)
            self.assertNotIn("AAAABBBBCCCCDDDD", visible)
        finally:
            dialog.close()

    def test_preview_graph_card_selects_quest_units_and_dialog_is_resizable(self):
        source = '{quests: [{id: "AAAABBBBCCCCDDDD", title: "Quest", description: ["English"]}]}\n'
        target = source.replace("Quest", "Квест").replace("English", "Описание")
        report = PreviewBuilder(target_regex=r"[А-Яа-яЁё]").build(
            [
                PreviewInput(
                    logical_path="config/ftbquests/quests/chapters/chapter.snbt",
                    source_text=source,
                    target_text=target,
                    kind="quest",
                )
            ]
        )
        dialog = PreviewDialog(report)
        try:
            self.assertTrue(dialog.isSizeGripEnabled())
            self.assertTrue(dialog._graph_views)
            dialog._select_graph_node("AAAABBBBCCCCDDDD")
            checked = [
                item
                for tree in dialog._selection_trees
                for index in range(tree.topLevelItemCount())
                for item in _walk_tree(tree.topLevelItem(index))
                if item.childCount() == 0 and item.checkState(0) == Qt.CheckState.Checked
            ]
            self.assertTrue(checked)
            quest_tree = dialog._selection_trees[1]
            quest_row = next(
                item
                for index in range(quest_tree.topLevelItemCount())
                for item in _walk_tree(quest_tree.topLevelItem(index))
                if item.childCount() == 0
            )
            dialog._select_tree_item(quest_row, 0)
            self.assertEqual(
                dialog._graph_views[0].highlighted_node_id.casefold(),
                "AAAABBBBCCCCDDDD".casefold(),
            )
            self.assertTrue(quest_row.isSelected())
        finally:
            dialog.close()

    def test_preview_quest_graph_supports_zoom_out_and_fit(self):
        source = '{quests: [{id: "AAAABBBBCCCCDDDD", title: "Quest"}]}\n'
        target = source.replace("Quest", "Квест")
        report = PreviewBuilder(target_regex=r"[А-Яа-яЁё]").build(
            [
                PreviewInput(
                    logical_path="config/ftbquests/quests/chapters/chapter.snbt",
                    source_text=source,
                    target_text=target,
                    kind="quest",
                )
            ]
        )
        dialog = PreviewDialog(report)
        try:
            graph = dialog._graph_views[0]
            initial_scale = graph.transform().m11()
            graph.zoom_out()
            self.assertLess(graph.transform().m11(), initial_scale)
            graph.fit_graph()
            self.assertGreater(graph.zoom_level, 0.0)
            self.assertLessEqual(graph.zoom_level, 1.0)
        finally:
            dialog.close()

    def test_preview_quest_selection_highlights_direct_dependencies_and_wraps_cards(self):
        long_title = "A quest title that is deliberately long enough to require multiple lines in the preview card"
        source = (
            '{quests: [{id: "AAAABBBBCCCCDDDD" title: "Selected"} '
            f'{{id: "1111222233334444" title: "{long_title}" dependencies: ["AAAABBBBCCCCDDDD"]}}]}}\n'
        )
        target = source.replace("Selected", "Выбранный").replace(long_title, "Длинный заголовок квеста")
        report = PreviewBuilder(target_regex=r"[А-Яа-яЁё]").build(
            [
                PreviewInput(
                    logical_path="config/ftbquests/quests/chapters/chapter.snbt",
                    source_text=source,
                    target_text=target,
                    kind="quest",
                )
            ]
        )
        dialog = PreviewDialog(report)
        try:
            graph = dialog._graph_views[0]
            dialog._select_graph_node("1111222233334444")
            self.assertEqual(
                graph.highlighted_dependency_ids,
                {"AAAABBBBCCCCDDDD"},
            )
            self.assertIn("AAAABBBBCCCCDDDD", graph._node_rects)
            self.assertEqual(
                graph._node_rects["AAAABBBBCCCCDDDD"].brush().color().name(),
                "#9a6a2f",
            )
            self.assertTrue(graph._node_texts)
            self.assertTrue(
                all(len(text.toPlainText().splitlines()) <= 3 for text in graph._node_texts.values())
            )
        finally:
            dialog.close()

    def test_preview_books_have_chapter_page_navigation_and_original_toggle(self):
        reports = PreviewReport(
            documents=(
                PreviewDocument(
                    logical_path="assets/example/guide/en_us/chapter-one.md",
                    kind="book",
                    format="markdown-v2",
                    pages=(
                        PreviewPage(0, "Chapter One", "Original page one", "Первая страница", ("u1",)),
                        PreviewPage(1, "Chapter One", "Original page two", "Вторая страница", ("u2",)),
                    ),
                ),
                PreviewDocument(
                    logical_path="assets/example/guide/en_us/chapter-two.md",
                    kind="book",
                    format="markdown-v2",
                    pages=(PreviewPage(0, "Chapter Two", "Original page three", "Третья страница", ("u3",)),),
                ),
            ),
            issues=(),
        )
        dialog = PreviewDialog(reports)
        try:
            self.assertEqual(dialog.book_chapter_combo.count(), 2)
            self.assertEqual(dialog.book_page_label.text(), "Страница 1 / 2")
            self.assertNotIn("Показать оригинал", dialog.books_view.toHtml())
            self.assertIn("Первая страница", dialog.books_view.toPlainText())
            self.assertEqual(dialog.book_original_button.text(), "Показать оригинал")

            dialog.book_next_button.click()
            self.assertEqual(dialog.book_page_label.text(), "Страница 2 / 2")
            dialog.book_original_button.click()
            self.assertEqual(dialog.book_original_button.text(), "Показать перевод")
            self.assertIn("Original page two", dialog.books_view.toHtml())
            self.assertIn("Оригинал как в игре", dialog.books_view.toHtml())
            dialog.book_original_button.click()
            self.assertIn("Вторая страница", dialog.books_view.toHtml())
        finally:
            dialog.close()

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

    def test_cache_recovery_keeps_native_local_providers(self):
        previous = settings.get("GENERAL", "cache_recovery_mode")
        settings.set("GENERAL", "cache_recovery_mode", False)
        try:
            for label, provider in (("Ollama", "ollama"), ("Llama", "llama")):
                window = TranslatorQtWindow()
                try:
                    window.engine_combo.setCurrentText(label)
                    window.cache_recovery_checkbox.setChecked(True)
                    self.app.processEvents()
                    self.assertEqual(window._translation_options().ai_provider, provider)
                finally:
                    window.close()
        finally:
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

    def test_log_trash_requires_confirmation_and_does_not_touch_caches(self):
        window = TranslatorQtWindow()
        try:
            window._clear_log()
            window._append_log("Строка, которую нельзя удалить случайно", "white")
            cache_std = window.cache_std
            cache_ai = window.cache_ai

            with mock.patch.object(
                QMessageBox,
                "question",
                return_value=QMessageBox.StandardButton.No,
            ):
                window._confirm_clear_log()
            self.assertEqual(len(window._log_entries), 1)

            with mock.patch.object(
                QMessageBox,
                "question",
                return_value=QMessageBox.StandardButton.Yes,
            ):
                window._confirm_clear_log()
            self.assertEqual(window._log_entries, [])
            self.assertIs(window.cache_std, cache_std)
            self.assertIs(window.cache_ai, cache_ai)
        finally:
            window.close()

    def test_full_lines_restores_complete_translation_after_compact_preview(self):
        window = TranslatorQtWindow()
        try:
            window.resize(1240, 760)
            window.show()
            self.app.processEvents()
            source = "Very long source text " * 30
            target = "Очень длинный полный перевод " * 30
            entry = entry_from_message(
                "dim",
                f" > {source} -> {target}",
                "#64748B",
            )

            window.log_full_lines.setChecked(False)
            compact = "".join(
                segment.text for segment in window._display_segments_for_entry(entry)
            )
            self.assertIn("…", compact)

            window.log_full_lines.setChecked(True)
            full = "".join(
                segment.text for segment in window._display_segments_for_entry(entry)
            )
            self.assertEqual(full, entry.plain_text)
            self.assertNotIn("…", full)
        finally:
            window.close()

    def test_full_lines_restores_complete_issue_message(self):
        window = TranslatorQtWindow()
        try:
            window.resize(1240, 760)
            window.show()
            self.app.processEvents()
            message = "❌ Отклонён полный исходный текст: " + ("diagnostic " * 100)
            entry = entry_from_message("red", message, "#F87171")

            window.log_full_lines.setChecked(False)
            compact = "".join(
                segment.text for segment in window._display_segments_for_entry(entry)
            )
            self.assertIn("…", compact)

            window.log_full_lines.setChecked(True)
            full = "".join(
                segment.text for segment in window._display_segments_for_entry(entry)
            )
            self.assertEqual(full, message)
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
