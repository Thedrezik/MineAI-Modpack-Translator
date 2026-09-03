import tempfile
from pathlib import Path
import unittest
from pathlib import Path
from types import SimpleNamespace

from mineai.gui_qt.view_model import compact_runtime_status, dashboard_columns, detected_source_roots, engine_readiness, format_duration, stats_from_snapshot


class _Config:
    def __init__(self, values):
        self.values = values

    def get(self, section, key):
        return self.values.get((section, key), "")


class ResponsiveLayoutTests(unittest.TestCase):
    def test_dashboard_switches_to_two_columns_on_narrow_windows(self):
        self.assertEqual(dashboard_columns(1240), 2)
        self.assertEqual(dashboard_columns(1366), 2)
        self.assertEqual(dashboard_columns(1419), 2)

    def test_dashboard_keeps_four_columns_when_space_is_available(self):
        self.assertEqual(dashboard_columns(1420), 4)
        self.assertEqual(dashboard_columns(1520), 4)


class DashboardStatsTests(unittest.TestCase):
    def test_stats_are_clamped_and_rate_is_derived(self):
        snapshot = SimpleNamespace(
            total_strings=100,
            translated_strings=120,
            ok_strings=90,
            failed_strings=5,
            start_time=100.0,
        )
        stats = stats_from_snapshot(snapshot, now=160.0, eta_text="20 сек")
        self.assertEqual(stats.processed, 100)
        self.assertEqual(stats.remaining_lines, 0)
        self.assertEqual(stats.percent, 100.0)
        self.assertAlmostEqual(stats.lines_per_minute, 100.0)
        self.assertEqual(stats.eta_text, "20 сек")

    def test_paused_seconds_are_not_counted_in_dashboard_rate(self):
        snapshot = SimpleNamespace(
            total_strings=100,
            translated_strings=10,
            ok_strings=10,
            failed_strings=0,
            start_time=100.0,
            paused_seconds=50.0,
        )
        stats = stats_from_snapshot(snapshot, now=160.0)
        self.assertAlmostEqual(stats.elapsed_seconds, 10.0)
        self.assertAlmostEqual(stats.lines_per_minute, 60.0)

    def test_zero_progress_has_no_fake_percentages(self):
        snapshot = SimpleNamespace(
            total_strings=0,
            translated_strings=0,
            ok_strings=0,
            failed_strings=0,
            start_time=None,
        )
        stats = stats_from_snapshot(snapshot, now=10.0)
        self.assertEqual(stats.percent, 0.0)
        self.assertEqual(stats.success_percent, 0.0)
        self.assertEqual(stats.error_percent, 0.0)
        self.assertEqual(stats.lines_per_minute, 0.0)

    def test_duration_format(self):
        self.assertEqual(format_duration(9), "00:09")
        self.assertEqual(format_duration(125), "02:05")
        self.assertEqual(format_duration(3661), "01:01:01")


class RuntimeStatusPresentationTests(unittest.TestCase):
    def test_generated_dashboard_metrics_are_removed_from_task_status(self):
        text = (
            "[Моды 4/513] Переведено: 1169/123755 | "
            "Обработано: 1173/123755 | Ошибки: 4 | "
            "KoboldCPP: пакет 15 | Осталось: 2 ч 13 мин"
        )
        self.assertEqual(compact_runtime_status(text), "KoboldCPP: пакет 15")

    def test_ordinary_status_is_preserved(self):
        self.assertEqual(compact_runtime_status("Остановлено"), "Остановлено")


class EngineReadinessTests(unittest.TestCase):
    def test_google_is_configuration_ready(self):
        self.assertEqual(engine_readiness(_Config({}), "Google"), (True, "Google готов"))

    def test_deepl_requires_key(self):
        self.assertFalse(engine_readiness(_Config({}), "DeepL")[0])
        cfg = _Config({("API", "deepl_key"): "secret"})
        self.assertTrue(engine_readiness(cfg, "DeepL")[0])

    def test_openrouter_requires_key_and_model(self):
        self.assertFalse(engine_readiness(_Config({}), "OpenRouter")[0])
        cfg = _Config({("OPENROUTER", "api_key"): "x", ("OPENROUTER", "model"): "model/id"})
        self.assertTrue(engine_readiness(cfg, "OpenRouter")[0])

    def test_local_ai_requires_existing_model(self):
        self.assertFalse(engine_readiness(_Config({}), "Локальный ИИ")[0])
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.gguf"
            model.write_bytes(b"x")
            cfg = _Config({("AI", "model_path"): str(model)})
            ready, text = engine_readiness(cfg, "Локальный ИИ")
            self.assertTrue(ready)
            self.assertIn("model.gguf", text)


class SourceRootDetectionTests(unittest.TestCase):
    def test_detects_all_supported_top_level_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("mods", "config", "kubejs", "defaultconfigs"):
                (root / name).mkdir()
            self.assertEqual(
                detected_source_roots(root),
                ("mods", "config", "kubejs", "defaultconfigs"),
            )


if __name__ == "__main__":
    unittest.main()
