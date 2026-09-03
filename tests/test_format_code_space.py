"""Tests for C1: spurious space removal after Minecraft format codes in unmask_translation."""
import unittest

from mineai.text_processing import (
    mask_protected_fragments,
    polish_translation,
    unmask_translation,
)


class FormatCodeSpaceTests(unittest.TestCase):
    """C1 — Пробелы после цветовых кодов после восстановления маркеров."""

    # --- Basic cases ----------------------------------------------------------

    def test_space_after_ampersand_color_code_removed(self):
        """'&a Глава' → '&aГлава': лишний пробел убирается."""
        result = unmask_translation("&a Глава", {})
        self.assertEqual(result, "&aГлава")

    def test_space_after_section_color_code_removed(self):
        """'§l Жирный' → '§lЖирный'."""
        result = unmask_translation("§l Жирный", {})
        self.assertEqual(result, "§lЖирный")

    def test_space_not_removed_when_not_after_format_code(self):
        """Обычный пробел в тексте НЕ удаляется."""
        result = unmask_translation("Привет мир", {})
        self.assertEqual(result, "Привет мир")

    def test_multiple_codes_in_string(self):
        """Несколько цветовых кодов в одной строке — все лишние пробелы убираются."""
        result = unmask_translation("&a Глава 1&r: &b Начало", {})
        self.assertEqual(result, "&aГлава 1&r: &bНачало")

    def test_space_not_removed_when_next_is_also_space(self):
        """'&a  Слово' (два пробела) — regex требует \\S после пробела, поэтому первый пробел
        НЕ удаляется (следующий символ — ещё один пробел). Оба пробела остаются.
        Это безопасное поведение — двойные пробелы у AI крайне редки."""
        result = unmask_translation("&a  Слово", {})
        # First space NOT removed because (?=\S) isn't satisfied (next char is space)
        self.assertEqual(result, "&a  Слово")

    # --- Via marker restore ---------------------------------------------------

    def test_marker_restored_to_format_code_no_extra_space(self):
        """Маркер [#0#] заменён на &a, перевод имел пробел → пробел убирается."""
        # Simulates AI returning "[#0#] Глава" when original had "[#0#]Глава"
        mapping = {"[#0#]": "&a"}
        result = unmask_translation("[#0#] Глава", mapping)
        self.assertEqual(result, "&aГлава")

    def test_marker_restored_to_section_code_no_extra_space(self):
        """§b восстановлен из маркера, без пробела."""
        mapping = {"[#0#]": "§b"}
        result = unmask_translation("[#0#] Текст", mapping)
        self.assertEqual(result, "§bТекст")

    def test_real_roundtrip_format_code(self):
        """Полный цикл: маскировка → AI добавляет пробел → восстановление убирает пробел."""
        source = "&aChapter 1&r: &bThe Beginning"
        masked, mapping = mask_protected_fragments(source)
        # Simulate AI adding a space after each restored marker
        ai_output = masked
        for token in mapping:
            ai_output = ai_output.replace(token, token + " ")
        result = unmask_translation(ai_output, mapping)
        # All spurious spaces should be gone
        self.assertNotIn("&a ", result)
        self.assertNotIn("&b ", result)
        self.assertNotIn("&r ", result)

    def test_no_space_removal_in_regular_text(self):
        """Format codes followed by punctuation/newline are untouched."""
        # &r at end of line – space before newline, not after code, ignore
        result = unmask_translation("Текст &r\n", {})
        self.assertEqual(result, "Текст &r\n")

    def test_all_color_codes_covered(self):
        """Проверяем все цифровые коды 0-9."""
        for ch in "0123456789":
            with self.subTest(code=ch):
                result = unmask_translation(f"&{ch} Слово", {})
                self.assertEqual(result, f"&{ch}Слово")

    def test_all_format_codes_covered(self):
        """Проверяем форматные коды l, m, n, o, r, k."""
        for ch in "lmnork":
            with self.subTest(code=ch):
                result = unmask_translation(f"&{ch} Слово", {})
                self.assertEqual(result, f"&{ch}Слово")

    def test_polish_preserves_source_boundary_after_ampersand_code(self):
        """&6Extras must become &6Дополнительно, without an invented space."""
        result = polish_translation(
            "&6Дополнительно",
            boundary_source="&6Extras",
        )
        self.assertEqual(result, "&6Дополнительно")

    def test_polish_keeps_intentional_source_space_after_code(self):
        result = polish_translation(
            "&6 Дополнительно",
            boundary_source="&6 Extras",
        )
        self.assertEqual(result, "&6 Дополнительно")

    def test_polish_preserves_adjacent_ampersand_style_codes(self):
        """Adjacent colour/style codes remain adjacent to translated text."""
        result = polish_translation(
            "&d&lСкалк-мука",
            boundary_source="&d&lSculk Flour",
        )
        self.assertEqual(result, "&d&lСкалк-мука")


if __name__ == "__main__":
    unittest.main()
