import json
import os
import shutil
import tempfile
import unittest
import zipfile

from mineai.processors.analyzer import ModpackAnalyzer
from mineai.processors.estimator import StringEstimator
from mineai.runtime.state import JobState

TARGET_LANG = {"file": "ru_ru", "api": "ru", "name": "Russian", "regex": r"[А-Яа-яЁё]"}


class AnalyzerEstimatorAlignmentTests(unittest.TestCase):
    def _make_jar(self, entries: dict[str, str]) -> str:
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp_dir)
        path = os.path.join(temp_dir, "books.jar")
        with zipfile.ZipFile(path, "w") as archive:
            for name, content in entries.items():
                archive.writestr(name, content)
        return path

    def _counts(self, path: str):
        state = JobState()
        state.start()
        rows = []
        analyzed = ModpackAnalyzer(state)._analyze_jar(
            path, "ru_ru.json", TARGET_LANG["regex"], False, True,
            lambda *row: rows.append(row), "Example",
        )
        estimated = StringEstimator(state)._estimate_jar(
            path, "ru_ru.json", TARGET_LANG, "force", False, True, False,
        )
        return analyzed, estimated, rows

    def test_files_without_en_us_are_ignored_by_both(self) -> None:
        path = self._make_jar({
            "assets/example/research/topic.json": json.dumps({"title": "Research Topic"}),
            "assets/example/manual/page.md": "Manual page text",
        })
        analyzed, estimated, rows = self._counts(path)
        self.assertEqual(analyzed, (0, 0))
        self.assertEqual(estimated, 0)
        self.assertEqual(rows, [])

    def test_en_us_research_file_is_counted_by_both(self) -> None:
        path = self._make_jar({
            "assets/example/research/en_us/topic.json": json.dumps({"title": "Research Topic"}),
        })
        analyzed, estimated, _rows = self._counts(path)
        self.assertEqual(analyzed, (1, 0))
        self.assertEqual(estimated, 1)


if __name__ == "__main__":
    unittest.main()
