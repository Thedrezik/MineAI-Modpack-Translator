import unittest

from datetime import datetime

from mineai import __version__
from mineai.gui_qt import log_model

entry_from_message = log_model.entry_from_message
split_translation_message = log_model.split_translation_message


class TranslationMessageSplitTests(unittest.TestCase):
    def test_splits_long_translation_and_duplicate_suffix_without_data_loss(self):
        source = " > " + "source " * 80
        target = "перевод " * 90
        message = f"{source} -> {target} ×3"
        parts = split_translation_message(message)
        self.assertIsNotNone(parts)
        assert parts is not None
        self.assertEqual(parts.left, source)
        self.assertEqual(parts.separator, " -> ")
        self.assertEqual(parts.right, target)
        self.assertEqual(parts.suffix, " ×3")
        self.assertEqual(parts.left + parts.separator + parts.right + parts.suffix, message)

    def test_non_translation_message_is_untouched(self):
        self.assertIsNone(split_translation_message("Обычная строка журнала"))

    def test_persisted_log_contains_timestamp_level_and_category(self):
        entry = entry_from_message(
            "red",
            "Ошибка файла\nполная диагностика",
            "#F87171",
        )

        line = log_model.format_persisted_log_line(
            entry,
            datetime(2026, 8, 15, 13, 14, 15),
        )

        self.assertEqual(
            line,
            "[2026-08-15 13:14:15] [ERROR] [ISSUES] "
            "Ошибка файла\nполная диагностика",
        )

    def test_session_header_identifies_version_and_start_time(self):
        header = log_model.format_session_header(
            __version__,
            datetime(2026, 8, 15, 13, 14, 15),
        )

        self.assertIn("NEW SESSION", header)
        self.assertIn(__version__, header)
        self.assertIn("2026-08-15 13:14:15", header)


if __name__ == "__main__":
    unittest.main()
