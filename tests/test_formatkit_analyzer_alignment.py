import json
import os
import shutil
import tempfile
import unittest
import zipfile

from mineai.processors.analyzer import ModpackAnalyzer
from mineai.processors.formatkit_pilot import FormatKitStringEstimator
from mineai.runtime.state import JobState


TARGET_LANG = {"file": "ru_ru", "api": "ru", "name": "Russian", "regex": r"[А-Яа-яЁё]"}


def serialized_component(*texts: str) -> str:
    nodes = []
    for index, text in enumerate(texts):
        node = {"text": text}
        if index == len(texts) - 1:
            node["color"] = "dark_green"
            node["clickEvent"] = {"action": "open_url", "value": "%s"}
        nodes.append(node)
    return json.dumps(nodes, separators=(",", ":"))


class FormatKitAnalyzerAlignmentTests(unittest.TestCase):
    def _make_jar(self, source: dict[str, str], target: dict[str, str] | None = None) -> str:
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp_dir)
        path = os.path.join(temp_dir, "actuallyadditions.jar")
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "assets/actuallyadditions/lang/en_us.json",
                json.dumps(source, ensure_ascii=False),
            )
            if target is not None:
                archive.writestr(
                    "assets/actuallyadditions/lang/ru_ru.json",
                    json.dumps(target, ensure_ascii=False),
                )
        return path

    def _analyze(self, path: str):
        state = JobState()
        state.start()
        rows = []
        counts = ModpackAnalyzer(state)._analyze_jar(
            path,
            "ru_ru.json",
            TARGET_LANG["regex"],
            True,
            False,
            lambda *row: rows.append(row),
            "Actuallyadditions",
        )
        return counts, rows

    def test_real_runtime_1021_to_1027_component_drift_is_closed(self) -> None:
        # Reproduce the exact cardinality seen in the FTB Evolution pilot:
        # legacy top-level JSON sees 1021 strings, while four serialized
        # Component strings contain ten independently translatable text leaves.
        source = {f"plain.{index}": f"Visible text {index}" for index in range(1017)}
        source.update(
            {
                "update.one": serialized_component("Update available", "Open page"),
                "update.two": serialized_component("Current version", "New version"),
                "update.three": serialized_component("Download", "Website", "Later"),
                "update.four": serialized_component("Update failed", "Try again", "Close"),
            }
        )
        self.assertEqual(len(source), 1021)
        path = self._make_jar(source)

        analyzed, rows = self._analyze(path)

        state = JobState()
        state.start()
        estimated = FormatKitStringEstimator(state)._estimate_jar(
            path,
            "ru_ru.json",
            TARGET_LANG,
            "force",
            True,
            False,
            False,
        )

        self.assertEqual(analyzed, (1027, 0))
        self.assertEqual(estimated, 1027)
        self.assertEqual(rows, [("📦", "Actuallyadditions", "Интерфейс", 0, 1027, 0)])

    def test_analyzer_reuses_the_same_safe_existing_target_as_formatkit(self) -> None:
        source = {
            "plain": "Visible text",
            "update": serialized_component("Update available", "Open page"),
        }
        target = {
            "plain": "Видимый текст",
            "update": serialized_component("Доступно обновление", "Открыть страницу"),
        }
        path = self._make_jar(source, target)

        analyzed, rows = self._analyze(path)

        self.assertEqual(analyzed, (3, 3))
        self.assertEqual(rows, [("📦", "Actuallyadditions", "Интерфейс", 3, 3, 100)])


if __name__ == "__main__":
    unittest.main()
