import threading
import time
import traceback
from dataclasses import dataclass

from mineai.cache import TranslationCache
from mineai.config import ConfigManager
from mineai.constants import LANGUAGES
from mineai.engines.base import EngineCallbacks
from mineai.engines.service import TranslationService
from mineai.output.pack_writer import PackWriter
from mineai.processors.analyzer import ModpackAnalyzer
from mineai.processors.discovery import discover_jar_files, discover_loose_lang_files, discover_snbt_files, discover_bq_files
from mineai.processors.estimator import StringEstimator
from mineai.processors.jar import JarProcessor
from mineai.processors.bq_json import BQProcessor
from mineai.processors.loose_json import LooseJsonProcessor
from mineai.processors.snbt import SnbtProcessor
from mineai.runtime.ai_launcher import AiLauncher
from mineai.runtime.state import JobState


@dataclass
class TranslationOptions:
    mc_dir: str
    language_label: str
    mc_version: str
    output_mode: str  # resourcepack | inplace
    pack_name: str
    engine: str  # google | deepl | ai
    google_mode: str
    ai_mode: str
    ai_batch: int
    ai_provider: str  # local | openrouter
    process_mode: str  # append | skip | force
    translate_mods: bool
    translate_books: bool
    translate_quests: bool


class TranslationJob:
    PROGRESS_STATUS_INTERVAL_SECONDS = 0.4

    def __init__(
        self,
        config: ConfigManager,
        cache_std: TranslationCache,
        cache_ai: TranslationCache,
        state: JobState,
        *,
        on_log,
        on_status,
        on_row,
    ) -> None:
        self.config = config
        self.cache_std = cache_std
        self.cache_ai = cache_ai
        self.state = state
        self.on_log = on_log
        self.on_status = on_status
        self.on_row = on_row
        self.ai_launcher = AiLauncher(config)
        self._progress_status_lock = threading.Lock()
        self._last_progress_status_at = 0.0

    def _reset_progress_status_throttle(self) -> None:
        with self._progress_status_lock:
            self._last_progress_status_at = 0.0

    def _on_progress(self, count: int = 1) -> None:
        self.state.increment_translated(count)
        now = time.monotonic()

        with self._progress_status_lock:
            if (
                self._last_progress_status_at > 0
                and now - self._last_progress_status_at
                < self.PROGRESS_STATUS_INTERVAL_SECONDS
            ):
                return
            self._last_progress_status_at = now

        self.on_status(
            self.state.get_full_status(),
            self.state.line_progress(),
        )

    def _on_metric(self, name: str, count: int = 1) -> None:
        if name == "ok":
            self.state.mark_ok(count)
        elif name == "failed":
            self.state.mark_failed(count)
        elif name == "cached":
            self.state.mark_cached(count)
        elif name == "fallback":
            self.state.mark_fallback(count)
        elif name == "protected":
            self.state.mark_protected(count)

    def _callbacks(self) -> EngineCallbacks:
        return EngineCallbacks(
            should_run=self.state.should_run,
            wait_if_paused=self.state.wait_if_paused,
            on_log=self.on_log,
            on_status=lambda msg: self.on_status(
                self.state.get_full_status(msg),
                None,
            ),
            on_progress=self._on_progress,
            on_metric=self._on_metric,
        )

    def run_analysis(self, options: TranslationOptions) -> None:
        lang = LANGUAGES[options.language_label]
        analyzer = ModpackAnalyzer(self.state)
        self.on_log(f"🚀 Сканирование сборки ({lang['name']})...\n", "yellow")
        self.on_log(f"{'ФАЙЛ / МОД':<37}{'ТИП':<15}{'СТРОКИ':<12}ПРОГРЕСС", "white")
        self.on_log("-" * 75, "dim")

        total_en, total_tr = analyzer.analyze(
            options.mc_dir,
            target_lang=lang,
            translate_mods=options.translate_mods,
            translate_books=options.translate_books,
            translate_quests=options.translate_quests,
            on_row=self.on_row,
            on_log=self.on_log,
            on_status=lambda text, val: self.on_status(text, val),
        )

        self.on_log("-" * 75, "dim")
        if not self.state.should_run():
            self.on_log("🛑 АНАЛИЗ ПРЕРВАН", "red")
            self.on_status("Остановлено", self.state.line_progress())
            return
        elif total_en > 0:
            pct = int(total_tr / total_en * 100)
            color = "green" if pct >= 90 else ("yellow" if pct >= 50 else "red")
            self.on_log(f"✅ АНАЛИЗ ЗАВЕРШЕН! Готовность: {pct}% | Строк: {total_en}", color)
        else:
            self.on_log("❌ Нечего переводить!", "red")
        self.on_status("Готово", 1.0)

    def run_translation(self, options: TranslationOptions) -> None:
        lang = LANGUAGES[options.language_label]
        cache = self.cache_ai if options.engine == "ai" else self.cache_std

        if options.engine == "deepl" and not self.config.get("API", "deepl_key").strip():
            self.on_log("❌ Введите ключ DeepL в настройках!", "red")
            return
        if options.engine == "ai":
            if options.ai_provider == "openrouter":
                if not self.config.get("OPENROUTER", "api_key").strip():
                    self.on_log("❌ Укажите API-ключ OpenRouter в настройках!", "red")
                    return
                if not self.config.get("OPENROUTER", "model").strip():
                    self.on_log("❌ Укажите ID модели OpenRouter в настройках!", "red")
                    return
            elif not self.config.get("AI", "model_path").strip():
                self.on_log("❌ Выберите модель .gguf в настройках!", "red")
                return

        jars = discover_jar_files(options.mc_dir) if (options.translate_mods or options.translate_books) else []
        loose = discover_loose_lang_files(options.mc_dir) if (options.translate_mods or options.translate_quests) else []
        snbt = discover_snbt_files(options.mc_dir) if options.translate_quests else []
        bq_files = discover_bq_files(options.mc_dir) if options.translate_quests else []

        if not jars and not loose and not snbt and not bq_files:
            self.on_log("❌ Нечего переводить!", "red")
            return

        self.on_log("📊 Подсчёт строк...", "yellow")
        estimator = StringEstimator(self.state)
        estimated_count = estimator.estimate(
            jars,
            loose,
            snbt,
            bq_files,
            target_lang=lang,
            mode=options.process_mode,
            translate_mods=options.translate_mods,
            translate_books=options.translate_books,
            translate_quests=options.translate_quests,
            smart_glue=self.config.getboolean("GENERAL", "smart_glue"),
        )
        self.state.set_total_strings(estimated_count)
        self.on_log(f"   Найдено: {estimated_count}", "cyan")

        if options.engine == "ai" and options.ai_provider == "local":
            if not self.ai_launcher.ensure_running(
                self.state.should_run,
                lambda msg: self.on_status(msg, None),
                self.on_log,
            ):
                return
        elif options.engine == "ai" and options.ai_provider == "openrouter":
            model = self.config.get("OPENROUTER", "model")
            self.on_log(f"🌐 OpenRouter: {model}", "cyan")

        pack_writer: PackWriter | None = None
        failed = False
        processing_failed = False
        failed_files = 0
        total_items = len(jars) + len(loose) + len(snbt) + len(bq_files)
        done = 0

        def process_file(path: str, file_type: str, action) -> None:
            nonlocal done, failed_files
            try:
                action()
            except Exception:
                failed_files += 1
                self.on_log(
                    f"\n❌ Ошибка файла {path}:\n{traceback.format_exc()}",
                    "red",
                )
            finally:
                done += 1
                self.state.update_file_progress(file_type, done, total_items)
                self.on_status(
                    self.state.get_full_status(),
                    self.state.line_progress(),
                )

        try:
            if options.output_mode == "resourcepack":
                pack_writer = PackWriter(
                    options.mc_dir,
                    options.pack_name,
                    options.mc_version,
                    lang["name"],
                )
                self.on_log(f"📦 Ресурспак: {pack_writer.rp_zip_path}", "cyan")
                self.on_log(f"📂 Датапак: {pack_writer.dp_zip_path}", "magenta")

            service = TranslationService(
                options.engine,
                cache,
                self.config,
                google_mode=options.google_mode,
                ai_mode=options.ai_mode,
                ai_batch=options.ai_batch,
                ai_provider=options.ai_provider,
            )
            callbacks = self._callbacks()
            jar_proc = JarProcessor(service, self.state, callbacks)
            loose_proc = LooseJsonProcessor(service, self.state, callbacks)
            snbt_proc = SnbtProcessor(service, self.state, callbacks)
            bq_proc = BQProcessor(service, self.state, callbacks)

            self._reset_progress_status_throttle()
            self.state.begin_progress()
            self.on_log(f"🚀 ЗАПУСК ПЕРЕВОДА ({lang['name']})...\n", "yellow")

            for path in jars:
                if not self.state.should_run():
                    break
                self.state.wait_if_paused()
                if not self.state.should_run():
                    break
                process_file(
                    path,
                    "Моды",
                    lambda path=path: jar_proc.process(
                        path,
                        target_lang=lang,
                        mode=options.process_mode,
                        output_mode=options.output_mode,
                        translate_mods=options.translate_mods,
                        translate_books=options.translate_books,
                        pack_writer=pack_writer,
                    ),
                )

            for path in loose:
                if not self.state.should_run():
                    break
                self.state.wait_if_paused()
                if not self.state.should_run():
                    break
                process_file(
                    path,
                    "Словари",
                    lambda path=path: loose_proc.process(
                        path,
                        options.mc_dir,
                        target_lang=lang,
                        mode=options.process_mode,
                        output_mode=options.output_mode,
                        pack_writer=pack_writer,
                    ),
                )

            for path in snbt:
                if not self.state.should_run():
                    break
                self.state.wait_if_paused()
                if not self.state.should_run():
                    break
                process_file(
                    path,
                    "Квесты",
                    lambda path=path: snbt_proc.process(
                        path,
                        target_lang=lang,
                        mode=options.process_mode,
                    ),
                )

            for path in bq_files:
                if not self.state.should_run():
                    break
                self.state.wait_if_paused()
                if not self.state.should_run():
                    break
                process_file(
                    path,
                    "BQ",
                    lambda path=path: bq_proc.process(
                        path,
                        target_lang=lang,
                        mode=options.process_mode,
                    ),
                )
        except Exception:
            failed = True
            processing_failed = True
            self.on_log(
                f"\n❌ КРИТИЧЕСКАЯ ОШИБКА:\n{traceback.format_exc()}",
                "red",
            )
        finally:
            try:
                cache.save()
            except Exception:
                failed = True
                self.on_log(
                    f"\n❌ Не удалось сохранить кэш:\n{traceback.format_exc()}",
                    "red",
                )

            if pack_writer:
                try:
                    if processing_failed or not self.state.should_run():
                        pack_writer.abort()
                    else:
                        pack_writer.close()
                except Exception:
                    failed = True
                    self.on_log(
                        f"\n❌ Не удалось завершить выходные архивы:\n"
                        f"{traceback.format_exc()}",
                        "red",
                    )

        if failed:
            self.on_status("Ошибка перевода", 1.0)
        elif not self.state.should_run():
            self.on_log("\n🛑 ОСТАНОВЛЕНО.", "red")
            self.on_status("Остановлено", self.state.line_progress())
        elif failed_files:
            self.on_log(
                f"\n⚠️ ЗАВЕРШЕНО С ОШИБКАМИ: пропущено файлов — {failed_files}.",
                "yellow",
            )
            self.on_status("Завершено с ошибками", 1.0)
        else:
            self.on_log("\n✅ ПЕРЕВОД УСПЕШНО ЗАВЕРШЕН!", "green")
            if options.output_mode == "resourcepack":
                self.on_log("💡 Включите ресурспак и датапак в игре.", "yellow")
            self.on_status("Все задачи выполнены!", 1.0)

    def stop(self) -> None:
        self.state.stop()
        self.ai_launcher.terminate()
