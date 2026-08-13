import unittest

from mineai.gui_qt.log_model import split_translation_message


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


if __name__ == "__main__":
    unittest.main()
