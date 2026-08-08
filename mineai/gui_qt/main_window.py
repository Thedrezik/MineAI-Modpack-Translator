"""Premium PyQt6 dashboard for MineAI Translator.

This module is an alternate presentation layer for the existing beta runtime.
TranslationJob, JobState, engines, processors, caches and PackWriter remain the
single source of truth for translation behavior.
"""

from __future__ import annotations

from html import escape
from pathlib import Path
import sys
import threading
import traceback

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from mineai import __version__
from mineai.cache import load_both_caches
from mineai.config import settings
from mineai.constants import LANGUAGES, MC_VERSIONS
from mineai.runtime.job import TranslationJob, TranslationOptions
from mineai.runtime.state import JobState
from mineai.gui_qt.bridge import RuntimeSignals
from mineai.gui_qt.dialogs import MigrationDialog, PromptEditorDialog, SettingsDialog
from mineai.gui_qt.theme import APP_QSS
from mineai.gui_qt.view_model import ENGINE_OPTIONS, engine_readiness, format_duration, stats_from_snapshot
from mineai.gui_qt.widgets import Card, LabeledValue, SegmentedProgressBar, StatCard, StatusPill


def _resolve_icon_path() -> str | None:
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        candidates.extend((base / "icon.png", base / "icon.ico"))
        candidates.extend((Path(sys.executable).parent / "icon.png", Path(sys.executable).parent / "icon.ico"))
    cwd = Path.cwd()
    candidates.extend((cwd / "icon.png", cwd / "icon.ico"))
    return str(next((path for path in candidates if path.exists()), "")) or None


LOG_COLORS = {
    "green": "#4ADE80",
    "lime": "#86EFAC",
    "yellow": "#FBBF24",
    "gold": "#FACC15",
    "orange": "#FB923C",
    "red": "#F87171",
    "pink": "#FB7185",
    "cyan": "#67E8F9",
    "blue": "#60A5FA",
    "magenta": "#C084FC",
    "dim": "#64748B",
    "gray": "#94A3B8",
    "white": "#E2E8F0",
}


class TranslatorQtWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"MineAI Modpack Translator — {__version__}")
        icon_path = _resolve_icon_path()
        if icon_path:
            self.setWindowIcon(QIcon(icon_path))
        self.resize(1520, 940)
        self.setMinimumSize(1180, 760)
        self.setStyleSheet(APP_QSS)

        self.job_state = JobState()
        self.cache_std, self.cache_ai, polish_total = load_both_caches()
        self._job: TranslationJob | None = None
        self._worker: threading.Thread | None = None
        self._closing = False
        self._allow_close = False
        self._log_entries: list[tuple[str, str]] = []

        self.signals = RuntimeSignals()
        self.signals.log.connect(self._append_log)
        self.signals.status.connect(self._set_status)
        self.signals.row.connect(self._append_analysis_row)
        self.signals.worker_finished.connect(self._worker_finished)
        self.signals.worker_failed.connect(self._worker_failed)

        self._build_ui()
        self._restore_state_from_config()
        self._refresh_folder_state()
        self._refresh_engine_state()
        self._refresh_system_readiness()
        self._refresh_footer()

        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._refresh_runtime_dashboard)
        self.refresh_timer.start(400)

        if polish_total:
            self._append_log(f"Кэш проверен: исправлено/удалено ошибок: {polish_total}.", "magenta")

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_header())

        body = QWidget()
        body.setObjectName("DashboardBody")
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(20, 14, 20, 14)
        body_layout.setSpacing(16)
        body_layout.addWidget(self._build_sidebar())
        body_layout.addWidget(self._build_content(), 1)
        outer.addWidget(body, 1)
        outer.addWidget(self._build_footer())

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("Header")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(22, 12, 22, 12)
        layout.setSpacing(10)

        logo = QLabel("◈")
        icon_path = _resolve_icon_path()
        if icon_path and icon_path.lower().endswith(".png"):
            pixmap = QPixmap(icon_path)
            if not pixmap.isNull():
                logo.setPixmap(pixmap.scaled(32, 32, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            logo.setStyleSheet("color: #8B6BE5; font-size: 28px; font-weight: 800;")
        logo.setFixedSize(36, 36)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(logo)

        titles = QVBoxLayout()
        titles.setSpacing(0)
        title = QLabel("MineAI Modpack Translator")
        title.setObjectName("AppTitle")
        version = QLabel(__version__)
        version.setObjectName("VersionLabel")
        titles.addWidget(title)
        titles.addWidget(version)
        layout.addLayout(titles)
        layout.addStretch(1)

        self.system_pill = StatusPill()
        layout.addWidget(self.system_pill)
        layout.addSpacing(14)

        for text, callback in (
            ("⚙  Настройки", self._open_settings),
            ("💬  Промпты", self._open_prompts),
            ("⇄  Миграция", self._open_migration),
        ):
            button = QPushButton(text)
            button.setObjectName("HeaderButton")
            button.clicked.connect(callback)
            layout.addWidget(button)
            if text.startswith("⚙"):
                self.settings_button = button
            elif "Промпты" in text:
                self.prompts_button = button
            else:
                self.migration_button = button
        return header

    def _build_sidebar(self) -> QWidget:
        host = QWidget()
        host.setObjectName("SidebarHost")
        host.setFixedWidth(390)
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(10)

        scroll = QScrollArea()
        scroll.setObjectName("Sidebar")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(10)
        scroll.setWidget(content)

        layout.addWidget(self._build_project_card())
        layout.addWidget(self._build_engine_card())
        layout.addWidget(self._build_scope_card())
        layout.addWidget(self._build_mode_card())
        layout.addStretch(1)

        host_layout.addWidget(scroll, 1)
        # Runtime actions remain permanently visible, like the reference dashboard.
        host_layout.addWidget(self._build_action_card())
        return host

    def _build_project_card(self) -> QWidget:
        card = Card("Проект")
        label = QLabel("Папка Minecraft")
        label.setObjectName("FieldLabel")
        card.body.addWidget(label)

        folder_row = QHBoxLayout()
        folder_row.setSpacing(6)
        self.folder_edit = QLineEdit()
        self.folder_edit.setReadOnly(True)
        self.folder_button = QPushButton("📁")
        self.folder_button.setFixedWidth(42)
        self.folder_button.clicked.connect(self._select_folder)
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(self.folder_button)
        card.body.addLayout(folder_row)

        self.folder_state = QLabel("Папка не выбрана")
        self.folder_state.setObjectName("MutedLabel")
        card.body.addWidget(self.folder_state)

        selectors = QGridLayout()
        selectors.setHorizontalSpacing(8)
        version_label = QLabel("Версия Minecraft")
        version_label.setObjectName("FieldLabel")
        language_label = QLabel("Язык перевода")
        language_label.setObjectName("FieldLabel")
        self.version_combo = QComboBox()
        self.version_combo.addItems(MC_VERSIONS)
        self.language_combo = QComboBox()
        self.language_combo.addItems(list(LANGUAGES.keys()))
        self.language_combo.currentTextChanged.connect(self._refresh_system_readiness)
        selectors.addWidget(version_label, 0, 0)
        selectors.addWidget(self.version_combo, 1, 0)
        selectors.addWidget(language_label, 2, 0)
        selectors.addWidget(self.language_combo, 3, 0)
        card.body.addLayout(selectors)
        return card

    def _build_engine_card(self) -> QWidget:
        card = Card("Движок перевода")
        row = QHBoxLayout()
        row.setSpacing(8)
        label = QLabel("Движок")
        label.setObjectName("FieldLabel")
        self.engine_combo = QComboBox()
        self.engine_combo.addItems(list(ENGINE_OPTIONS.keys()))
        self.engine_combo.currentTextChanged.connect(self._engine_changed)
        row.addWidget(label)
        row.addWidget(self.engine_combo, 1)
        card.body.addLayout(row)

        ready = QFrame()
        ready.setObjectName("ReadyBox")
        ready_layout = QHBoxLayout(ready)
        ready_layout.setContentsMargins(9, 6, 7, 6)
        self.engine_ready_label = QLabel("Проверка…")
        self.engine_ready_label.setObjectName("ReadyText")
        configure = QPushButton("Настроить")
        configure.setFixedWidth(92)
        configure.clicked.connect(self._open_settings)
        ready_layout.addWidget(self.engine_ready_label, 1)
        ready_layout.addWidget(configure)
        card.body.addWidget(ready)

        self.google_options = QWidget()
        google_layout = QHBoxLayout(self.google_options)
        google_layout.setContentsMargins(0, 0, 0, 0)
        google_layout.addWidget(QLabel("Режим Google"))
        self.google_mode_combo = QComboBox()
        self.google_mode_combo.addItem("Построчно", "single")
        self.google_mode_combo.addItem("Пачками", "batch")
        google_layout.addWidget(self.google_mode_combo, 1)
        card.body.addWidget(self.google_options)

        self.ai_options = QWidget()
        ai_grid = QGridLayout(self.ai_options)
        ai_grid.setContentsMargins(0, 0, 0, 0)
        ai_grid.setHorizontalSpacing(8)
        ai_grid.setVerticalSpacing(7)
        ai_grid.addWidget(QLabel("Режим AI"), 0, 0)
        self.ai_mode_combo = QComboBox()
        self.ai_mode_combo.addItem("Стандартный", "safe")
        self.ai_mode_combo.addItem("Контекст + лор", "context")
        ai_grid.addWidget(self.ai_mode_combo, 0, 1)
        ai_grid.addWidget(QLabel("Пакет"), 1, 0)
        self.ai_batch_spin = QSpinBox()
        self.ai_batch_spin.setRange(1, 40)
        self.ai_batch_spin.setValue(20)
        self.ai_batch_spin.valueChanged.connect(self._refresh_footer)
        ai_grid.addWidget(self.ai_batch_spin, 1, 1)
        self.ai_fallback = QCheckBox("Fallback через Google")
        self.ai_fallback.setChecked(settings.getboolean("AI", "fallback_google"))
        ai_grid.addWidget(self.ai_fallback, 2, 0, 1, 2)
        card.body.addWidget(self.ai_options)
        return card

    def _build_scope_card(self) -> QWidget:
        card = Card("Области перевода")
        self.scope_mods = QCheckBox("Моды (.jar)")
        self.scope_books = QCheckBox("Книги и тексты")
        self.scope_quests = QCheckBox("Квесты (FTB / KubeJS / SNBT)")
        for checkbox in (self.scope_mods, self.scope_books, self.scope_quests):
            checkbox.setChecked(True)
            checkbox.stateChanged.connect(self._refresh_system_readiness)
            card.body.addWidget(checkbox)
        return card

    def _build_mode_card(self) -> QWidget:
        card = Card("Режим перевода")
        mode_label = QLabel("Обработка")
        mode_label.setObjectName("FieldLabel")
        card.body.addWidget(mode_label)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(6)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_buttons: dict[str, QPushButton] = {}
        for value, text in (("append", "Append"), ("skip", "Skip"), ("force", "Force")):
            button = QPushButton(text)
            button.setObjectName("SegmentButton")
            button.setCheckable(True)
            if value == "append":
                button.setChecked(True)
            self.mode_group.addButton(button)
            self.mode_buttons[value] = button
            mode_row.addWidget(button, 1)
        card.body.addLayout(mode_row)

        output_label = QLabel("Выход")
        output_label.setObjectName("FieldLabel")
        card.body.addWidget(output_label)
        output_row = QHBoxLayout()
        output_row.setSpacing(6)
        self.output_group = QButtonGroup(self)
        self.output_group.setExclusive(True)
        self.output_rp = QPushButton("Resource Pack")
        self.output_inplace = QPushButton("In-place")
        for button in (self.output_rp, self.output_inplace):
            button.setObjectName("SegmentButton")
            button.setCheckable(True)
            self.output_group.addButton(button)
            output_row.addWidget(button, 1)
        self.output_rp.setChecked(True)
        self.output_rp.toggled.connect(self._output_changed)
        card.body.addLayout(output_row)

        self.pack_name = QLineEdit("MineAI_Pack")
        self.pack_name.setPlaceholderText("Имя Resource Pack / Datapack")
        card.body.addWidget(self.pack_name)
        return card

    def _build_action_card(self) -> QWidget:
        card = Card("Действия")
        action_row = QHBoxLayout()
        self.analyze_button = QPushButton("Анализ")
        self.start_button = QPushButton("🚀  НАЧАТЬ ПЕРЕВОД")
        self.start_button.setObjectName("PrimaryButton")
        self.analyze_button.clicked.connect(self._start_analysis)
        self.start_button.clicked.connect(self._start_translation)
        action_row.addWidget(self.analyze_button)
        action_row.addWidget(self.start_button, 1)
        card.body.addLayout(action_row)

        run_row = QHBoxLayout()
        self.pause_button = QPushButton("⏸  Пауза")
        self.pause_button.setObjectName("WarningButton")
        self.stop_button = QPushButton("◉  Стоп")
        self.stop_button.setObjectName("DangerButton")
        self.pause_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.pause_button.clicked.connect(self._toggle_pause)
        self.stop_button.clicked.connect(self._stop)
        run_row.addWidget(self.pause_button, 1)
        run_row.addWidget(self.stop_button, 1)
        card.body.addLayout(run_row)

        self.lock_hint = QLabel("🔒 Настройки блокируются во время активной задачи")
        self.lock_hint.setObjectName("MutedLabel")
        card.body.addWidget(self.lock_hint)
        return card

    def _build_content(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(self._build_status_card())
        layout.addWidget(self._build_task_card())
        layout.addWidget(self._build_log_card(), 1)
        return content

    def _build_status_card(self) -> QWidget:
        card = Card("Статус перевода")
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        self.kpi_processed = StatCard("Обработано", "KpiBlue")
        self.kpi_success = StatCard("Успешно", "KpiGreen")
        self.kpi_errors = StatCard("Ошибки", "KpiAmber")
        self.kpi_eta = StatCard("Осталось", "KpiViolet")
        for widget, glyph, color in (
            (self.kpi_processed, "▣", "#60A5FA"),
            (self.kpi_success, "✓", "#5EEAD4"),
            (self.kpi_errors, "!", "#FBBF24"),
            (self.kpi_eta, "◷", "#A78BFA"),
        ):
            widget.icon.setText(glyph)
            widget.icon.setStyleSheet(
                f"background: transparent; border: none; color: {color}; "
                "font-size: 18px; font-weight: 800;"
            )
        for col, widget in enumerate((self.kpi_processed, self.kpi_success, self.kpi_errors, self.kpi_eta)):
            grid.addWidget(widget, 0, col)
            grid.setColumnStretch(col, 1)
        card.body.addLayout(grid)
        return card

    def _build_task_card(self) -> QWidget:
        card = Card("Текущая задача")
        title_row = QHBoxLayout()
        self.task_title = QLabel("Ожидание запуска")
        self.task_title.setObjectName("StrongLabel")
        self.task_percent = QLabel("0.0%")
        self.task_percent.setObjectName("KpiValue")
        title_row.addWidget(self.task_title, 1)
        title_row.addWidget(self.task_percent)
        card.body.addLayout(title_row)

        self.task_status = QLabel("Готов к работе")
        self.task_status.setObjectName("MutedLabel")
        self.task_status.setWordWrap(True)
        card.body.addWidget(self.task_status)

        self.segmented_progress = SegmentedProgressBar()
        card.body.addWidget(self.segmented_progress)

        metrics = QHBoxLayout()
        metrics.setSpacing(18)
        self.task_lines = LabeledValue("Строка:")
        self.task_speed = LabeledValue("Скорость:")
        self.task_elapsed = LabeledValue("Прошло:")
        self.task_remaining = LabeledValue("Осталось:")
        for widget in (self.task_lines, self.task_speed, self.task_elapsed, self.task_remaining):
            metrics.addWidget(widget)
        metrics.addStretch(1)
        card.body.addLayout(metrics)
        return card

    def _build_log_card(self) -> QWidget:
        card = Card("Журнал")
        toolbar = QHBoxLayout()
        toolbar.addStretch(1)
        self.log_filter = QComboBox()
        self.log_filter.addItem("Все уровни", "all")
        self.log_filter.addItem("Информация", "info")
        self.log_filter.addItem("Успешно", "success")
        self.log_filter.addItem("Предупреждения", "warning")
        self.log_filter.addItem("Ошибки", "error")
        self.log_filter.currentIndexChanged.connect(self._render_log)
        clear = QPushButton("🗑  Очистить")
        save = QPushButton("⇩  Сохранить")
        clear.clicked.connect(self._clear_log)
        save.clicked.connect(self._save_log)
        toolbar.addWidget(self.log_filter)
        toolbar.addWidget(clear)
        toolbar.addWidget(save)
        card.body.addLayout(toolbar)

        self.log_view = QTextEdit()
        self.log_view.setObjectName("LogView")
        self.log_view.setReadOnly(True)
        card.body.addWidget(self.log_view, 1)
        return card

    def _build_footer(self) -> QWidget:
        footer = QFrame()
        footer.setObjectName("Footer")
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(24, 8, 24, 8)
        self.footer_status = QLabel("●  Готов к работе")
        self.footer_status.setObjectName("ReadyText")
        self.footer_details = QLabel("")
        self.footer_details.setObjectName("MutedLabel")
        layout.addWidget(self.footer_status)
        layout.addStretch(1)
        layout.addWidget(self.footer_details)
        return footer

    def _restore_state_from_config(self) -> None:
        self.folder_edit.setText(settings.get("GENERAL", "mc_dir"))
        if "1.20.1" in MC_VERSIONS:
            self.version_combo.setCurrentText("1.20.1")
        self.language_combo.setCurrentText("Русский")
        provider = settings.get("AI", "ai_provider") or "local"
        if provider == "openrouter":
            self.engine_combo.setCurrentText("OpenRouter")
        else:
            self.engine_combo.setCurrentText("Google")
        self.ai_fallback.setChecked(settings.getboolean("AI", "fallback_google"))
        self._engine_changed(self.engine_combo.currentText())

    def _select_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Папка Minecraft", settings.get("GENERAL", "mc_dir"))
        if not path:
            return
        settings.set("GENERAL", "mc_dir", path)
        self.folder_edit.setText(path)
        self._refresh_folder_state()
        self._refresh_system_readiness()

    def _refresh_folder_state(self) -> None:
        raw = settings.get("GENERAL", "mc_dir").strip()
        if not raw:
            self.folder_state.setText("Папка не выбрана")
            self.folder_state.setObjectName("MutedLabel")
        else:
            root = Path(raw)
            if not root.is_dir():
                self.folder_state.setText("✕ Папка недоступна")
                self.folder_state.setObjectName("DangerText")
            else:
                markers = []
                if (root / "mods").is_dir():
                    markers.append("mods/ ✓")
                if (root / "config").is_dir():
                    markers.append("config/ ✓")
                suffix = "   " + "   ".join(markers) if markers else ""
                self.folder_state.setText("✓ Папка найдена" + suffix)
                self.folder_state.setObjectName("ReadyText")
        self.folder_state.style().unpolish(self.folder_state)
        self.folder_state.style().polish(self.folder_state)

    def _engine_changed(self, label: str) -> None:
        engine, _provider = ENGINE_OPTIONS[label]
        self.google_options.setVisible(engine == "google")
        self.ai_options.setVisible(engine == "ai")
        self._refresh_engine_state()
        self._refresh_system_readiness()

    def _refresh_engine_state(self) -> None:
        ready, text = engine_readiness(settings, self.engine_combo.currentText())
        self.engine_ready_label.setText(("✓ " if ready else "⚠ ") + text)
        self.engine_ready_label.setObjectName("ReadyText" if ready else "WarningText")
        self.engine_ready_label.style().unpolish(self.engine_ready_label)
        self.engine_ready_label.style().polish(self.engine_ready_label)

    def _refresh_system_readiness(self, *_args) -> None:
        raw = settings.get("GENERAL", "mc_dir").strip()
        if not raw or not Path(raw).is_dir():
            self.system_pill.set_ready(False, "Выберите папку Minecraft")
            return
        if not any((self.scope_mods.isChecked(), self.scope_books.isChecked(), self.scope_quests.isChecked())):
            self.system_pill.set_ready(False, "Выберите область перевода")
            return
        ready, text = engine_readiness(settings, self.engine_combo.currentText())
        if not ready:
            self.system_pill.set_ready(False, text)
            return
        self.system_pill.set_ready(True, "Все системы готовы")

    def _refresh_footer(self, *_args) -> None:
        workers = settings.getint("GENERAL", "google_workers", 5)
        retries = settings.getint("AI", "ai_retries", 3)
        batch = self.ai_batch_spin.value() if hasattr(self, "ai_batch_spin") else 20
        self.footer_details.setText(f"Потоки: {workers}   |   Пакет AI: {batch}   |   Ретраи AI: {retries}")

    def _output_changed(self, checked: bool) -> None:
        self.pack_name.setEnabled(checked)

    def _open_settings(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        dialog = SettingsDialog(settings, self._after_settings_saved, self)
        dialog.exec()

    def _after_settings_saved(self) -> None:
        self._refresh_engine_state()
        self._refresh_system_readiness()
        self._refresh_footer()

    def _open_prompts(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        PromptEditorDialog(self).exec()

    def _open_migration(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        mc_dir = settings.get("GENERAL", "mc_dir").strip()
        if not mc_dir or not Path(mc_dir).is_dir() or not (Path(mc_dir) / "mods").is_dir():
            QMessageBox.warning(self, "Миграция", "Для миграции выберите Minecraft instance с папкой mods.")
            return
        dialog = MigrationDialog(
            mc_dir,
            self.language_combo.currentText(),
            self.cache_std,
            self.cache_ai,
            lambda msg, tag="white": self.signals.log.emit(msg, tag),
            self,
        )
        dialog.exec()

    def _mode_value(self) -> str:
        for value, button in self.mode_buttons.items():
            if button.isChecked():
                return value
        return "append"

    def _translation_options(self) -> TranslationOptions:
        engine, provider = ENGINE_OPTIONS[self.engine_combo.currentText()]
        return TranslationOptions(
            mc_dir=settings.get("GENERAL", "mc_dir"),
            language_label=self.language_combo.currentText(),
            mc_version=self.version_combo.currentText(),
            output_mode="resourcepack" if self.output_rp.isChecked() else "inplace",
            pack_name=self.pack_name.text().strip() or "MineAI_Pack",
            engine=engine,
            google_mode=self.google_mode_combo.currentData() or "single",
            ai_mode=self.ai_mode_combo.currentData() or "safe",
            ai_batch=self.ai_batch_spin.value(),
            ai_provider=provider,
            process_mode=self._mode_value(),
            translate_mods=self.scope_mods.isChecked(),
            translate_books=self.scope_books.isChecked(),
            translate_quests=self.scope_quests.isChecked(),
        )

    def _validate_preflight(self, *, translation: bool) -> bool:
        mc_dir = settings.get("GENERAL", "mc_dir").strip()
        if not mc_dir:
            QMessageBox.warning(self, "Папка Minecraft", "Сначала выберите папку Minecraft instance/сборки.")
            return False
        if not Path(mc_dir).is_dir():
            QMessageBox.warning(self, "Папка недоступна", f"Каталог не существует:\n{mc_dir}")
            return False
        if not any((self.scope_mods.isChecked(), self.scope_books.isChecked(), self.scope_quests.isChecked())):
            QMessageBox.warning(self, "Нечего обрабатывать", "Выберите хотя бы одну область перевода.")
            return False
        if translation:
            ready, text = engine_readiness(settings, self.engine_combo.currentText())
            if not ready:
                QMessageBox.warning(self, "Движок не настроен", text)
                return False
            if self.output_inplace.isChecked():
                answer = QMessageBox.warning(
                    self,
                    "Подтвердить изменение JAR",
                    "Режим In-place изменяет файлы модов напрямую.\n\nResource Pack безопаснее. Продолжить?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return False
        return True

    def _new_job(self) -> TranslationJob:
        return TranslationJob(
            settings,
            self.cache_std,
            self.cache_ai,
            self.job_state,
            on_log=lambda message, tag="white": self.signals.log.emit(message, tag),
            on_status=lambda text, progress: self.signals.status.emit(text, progress),
            on_row=lambda icon, name, kind, trans_c, en_c, pct: self.signals.row.emit(icon, name, kind, trans_c, en_c, pct),
        )

    def _start_analysis(self) -> None:
        if not self._validate_preflight(translation=False):
            return
        self._start_worker("analysis")

    def _start_translation(self) -> None:
        if not self._validate_preflight(translation=True):
            return
        engine, provider = ENGINE_OPTIONS[self.engine_combo.currentText()]
        if engine == "ai":
            settings.set_many("AI", {
                "ai_provider": provider,
                "fallback_google": self.ai_fallback.isChecked(),
            })
        self._start_worker("translation")

    def _start_worker(self, kind: str) -> None:
        if self._worker and self._worker.is_alive():
            return
        self.job_state.start()
        self._clear_log()
        self._job = self._new_job()
        options = self._translation_options()
        self._lock_ui(True)
        self.footer_status.setText("●  Выполняется задача")
        self.task_title.setText("Анализ сборки" if kind == "analysis" else "Подготовка перевода")
        self.task_status.setText("Запуск…")

        def target() -> None:
            try:
                if kind == "analysis":
                    self._job.run_analysis(options)
                else:
                    self._job.run_translation(options)
            except Exception:
                error = traceback.format_exc()
                self.signals.worker_failed.emit(kind, error)
            finally:
                self.job_state.finish()
                self.signals.worker_finished.emit(kind)

        self._worker = threading.Thread(target=target, daemon=True)
        self._worker.start()

    def _worker_failed(self, kind: str, error: str) -> None:
        name = "анализа" if kind == "analysis" else "перевода"
        self._append_log(f"Ошибка {name}:\n{error}", "red")
        self._set_status(f"Ошибка {name}", None)

    def _worker_finished(self, _kind: str) -> None:
        self._job = None
        self._worker = None
        if not self._closing:
            self._lock_ui(False)
            self.footer_status.setText("●  Готов к работе")
            self._refresh_system_readiness()
        if self._closing:
            self._allow_close = True
            QTimer.singleShot(0, self.close)

    def _toggle_pause(self) -> None:
        paused = self.job_state.toggle_pause()
        if paused:
            self.pause_button.setText("▶  Продолжить")
            self._append_log("Пауза", "yellow")
        else:
            self.pause_button.setText("⏸  Пауза")
            self._append_log("Продолжение", "green")

    def _stop(self) -> None:
        if self._job is not None:
            self._job.stop()
        else:
            self.job_state.stop()
        self.pause_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self._set_status("Остановка…", None)

    def _lock_ui(self, locked: bool) -> None:
        for widget in (
            self.settings_button,
            self.prompts_button,
            self.migration_button,
            self.folder_button,
            self.version_combo,
            self.language_combo,
            self.engine_combo,
            self.scope_mods,
            self.scope_books,
            self.scope_quests,
            self.analyze_button,
            self.start_button,
            self.output_rp,
            self.output_inplace,
        ):
            widget.setEnabled(not locked)
        for button in self.mode_buttons.values():
            button.setEnabled(not locked)
        self.pause_button.setEnabled(locked)
        self.stop_button.setEnabled(locked)
        self.pack_name.setEnabled((not locked) and self.output_rp.isChecked())
        self.google_mode_combo.setEnabled(not locked)
        self.ai_mode_combo.setEnabled(not locked)
        self.ai_batch_spin.setEnabled(not locked)
        self.ai_fallback.setEnabled(not locked)
        if not locked:
            self.pause_button.setText("⏸  Пауза")

    def _set_status(self, text: str, progress) -> None:
        self.task_status.setText(text)
        if progress is not None:
            value = max(0.0, min(1.0, float(progress)))
            self.segmented_progress.setValue(value)
            self.task_percent.setText(f"{value * 100:.1f}%")
        self._refresh_runtime_dashboard()

    def _refresh_runtime_dashboard(self) -> None:
        snapshot = self.job_state.snapshot()
        stats = stats_from_snapshot(snapshot, eta_text=self.job_state.eta_text())

        total_text = f"{stats.processed:,} / {stats.total:,}".replace(",", " ") if stats.total else "—"
        self.kpi_processed.value.setText(total_text)
        self.kpi_processed.meta.setText(f"{stats.percent:.1f}%")
        self.kpi_processed.progress.setValue(int(stats.percent * 10))

        self.kpi_success.value.setText(f"{stats.successful:,}".replace(",", " "))
        self.kpi_success.meta.setText(f"{stats.success_percent:.1f}% от обработанных" if stats.processed else "—")
        self.kpi_success.progress.setValue(int(stats.success_percent * 10))

        self.kpi_errors.value.setText(str(stats.failed))
        self.kpi_errors.meta.setText(f"{stats.error_percent:.1f}% от обработанных" if stats.processed else "—")
        self.kpi_errors.progress.setValue(int(stats.error_percent * 10))

        self.kpi_eta.value.setText(stats.eta_text if snapshot.is_running else ("готово" if stats.total and stats.remaining_lines == 0 else "—"))
        self.kpi_eta.meta.setText(f"≈ {stats.remaining_lines:,} строк".replace(",", " ") if stats.total else "—")
        self.kpi_eta.progress.setValue(int(stats.percent * 10) if stats.total else 0)

        if snapshot.total_files > 0:
            self.task_title.setText(f"{snapshot.current_file_type} · {snapshot.current_file_done}/{snapshot.total_files}")

        if stats.total:
            self.segmented_progress.setValue(stats.percent / 100.0)
            self.task_percent.setText(f"{stats.percent:.1f}%")
            self.task_lines.value.setText(f"{stats.processed:,} / {stats.total:,}".replace(",", " "))
        else:
            self.task_lines.value.setText("—")
        self.task_speed.value.setText(f"{stats.lines_per_minute:.0f} строк/мин" if stats.lines_per_minute else "—")
        self.task_elapsed.value.setText(format_duration(stats.elapsed_seconds) if stats.elapsed_seconds else "—")
        self.task_remaining.value.setText(stats.eta_text if snapshot.is_running else "—")

    @staticmethod
    def _log_level(tag: str) -> str:
        if tag == "red":
            return "error"
        if tag in {"yellow", "gold", "orange"}:
            return "warning"
        if tag in {"green", "lime"}:
            return "success"
        return "info"

    def _append_log(self, message: str, tag: str = "white") -> None:
        self._log_entries.append((tag, message))
        try:
            with open("mineai_log.txt", "a", encoding="utf-8") as file:
                file.write(message + "\n")
        except Exception:
            pass
        selected = self.log_filter.currentData() if hasattr(self, "log_filter") else "all"
        if selected == "all" or selected == self._log_level(tag):
            self._append_log_html(tag, message)

    def _append_analysis_row(self, icon: str, name: str, kind: str, trans_c: int, en_c: int, pct: int) -> None:
        color = "green" if pct >= 90 else ("yellow" if pct >= 50 else "red")
        self._append_log(f"{icon} {name[:38]}  [{kind}]  {trans_c}/{en_c}  {pct}%", color)

    def _append_log_html(self, tag: str, message: str) -> None:
        color = LOG_COLORS.get(tag, LOG_COLORS["white"])
        text = escape(message).replace("\n", "<br>")
        self.log_view.append(f'<div style="margin:2px 0 5px 0; color:{color}; line-height:1.35;">{text}</div>')

    def _render_log(self) -> None:
        self.log_view.clear()
        selected = self.log_filter.currentData()
        for tag, message in self._log_entries:
            if selected == "all" or selected == self._log_level(tag):
                self._append_log_html(tag, message)

    def _clear_log(self) -> None:
        self._log_entries.clear()
        self.log_view.clear()

    def _save_log(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить журнал", "mineai_log_export.txt", "Text files (*.txt);;All files (*)")
        if not path:
            return
        selected = self.log_filter.currentData()
        lines = [message for tag, message in self._log_entries if selected == "all" or selected == self._log_level(tag)]
        try:
            Path(path).write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить журнал:\n{exc}")

    def closeEvent(self, event) -> None:
        if self._allow_close or not (self._worker and self._worker.is_alive()):
            event.accept()
            return
        event.ignore()
        if self._closing:
            return
        self._closing = True
        if self._job is not None:
            self._job.stop()
        else:
            self.job_state.stop()
        self._lock_ui(True)
        self.pause_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self._set_status("Завершение работы…", None)
        self.footer_status.setText("●  Завершение задачи")
        QTimer.singleShot(60, self._poll_close)

    def _poll_close(self) -> None:
        if self._worker and self._worker.is_alive():
            QTimer.singleShot(60, self._poll_close)
            return
        self._allow_close = True
        self.close()


def run() -> int:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("MineAI Translator")
    app.setStyleSheet(APP_QSS)
    window = TranslatorQtWindow()
    window.show()
    return app.exec()
