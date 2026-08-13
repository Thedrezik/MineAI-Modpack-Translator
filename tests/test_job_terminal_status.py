import unittest
import tempfile
from unittest import mock

from mineai.constants import LANGUAGES
from mineai.runtime.job import TranslationJob, TranslationOptions


class TerminalStopStatusTests(unittest.TestCase):
    @staticmethod
    def _options() -> TranslationOptions:
        return TranslationOptions(
            mc_dir="C:/Minecraft",
            language_label="Русский",
            mc_version="1.20.1",
            output_mode="inplace",
            pack_name="MineAI_Pack",
            engine="google",
            google_mode="single",
            ai_mode="safe",
            ai_batch=20,
            ai_provider="local",
            process_mode="append",
            translate_mods=False,
            translate_books=False,
            translate_quests=True,
        )

    @staticmethod
    def _job(*, progress: float):
        config = mock.Mock()
        config.getboolean.return_value = True
        state = mock.Mock()
        state.should_run.return_value = False
        state.line_progress.return_value = progress
        state.get_full_status.return_value = "status"
        cache_std = mock.Mock()
        cache_ai = mock.Mock()
        on_log = mock.Mock()
        on_status = mock.Mock()
        job = TranslationJob(
            config,
            cache_std,
            cache_ai,
            state,
            on_log=on_log,
            on_status=on_status,
            on_row=mock.Mock(),
        )
        return job, state, cache_std, on_log, on_status

    def test_stopped_analysis_preserves_current_progress(self) -> None:
        job, _state, _cache, _log, on_status = self._job(progress=0.37)
        analyzer = mock.Mock()
        analyzer.analyze.return_value = (100, 25)

        with mock.patch("mineai.runtime.job.ModpackAnalyzer", return_value=analyzer):
            job.run_analysis(self._options())

        on_status.assert_called_with("Остановлено", 0.37)
        self.assertNotIn(mock.call("Готово", 1.0), on_status.call_args_list)

    def test_stopped_translation_preserves_current_progress(self) -> None:
        job, state, cache_std, _log, on_status = self._job(progress=0.42)

        with (
            mock.patch("mineai.runtime.job.discover_jar_files", return_value=[]),
            mock.patch(
                "mineai.runtime.job.discover_loose_lang_files",
                return_value=["C:/Minecraft/config/ftbquests/lang/en_us.json"],
            ),
            mock.patch("mineai.runtime.job.discover_snbt_files", return_value=[]),
            mock.patch("mineai.runtime.job.discover_bq_files", return_value=[]),
            mock.patch("mineai.runtime.job.StringEstimator") as estimator_cls,
            mock.patch("mineai.runtime.job.TranslationService"),
            mock.patch("mineai.runtime.job.JarProcessor"),
            mock.patch("mineai.runtime.job.LooseJsonProcessor"),
            mock.patch("mineai.runtime.job.SnbtProcessor"),
            mock.patch("mineai.runtime.job.BQProcessor"),
        ):
            estimator_cls.return_value.estimate.return_value = 10
            job.run_translation(self._options())

        cache_std.save.assert_called_once_with()
        state.line_progress.assert_called()
        on_status.assert_called_with("Остановлено", 0.42)
        self.assertNotIn(
            mock.call("Все задачи выполнены!", 1.0),
            on_status.call_args_list,
        )

    def test_success_reports_only_real_archives_and_modified_files(self) -> None:
        with tempfile.TemporaryDirectory() as mc_dir:
            options = self._options()
            options.mc_dir = mc_dir
            options.language_label = next(iter(LANGUAGES))
            options.output_mode = "resourcepack"

            config = mock.Mock()
            config.getboolean.return_value = False
            state = mock.Mock()
            state.should_run.return_value = True
            state.get_full_status.return_value = "status"
            state.line_progress.return_value = 1.0
            state.snapshot.return_value.failed_strings = 0
            logs: list[str] = []
            job = TranslationJob(
                config,
                mock.Mock(),
                mock.Mock(),
                state,
                on_log=lambda message, _tag: logs.append(message),
                on_status=mock.Mock(),
                on_row=mock.Mock(),
            )
            source = f"{mc_dir}/config/ftbquests/quests/lang/en_us.snbt"
            target = f"{mc_dir}/config/ftbquests/quests/lang/ru_ru.snbt"
            real_datapack = f"{mc_dir}/config/openloader/data/Real_Datapack.zip"
            installed_kubejs_file = (
                f"{mc_dir}/kubejs/data/paganbless/modonomicon/books/"
                "pagan_guide/book.json"
            )
            ghost_resourcepack = f"{mc_dir}/resourcepacks/Empty.zip"
            pack_writer = mock.Mock()
            pack_writer.rp_zip_path = ghost_resourcepack
            pack_writer.dp_zip_path = real_datapack
            pack_writer.datapack_install_mode = "openloader"
            pack_writer.datapack_installed_paths = [installed_kubejs_file]
            pack_writer.close.return_value = (None, real_datapack)
            snbt_processor = mock.Mock()
            snbt_processor.process.return_value = target

            with (
                mock.patch("mineai.runtime.job.discover_jar_files", return_value=[]),
                mock.patch("mineai.runtime.job.discover_loose_lang_files", return_value=[]),
                mock.patch("mineai.runtime.job.discover_snbt_files", return_value=[source]),
                mock.patch("mineai.runtime.job.discover_bq_files", return_value=[]),
                mock.patch("mineai.runtime.job.StringEstimator") as estimator_cls,
                mock.patch("mineai.runtime.job.TranslationService"),
                mock.patch("mineai.runtime.job.PackWriter", return_value=pack_writer),
                mock.patch("mineai.runtime.job.JarProcessor"),
                mock.patch("mineai.runtime.job.LooseJsonProcessor"),
                mock.patch("mineai.runtime.job.SnbtProcessor", return_value=snbt_processor),
                mock.patch("mineai.runtime.job.BQProcessor"),
            ):
                estimator_cls.return_value.estimate.return_value = 1
                job.run_translation(options)

        report = "\n".join(logs)
        self.assertIn(real_datapack, report)
        self.assertIn(target, report)
        self.assertIn(installed_kubejs_file, report)
        self.assertNotIn(ghost_resourcepack, report)
        self.assertNotIn("Включите ресурспак и датапак", report)

    def test_failed_strings_prevent_false_success_status(self) -> None:
        with tempfile.TemporaryDirectory() as mc_dir:
            options = self._options()
            options.mc_dir = mc_dir
            options.language_label = next(iter(LANGUAGES))

            config = mock.Mock()
            config.getboolean.return_value = False
            state = mock.Mock()
            state.should_run.return_value = True
            state.get_full_status.return_value = "status"
            state.line_progress.return_value = 1.0
            state.snapshot.return_value.failed_strings = 3
            logs: list[str] = []
            on_status = mock.Mock()
            job = TranslationJob(
                config,
                mock.Mock(),
                mock.Mock(),
                state,
                on_log=lambda message, _tag: logs.append(message),
                on_status=on_status,
                on_row=mock.Mock(),
            )
            source = f"{mc_dir}/config/ftbquests/quests/lang/en_us.snbt"
            snbt_processor = mock.Mock()
            snbt_processor.process.return_value = None

            with (
                mock.patch("mineai.runtime.job.discover_jar_files", return_value=[]),
                mock.patch("mineai.runtime.job.discover_loose_lang_files", return_value=[]),
                mock.patch("mineai.runtime.job.discover_snbt_files", return_value=[source]),
                mock.patch("mineai.runtime.job.discover_bq_files", return_value=[]),
                mock.patch("mineai.runtime.job.StringEstimator") as estimator_cls,
                mock.patch("mineai.runtime.job.TranslationService"),
                mock.patch("mineai.runtime.job.JarProcessor"),
                mock.patch("mineai.runtime.job.LooseJsonProcessor"),
                mock.patch("mineai.runtime.job.SnbtProcessor", return_value=snbt_processor),
                mock.patch("mineai.runtime.job.BQProcessor"),
            ):
                estimator_cls.return_value.estimate.return_value = 3
                job.run_translation(options)

        report = "\n".join(logs)
        self.assertIn("не переведено строк — 3", report)
        self.assertNotIn("ПЕРЕВОД УСПЕШНО ЗАВЕРШЕН", report)
        on_status.assert_called_with("Завершено с ошибками", 1.0)


if __name__ == "__main__":
    unittest.main()
