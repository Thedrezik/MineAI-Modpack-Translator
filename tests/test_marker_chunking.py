"""Tests for H1: long texts with many [#N#] markers are split into sub-chunks."""
import unittest
from unittest.mock import MagicMock, patch

from mineai.engines.llm_common import split_by_placeholders


class MarkerChunkingTests(unittest.TestCase):
    """H1 — Дробление строк с 8+ маркерами на чанки."""

    def _make_masked(self, n: int) -> str:
        """Return a masked string with n markers separated by words."""
        parts = []
        for i in range(n):
            parts.append(f"[#{i}#]word{i}")
        return " ".join(parts)

    def test_few_markers_no_split(self):
        """7 маркеров — не дробится (ниже порога 8)."""
        masked = self._make_masked(7)
        chunks = split_by_placeholders(masked, max_per_chunk=8)
        self.assertEqual(len(chunks), 1)

    def test_exactly_threshold_no_split(self):
        """8 маркеров — ровно на пороге, не дробится."""
        masked = self._make_masked(8)
        chunks = split_by_placeholders(masked, max_per_chunk=8)
        self.assertEqual(len(chunks), 1)

    def test_nine_markers_splits(self):
        """9 маркеров → разбивается на >= 2 чанка."""
        masked = self._make_masked(9)
        chunks = split_by_placeholders(masked, max_per_chunk=8)
        self.assertGreater(len(chunks), 1)

    def test_ten_markers_splits_into_chunks(self):
        """10 маркеров, max_per_chunk=4 → 3 чанка."""
        masked = self._make_masked(10)
        chunks = split_by_placeholders(masked, max_per_chunk=4)
        # 10 markers, 4 per chunk → ceil(10/4) ≈ 3 chunks
        self.assertGreaterEqual(len(chunks), 2)

    def test_all_markers_preserved_after_split(self):
        """Все маркеры сохраняются после объединения чанков."""
        import re
        n = 12
        masked = self._make_masked(n)
        chunks = split_by_placeholders(masked, max_per_chunk=4)
        combined = "".join(chunks)
        found = re.findall(r'\[#\d+#\]', combined)
        self.assertEqual(len(found), n)
        # All original marker ids present
        for i in range(n):
            self.assertIn(f"[#{i}#]", combined)

    def test_no_markers_returns_single_chunk(self):
        """Строка без маркеров возвращается как один чанк."""
        text = "Simple text without any markers here."
        chunks = split_by_placeholders(text, max_per_chunk=4)
        self.assertEqual(chunks, [text])

    def test_clean_transport_replaces_marker_threshold_gating(self):
        """Новые запросы используют видимые узлы, а не порог маркеров."""
        import inspect
        from mineai.engines import llm_common
        src = inspect.getsource(llm_common.BatchLlmEngine._translate_clean_chunk)
        self.assertIn("extract_visible_nodes", src)
        self.assertNotIn("PLACEHOLDER_THRESHOLD", src)


if __name__ == "__main__":
    unittest.main()
