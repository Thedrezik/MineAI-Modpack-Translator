"""Premium PyQt6 dashboard for MineAI Translator.

This module is an alternate presentation layer for the existing beta runtime.
TranslationJob, JobState, engines, processors, caches and PackWriter remain the
single source of truth for translation behavior.
"""

from __future__ import annotations

from pathlib import Path
import sys
import threading
import traceback

from PyQt6.QtCore import QTimer, Qt, QUrl
from PyQt6.QtGui import QColor, QDesktopServices, QIcon, QPixmap, QTextCharFormat, QTextCursor
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
    QToolButton,
    QScrollArea,
    QSpinBox,
    QPlainTextEdit,
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
from mineai.gui_qt.i18n import t, translator
from mineai.gui_qt.i18n_runtime import tr as rt
from mineai.gui_qt.log_model import LogEntry, LogSegment, entry_from_message, matches_entry
from mineai.gui_qt.theme import theme_qss
from mineai.gui_qt.view_model import ENGINE_OPTIONS, engine_readiness, format_duration, stats_from_snapshot
from mineai.gui_qt.widgets import Card, HelpMarker, LabeledValue, SegmentedProgressBar, StatCard, StatusPill


def _resolve_icon_path() -> str | None:
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        candidates.extend((base / "icon.png", base / "icon.ico"))
        candidates.extend((Path(sys.executable).parent / "icon.png", Path(sys.executable).parent / "icon.ico"))
    cwd = Path.cwd()
    candidates.extend((cwd / "icon.png", cwd / "icon.ico"))
    return str(next((path for path in candidates if path.exists()), "")) or None


LOG_PATH = Path("mineai_log.txt").resolve()
MAX_LOG_ENTRIES = 50_000
MAX_LOG_BLOCKS = 25_000


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
        translator.set_language(settings.get("GENERAL", "ui_language"))
        self._ui_language = translator.language
        self._theme_name = settings.get("GENERAL", "theme") or "Dark"
        self.setWindowTitle(f"{t('app.title')} — {__version__}")
        icon_path = _resolve_icon_path()
        if icon_path:
            self.setWindowIcon(QIcon(icon_path))
        self.resize(1520, 940)
        self.setMinimumSize(1180, 760)
        self.setAcceptDrops(True)
        self.setStyleSheet(theme_qss(self._theme_name))

        self.job_state = JobState()
        self.cache_std, self.cache_ai, polish_total = load_both_caches()
        self._job: TranslationJob | None = None
        self._worker: threading.Thread | None = None
        self._closing = False
        self._allow_close = False
        self._log_entries: list[LogEntry] = []
        self._log_file = None
        try:
            self._log_file = LOG_PATH.open("a", encoding="utf-8", buffering=1)
        except OSError:
            self._log_file = None

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
        self._apply_theme(self._theme_name)

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
        if icon_path:
            pixmap = QPixmap(icon_path)
            if not pixmap.isNull():
                logo.setPixmap(
                    pixmap.scaled(
                        32,
                        32,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
            else:
                logo.setStyleSheet("color: #8B6BE5; font-size: 28px; font-weight: 800;")
        else:
            logo.setStyleSheet("color: #8B6BE5; font-size: 28px; font-weight: 800;")
        logo.setFixedSize(36, 36)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(logo)

        titles = QVBoxLayout()
        titles.setSpacing(0)
        title = QLabel(t("app.title"))
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

        self.settings_button = QPushButton(t("header.settings"))
        self.settings_button.setObjectName("HeaderButton")
        self.settings_button.clicked.connect(self._open_settings)
        layout.addWidget(self.settings_button)

        self.prompts_button = QPushButton(t("header.prompts"))
        self.prompts_button.setObjectName("HeaderButton")
        self.prompts_button.clicked.connect(self._open_prompts)
        layout.addWidget(self.prompts_button)

        self.migration_button = QPushButton(t("header.migration"))
        self.migration_button.setObjectName("HeaderButton")
        self.migration_button.setToolTip(t("tooltip.migration"))
        self.migration_button.clicked.connect(self._open_migration)
        layout.addWidget(self.migration_button)

        layout.addSpacing(4)
        self.interface_language = QComboBox()
        self.interface_language.setObjectName("HeaderLanguageCombo")
        self.interface_language.addItem("RU", "ru")
        self.interface_language.addItem("EN", "en")
        language_index = self.interface_language.findData(self._ui_language)
        self.interface_language.setCurrentIndex(language_index if language_index >= 0 else 0)
        self.interface_language.setFixedWidth(72)
        self.interface_language.setToolTip(t("header.language_tooltip"))
        self.interface_language.currentIndexChanged.connect(self._change_interface_language)
        layout.addWidget(self.interface_language)

        self.theme_button = QToolButton()
        self.theme_button.setObjectName("ThemeToggle")
        self.theme_button.setFixedSize(38, 36)
        self.theme_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_button.clicked.connect(self._toggle_theme)
        layout.addWidget(self.theme_button)
        self._refresh_theme_button()
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
        card = Card(t("card.project"))
        label = QLabel(t("field.minecraft_folder"))
        label.setObjectName("FieldLabel")
        card.body.addWidget(label)

        folder_row = QHBoxLayout()
        folder_row.setSpacing(6)
        self.folder_edit = QLineEdit()
        self.folder_edit.setReadOnly(True)
        self.folder_button = QPushButton("📁")
        self.folder_button.setFixedWidth(42)
        self.folder_button.setToolTip(t("tooltip.folder"))
        self.folder_button.clicked.connect(self._select_folder)
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(self.folder_button)
        card.body.addLayout(folder_row)

        self.folder_state = QLabel(t("folder.not_selected"))
        self.folder_state.setObjectName("MutedLabel")
        card.body.addWidget(self.folder_state)

        selectors = QGridLayout()
        selectors.setHorizontalSpacing(8)
        version_label = QLabel(t("field.minecraft_version"))
        version_label.setObjectName("FieldLabel")
        language_label = QLabel(t("field.target_language"))
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
        card = Card(t("card.engine"))
        row = QHBoxLayout()
        row.setSpacing(8)
        label = QLabel(t("field.engine"))
        label.setObjectName("FieldLabel")
        self.engine_combo = QComboBox()
        self.engine_combo.addItems(["Google", "DeepL", rt("engine.local"), "OpenRouter"])
        self.engine_combo.currentTextChanged.connect(self._engine_changed)
        row.addWidget(label)
        row.addWidget(self.engine_combo, 1)
        card.body.addLayout(row)

        ready = QFrame()
        ready.setObjectName("ReadyBox")
        ready_layout = QHBoxLayout(ready)
        ready_layout.setContentsMargins(9, 6, 7, 6)
        self.engine_ready_label = QLabel(t("engine.checking"))
        self.engine_ready_label.setObjectName("ReadyText")
        configure = QPushButton(t("button.configure"))
        configure.setFixedWidth(92)
        configure.clicked.connect(self._open_settings)
        ready_layout.addWidget(self.engine_ready_label, 1)
        ready_layout.addWidget(configure)
        card.body.addWidget(ready)

        self.google_options = QWidget()
        google_layout = QHBoxLayout(self.google_options)
        google_layout.setContentsMargins(0, 0, 0, 0)
        google_layout.addWidget(QLabel(t("field.google_mode")))
        self.google_mode_combo = QComboBox()
        self.google_mode_combo.addItem(t("google.single"), "single")
        self.google_mode_combo.addItem(t("google.batch"), "batch")
        google_layout.addWidget(self.google_mode_combo, 1)
        card.body.addWidget(self.google_options)

        self.ai_options = QWidget()
        ai_grid = QGridLayout(self.ai_options)
        ai_grid.setContentsMargins(0, 0, 0, 0)
        ai_grid.setHorizontalSpacing(8)
        ai_grid.setVerticalSpacing(7)
        ai_grid.addWidget(QLabel(t("field.ai_mode")), 0, 0, 1, 2)
        self.ai_mode_combo = QComboBox()
        self.ai_mode_combo.addItem(t("ai.safe"), "safe")
        self.ai_mode_combo.addItem(t("ai.context"), "context")
        ai_grid.addWidget(self.ai_mode_combo, 0, 2)

        batch_label = QLabel(t("field.ai_batch"))
        ai_grid.addWidget(batch_label, 1, 0)
        ai_grid.addWidget(HelpMarker(t("tooltip.ai_batch")), 1, 1)
        self.ai_batch_spin = QSpinBox()
        self.ai_batch_spin.setRange(1, 40)
        self.ai_batch_spin.setValue(20)
        self.ai_batch_spin.valueChanged.connect(self._refresh_footer)
        ai_grid.addWidget(self.ai_batch_spin, 1, 2)

        fallback_host = QWidget()
        fallback_layout = QHBoxLayout(fallback_host)
        fallback_layout.setContentsMargins(0, 0, 0, 0)
        self.ai_fallback = QCheckBox(t("field.google_fallback"))
        self.ai_fallback.setChecked(settings.getboolean("AI", "fallback_google"))
        fallback_layout.addWidget(self.ai_fallback)
        fallback_layout.addWidget(HelpMarker(t("tooltip.fallback")))
        fallback_layout.addStretch(1)
        ai_grid.addWidget(fallback_host, 2, 0, 1, 3)
        card.body.addWidget(self.ai_options)
        return card

    def _build_scope_card(self) -> QWidget:
        card = Card(t("card.scope"))
        self.scope_mods = QCheckBox(t("scope.mods"))
        self.scope_books = QCheckBox(t("scope.books"))
        self.scope_quests = QCheckBox(t("scope.quests"))
        for checkbox in (self.scope_mods, self.scope_books, self.scope_quests):
            checkbox.setChecked(True)
            checkbox.stateChanged.connect(self._refresh_system_readiness)
            card.body.addWidget(checkbox)
        return card

    def _build_mode_card(self) -> QWidget:
        card = Card(t("card.mode"))
        mode_label = QLabel(t("field.processing"))
        mode_label.setObjectName("FieldLabel")
        card.body.addWidget(mode_label)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(6)
        self.mode_group = QButtonGroup(card)
        self.mode_group.setExclusive(True)
        self.mode_buttons: dict[str, QPushButton] = {}
        for value, label in (("append", "Append"), ("skip", "Skip"), ("force", "Force")):
            button = QPushButton(label)
            button.setObjectName("SegmentButton")
            button.setCheckable(True)
            if value == "append":
                button.setChecked(True)
            self.mode_group.addButton(button)
            self.mode_buttons[value] = button
            mode_row.addWidget(button, 1)
        card.body.addLayout(mode_row)

        output_label = QLabel(t("field.output"))
        output_label.setObjectName("FieldLabel")
        card.body.addWidget(output_label)
        output_row = QHBoxLayout()
        output_row.setSpacing(6)
        self.output_group = QButtonGroup(card)
        self.output_group.setExclusive(True)
        self.output_rp = QPushButton(t("output.resourcepack"))
        self.output_inplace = QPushButton(t("output.inplace"))
        for button in (self.output_rp, self.output_inplace):
            button.setObjectName("SegmentButton")
            button.setCheckable(True)
            self.output_group.addButton(button)
            output_row.addWidget(button, 1)
        output_row.addWidget(HelpMarker(t("tooltip.inplace")))
        self.output_rp.setChecked(True)
        self.output_rp.toggled.connect(self._output_changed)
        card.body.addLayout(output_row)

        self.pack_name = QLineEdit("MineAI_Pack")
        self.pack_name.setPlaceholderText(t("output.pack_placeholder"))
        card.body.addWidget(self.pack_name)
        return card

    def _build_action_card(self) -> QWidget:
        card = Card(t("card.actions"))
        action_row = QHBoxLayout()
        self.analyze_button = QPushButton(t("button.analysis"))
        self.start_button = QPushButton(t("button.start"))
        self.start_button.setObjectName("PrimaryButton")
        self.analyze_button.setToolTip(t("tooltip.analysis"))
        self.start_button.setToolTip(t("tooltip.start"))
        self.analyze_button.clicked.connect(self._start_analysis)
        self.start_button.clicked.connect(self._start_translation)
        action_row.addWidget(self.analyze_button)
        action_row.addWidget(self.start_button, 1)
        card.body.addLayout(action_row)

        run_row = QHBoxLayout()
        self.pause_button = QPushButton(t("button.pause"))
        self.pause_button.setObjectName("WarningButton")
        self.stop_button = QPushButton(t("button.stop"))
        self.stop_button.setObjectName("DangerButton")
        self.pause_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.pause_button.clicked.connect(self._toggle_pause)
        self.stop_button.clicked.connect(self._stop)
        run_row.addWidget(self.pause_button, 1)
        run_row.addWidget(self.stop_button, 1)
        card.body.addLayout(run_row)

        self.lock_hint = QLabel(t("lock.hint"))
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
        card = Card(t("card.status"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        self.kpi_processed = StatCard(t("kpi.processed"), "KpiBlue")
        self.kpi_success = StatCard(t("kpi.success"), "KpiGreen")
        self.kpi_errors = StatCard(t("kpi.errors"), "KpiAmber")
        self.kpi_eta = StatCard(t("kpi.remaining"), "KpiViolet")
        for widget, glyph, color in (
            (self.kpi_processed, "▣", "#60A5FA"),
            (self.kpi_success, "✓", "#5EEAD4"),
            (self.kpi_errors, "!", "#FBBF24"),
            (self.kpi_eta, "◷", "#A78BFA"),
        ):
            widget.icon.setText(glyph)
            widget.icon.setStyleSheet(
                f"background: transparent; border: none; color: {color}; font-size: 18px; font-weight: 800;"
            )
        for col, widget in enumerate((self.kpi_processed, self.kpi_success, self.kpi_errors, self.kpi_eta)):
            grid.addWidget(widget, 0, col)
            grid.setColumnStretch(col, 1)
        card.body.addLayout(grid)
        return card

    def _build_task_card(self) -> QWidget:
        card = Card(t("card.task"))
        title_row = QHBoxLayout()
        self.task_title = QLabel(t("task.idle"))
        self.task_title.setObjectName("StrongLabel")
        self.task_percent = QLabel("0.0%")
        self.task_percent.setObjectName("KpiValue")
        title_row.addWidget(self.task_title, 1)
        title_row.addWidget(self.task_percent)
        card.body.addLayout(title_row)

        self.task_status = QLabel(t("task.ready"))
        self.task_status.setObjectName("MutedLabel")
        self.task_status.setWordWrap(True)
        card.body.addWidget(self.task_status)

        self.segmented_progress = SegmentedProgressBar()
        self.segmented_progress.set_theme(self._theme_name)
        card.body.addWidget(self.segmented_progress)

        metrics = QHBoxLayout()
        metrics.setSpacing(18)
        self.task_lines = LabeledValue(t("task.line"))
        self.task_speed = LabeledValue(t("task.speed"))
        self.task_elapsed = LabeledValue(t("task.elapsed"))
        self.task_remaining = LabeledValue(t("task.remaining"))
        for widget in (self.task_lines, self.task_speed, self.task_elapsed, self.task_remaining):
            metrics.addWidget(widget)
        metrics.addStretch(1)
        card.body.addLayout(metrics)
        return card

    def _build_log_card(self) -> QWidget:
        card = Card(t("card.log"))
        toolbar = QGridLayout()
        toolbar.setHorizontalSpacing(8)
        toolbar.setVerticalSpacing(7)
        self.log_filter = QComboBox()
        self.log_filter.addItem(t("log.all"), "all")
        self.log_filter.addItem(t("log.translated"), "translated")
        self.log_filter.addItem(t("log.issues"), "issues")
        self.log_filter.addItem(t("log.analysis"), "analysis")
        self.log_filter.currentIndexChanged.connect(self._render_log)

        self.log_search = QLineEdit()
        self.log_search.setPlaceholderText(t("log.search"))
        self.log_search.setClearButtonEnabled(True)
        self.log_search.setMaximumWidth(300)
        self.log_search.textChanged.connect(self._render_log)

        self.log_autoscroll = QCheckBox(t("log.autoscroll"))
        self.log_autoscroll.setChecked(True)

        open_log = QPushButton(t("button.open_log"))
        clear = QPushButton(t("button.clear"))
        save = QPushButton(t("button.save"))
        open_log.clicked.connect(self._open_log_file)
        clear.clicked.connect(self._clear_log)
        save.clicked.connect(self._save_log)

        toolbar.addWidget(self.log_filter, 0, 0)
        toolbar.addWidget(self.log_search, 0, 1)
        toolbar.addWidget(self.log_autoscroll, 0, 2)
        toolbar.setColumnStretch(1, 1)

        log_actions = QHBoxLayout()
        log_actions.setSpacing(7)
        log_actions.addStretch(1)
        log_actions.addWidget(open_log)
        log_actions.addWidget(clear)
        log_actions.addWidget(save)
        toolbar.addLayout(log_actions, 1, 0, 1, 3)
        card.body.addLayout(toolbar)

        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("LogView")
        self.log_view.setReadOnly(True)
        self.log_view.setUndoRedoEnabled(False)
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.log_view.document().setMaximumBlockCount(MAX_LOG_BLOCKS)
        card.body.addWidget(self.log_view, 1)
        return card

    def _build_footer(self) -> QWidget:
        footer = QFrame()
        footer.setObjectName("Footer")
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(24, 8, 24, 8)
        self.footer_status = QLabel(t("footer.ready"))
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
        path = QFileDialog.getExistingDirectory(self, t("dialog.minecraft_folder"), settings.get("GENERAL", "mc_dir"))
        if path:
            self._set_minecraft_directory(path)

    def _refresh_folder_state(self) -> None:
        raw = settings.get("GENERAL", "mc_dir").strip()
        if not raw:
            self.folder_state.setText(t("folder.not_selected"))
            self.folder_state.setObjectName("MutedLabel")
        else:
            root = Path(raw)
            if not root.is_dir():
                self.folder_state.setText(t("folder.missing"))
                self.folder_state.setObjectName("DangerText")
            else:
                markers = []
                if (root / "mods").is_dir():
                    markers.append("mods/ ✓")
                if (root / "config").is_dir():
                    markers.append("config/ ✓")
                suffix = "   " + "   ".join(markers) if markers else ""
                self.folder_state.setText(t("folder.found") + suffix)
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
            self.system_pill.set_ready(False, t("ready.folder"))
            return
        if not any((self.scope_mods.isChecked(), self.scope_books.isChecked(), self.scope_quests.isChecked())):
            self.system_pill.set_ready(False, t("ready.scope"))
            return
        ready, status_text = engine_readiness(settings, self.engine_combo.currentText())
        if not ready:
            self.system_pill.set_ready(False, status_text)
            return
        self.system_pill.set_ready(True, t("ready.all"))

    def _refresh_footer(self, *_args) -> None:
        workers = settings.getint("GENERAL", "google_workers", 5)
        retries = settings.getint("AI", "ai_retries", 3)
        batch = self.ai_batch_spin.value() if hasattr(self, "ai_batch_spin") else 20
        self.footer_details.setText(t("footer.details", workers=workers, batch=batch, retries=retries))

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

    def _change_interface_language(self, _index: int) -> None:
        code = self.interface_language.currentData() or "ru"
        if code == self._ui_language:
            return
        if self._worker and self._worker.is_alive():
            previous = self.interface_language.findData(self._ui_language)
            if previous >= 0:
                self.interface_language.blockSignals(True)
                self.interface_language.setCurrentIndex(previous)
                self.interface_language.blockSignals(False)
            return
        settings.set("GENERAL", "ui_language", code)
        translator.set_language(code)
        self._ui_language = translator.language
        self.setWindowTitle(f"{t('app.title')} — {__version__}")
        QTimer.singleShot(0, self._rebuild_ui_for_locale)

    def _toggle_theme(self) -> None:
        new_theme = "Light" if self._theme_name.casefold() == "dark" else "Dark"
        settings.set("GENERAL", "theme", new_theme)
        self._apply_theme(new_theme)

    def _refresh_theme_button(self) -> None:
        if not hasattr(self, "theme_button"):
            return
        is_light = self._theme_name.casefold() == "light"
        self.theme_button.setText("☀" if is_light else "☾")
        self.theme_button.setToolTip(
            t("header.theme_to_dark") if is_light else t("header.theme_to_light")
        )

    def _open_prompts(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        PromptEditorDialog(self).exec()

    def _open_migration(self, initial_zip: str | None = None) -> None:
        if self._worker and self._worker.is_alive():
            return
        mc_dir = settings.get("GENERAL", "mc_dir").strip()
        if not mc_dir or not Path(mc_dir).is_dir() or not (Path(mc_dir) / "mods").is_dir():
            QMessageBox.warning(self, t("dialog.migration"), t("dialog.migration_need_mods"))
            return
        dialog = MigrationDialog(
            mc_dir,
            self.language_combo.currentText(),
            self.cache_std,
            self.cache_ai,
            lambda msg, tag="white": self.signals.log.emit(msg, tag),
            self,
            initial_zip=initial_zip,
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
            QMessageBox.warning(self, t("dialog.minecraft_folder"), t("dialog.folder_first"))
            return False
        if not Path(mc_dir).is_dir():
            QMessageBox.warning(self, t("dialog.folder_missing"), t("dialog.folder_missing_text", path=mc_dir))
            return False
        if not any((self.scope_mods.isChecked(), self.scope_books.isChecked(), self.scope_quests.isChecked())):
            QMessageBox.warning(self, t("dialog.nothing"), t("dialog.nothing_text"))
            return False
        if translation:
            ready, status_text = engine_readiness(settings, self.engine_combo.currentText())
            if not ready:
                QMessageBox.warning(self, t("dialog.engine"), status_text)
                return False
            if self.output_inplace.isChecked():
                answer = QMessageBox.warning(
                    self,
                    t("dialog.inplace_title"),
                    t("dialog.inplace_text"),
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
        self.footer_status.setText(t("footer.running"))
        self.task_title.setText(t("status.analysis") if kind == "analysis" else t("status.translation_prepare"))
        self.task_status.setText(t("status.starting"))

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
        label = t("status.error_analysis") if kind == "analysis" else t("status.error_translation")
        self._append_log(f"{label}:\n{error}", "red")
        self._set_status(label, None)

    def _worker_finished(self, _kind: str) -> None:
        self._job = None
        self._worker = None
        if not self._closing:
            self._lock_ui(False)
            self.footer_status.setText(t("footer.ready"))
            self._refresh_system_readiness()
        if self._closing:
            self._allow_close = True
            QTimer.singleShot(0, self.close)

    def _toggle_pause(self) -> None:
        paused = self.job_state.toggle_pause()
        if paused:
            self.pause_button.setText(t("button.resume"))
            self._append_log(t("status.pause"), "yellow")
        else:
            self.pause_button.setText(t("button.pause"))
            self._append_log(t("status.resume"), "green")

    def _stop(self) -> None:
        if self._job is not None:
            self._job.stop()
        else:
            self.job_state.stop()
        self.pause_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self._set_status(t("status.stopping"), None)

    def _lock_ui(self, locked: bool) -> None:
        for widget in (
            self.settings_button,
            self.prompts_button,
            self.migration_button,
            self.interface_language,
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
            self.pause_button.setText(t("button.pause"))

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
        self.kpi_success.meta.setText(rt("stats.processed_share", percent=stats.success_percent) if stats.processed else "—")
        self.kpi_success.progress.setValue(int(stats.success_percent * 10))

        self.kpi_errors.value.setText(str(stats.failed))
        self.kpi_errors.meta.setText(rt("stats.processed_share", percent=stats.error_percent) if stats.processed else "—")
        self.kpi_errors.progress.setValue(int(stats.error_percent * 10))

        self.kpi_eta.value.setText(stats.eta_text if snapshot.is_running else (rt("stats.done") if stats.total and stats.remaining_lines == 0 else "—"))
        remaining_text = f"{stats.remaining_lines:,}".replace(",", " ")
        self.kpi_eta.meta.setText(rt("stats.remaining_lines", count=remaining_text) if stats.total else "—")
        self.kpi_eta.progress.setValue(int(stats.percent * 10) if stats.total else 0)

        if snapshot.total_files > 0:
            self.task_title.setText(f"{snapshot.current_file_type} · {snapshot.current_file_done}/{snapshot.total_files}")

        if stats.total:
            self.segmented_progress.setValue(stats.percent / 100.0)
            self.task_percent.setText(f"{stats.percent:.1f}%")
            self.task_lines.value.setText(f"{stats.processed:,} / {stats.total:,}".replace(",", " "))
        else:
            self.task_lines.value.setText("—")
        self.task_speed.value.setText(rt("stats.rate", rate=stats.lines_per_minute) if stats.lines_per_minute else "—")
        self.task_elapsed.value.setText(format_duration(stats.elapsed_seconds) if stats.elapsed_seconds else "—")
        self.task_remaining.value.setText(stats.eta_text if snapshot.is_running else "—")

    def _append_log(self, message: str, tag: str = "white") -> None:
        color = LOG_COLORS.get(tag, LOG_COLORS["white"])
        self._push_log_entry(entry_from_message(tag, message, color), persist=True)

    def _append_analysis_row(self, icon: str, name: str, kind: str, trans_c: int, en_c: int, pct: int) -> None:
        pct_color = LOG_COLORS["green"] if pct >= 90 else (LOG_COLORS["yellow"] if pct >= 50 else LOG_COLORS["red"])
        visible_name = name[:38]
        plain = f"{icon} {visible_name}  [{kind}]  {trans_c}/{en_c}  {pct}%"
        entry = LogEntry(
            plain_text=plain,
            level="success" if pct >= 90 else ("warning" if pct >= 50 else "error"),
            category="analysis",
            segments=(
                LogSegment(f"{icon} {visible_name}", LOG_COLORS["cyan"]),
                LogSegment("  [", LOG_COLORS["white"]),
                LogSegment(kind, LOG_COLORS["magenta"]),
                LogSegment("]  ", LOG_COLORS["white"]),
                LogSegment(f"{trans_c}/{en_c}", LOG_COLORS["white"]),
                LogSegment("  ", LOG_COLORS["white"]),
                LogSegment(f"{pct}%", pct_color),
            ),
        )
        self._push_log_entry(entry, persist=True)

    def _push_log_entry(self, entry: LogEntry, *, persist: bool) -> None:
        self._log_entries.append(entry)
        if len(self._log_entries) > MAX_LOG_ENTRIES:
            del self._log_entries[: len(self._log_entries) - MAX_LOG_ENTRIES]
        if persist and self._log_file is not None:
            try:
                self._log_file.write(entry.plain_text + "\n")
            except OSError:
                pass
        if hasattr(self, "log_filter") and self._log_entry_visible(entry):
            self._append_entry_to_view(entry)

    def _log_entry_visible(self, entry: LogEntry) -> bool:
        filter_key = self.log_filter.currentData() if hasattr(self, "log_filter") else "all"
        query = self.log_search.text() if hasattr(self, "log_search") else ""
        return matches_entry(entry, filter_key or "all", query)

    def _append_entry_to_view(self, entry: LogEntry, *, allow_scroll: bool = True) -> None:
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not self.log_view.document().isEmpty():
            cursor.insertBlock()
        for segment in entry.segments:
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(segment.color))
            fmt.setFontFamily("Cascadia Mono")
            cursor.insertText(segment.text, fmt)
        self.log_view.setTextCursor(cursor)
        if allow_scroll and self.log_autoscroll.isChecked():
            bar = self.log_view.verticalScrollBar()
            bar.setValue(bar.maximum())

    def _render_log(self, *_args) -> None:
        if not hasattr(self, "log_view"):
            return
        bar = self.log_view.verticalScrollBar()
        previous_scroll = bar.value()
        self.log_view.setUpdatesEnabled(False)
        try:
            self.log_view.clear()
            for entry in self._log_entries:
                if self._log_entry_visible(entry):
                    self._append_entry_to_view(entry, allow_scroll=False)
        finally:
            self.log_view.setUpdatesEnabled(True)
        if self.log_autoscroll.isChecked():
            bar.setValue(bar.maximum())
        else:
            bar.setValue(min(previous_scroll, bar.maximum()))

    def _clear_log(self) -> None:
        self._log_entries.clear()
        self.log_view.clear()

    def _open_log_file(self) -> None:
        if self._log_file is not None:
            try:
                self._log_file.flush()
            except OSError:
                pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(LOG_PATH)))

    def _save_log(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, t("button.save"), "mineai_log_export.txt", "Text files (*.txt);;All files (*)")
        if not path:
            return
        lines = [entry.plain_text for entry in self._log_entries if self._log_entry_visible(entry)]
        try:
            Path(path).write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        except Exception as exc:
            QMessageBox.critical(self, t("error.title"), str(exc))

    def _set_minecraft_directory(self, path: str) -> None:
        root = Path(path)
        if not root.is_dir():
            return
        settings.set("GENERAL", "mc_dir", str(root))
        self.folder_edit.setText(str(root))
        self._refresh_folder_state()
        self._refresh_system_readiness()

    def _apply_theme(self, theme: str) -> None:
        self._theme_name = "Light" if str(theme).casefold() == "light" else "Dark"
        stylesheet = theme_qss(self._theme_name)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(stylesheet)
        self.setStyleSheet(stylesheet)
        if hasattr(self, "segmented_progress"):
            self.segmented_progress.set_theme(self._theme_name)
        self._refresh_theme_button()

    def _capture_ui_state(self) -> dict[str, object]:
        return {
            "version": self.version_combo.currentText(),
            "target_language": self.language_combo.currentText(),
            "engine_spec": ENGINE_OPTIONS.get(self.engine_combo.currentText(), ("google", "local")),
            "google_mode": self.google_mode_combo.currentData(),
            "ai_mode": self.ai_mode_combo.currentData(),
            "ai_batch": self.ai_batch_spin.value(),
            "fallback": self.ai_fallback.isChecked(),
            "scope": (self.scope_mods.isChecked(), self.scope_books.isChecked(), self.scope_quests.isChecked()),
            "mode": self._mode_value(),
            "resourcepack": self.output_rp.isChecked(),
            "pack_name": self.pack_name.text(),
        }

    def _rebuild_ui_for_locale(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self.setWindowTitle(f"{t('app.title')} — {__version__}")
        state = self._capture_ui_state()
        old = self.takeCentralWidget()
        if old is not None:
            old.deleteLater()
        self._build_ui()
        self.folder_edit.setText(settings.get("GENERAL", "mc_dir"))
        self.version_combo.setCurrentText(str(state["version"]))
        self.language_combo.setCurrentText(str(state["target_language"]))
        engine_spec = tuple(state["engine_spec"])
        for index in range(self.engine_combo.count()):
            label = self.engine_combo.itemText(index)
            if ENGINE_OPTIONS.get(label) == engine_spec:
                self.engine_combo.setCurrentIndex(index)
                break
        google_index = self.google_mode_combo.findData(state["google_mode"])
        if google_index >= 0:
            self.google_mode_combo.setCurrentIndex(google_index)
        ai_index = self.ai_mode_combo.findData(state["ai_mode"])
        if ai_index >= 0:
            self.ai_mode_combo.setCurrentIndex(ai_index)
        self.ai_batch_spin.setValue(int(state["ai_batch"]))
        self.ai_fallback.setChecked(bool(state["fallback"]))
        for checkbox, checked in zip((self.scope_mods, self.scope_books, self.scope_quests), state["scope"]):
            checkbox.setChecked(bool(checked))
        self.mode_buttons[str(state["mode"])].setChecked(True)
        self.output_rp.setChecked(bool(state["resourcepack"]))
        self.output_inplace.setChecked(not bool(state["resourcepack"]))
        self.pack_name.setText(str(state["pack_name"]))
        self._refresh_folder_state()
        self._refresh_engine_state()
        self._refresh_system_readiness()
        self._refresh_footer()
        self._apply_theme(self._theme_name)
        self._render_log()

    def dragEnterEvent(self, event) -> None:
        mime = event.mimeData()
        if not mime.hasUrls():
            return
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.is_dir() or (path.is_file() and path.suffix.casefold() == ".zip"):
                event.acceptProposedAction()
                return

    def dropEvent(self, event) -> None:
        local_paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        directory = next((path for path in local_paths if path.is_dir()), None)
        if directory is not None:
            self._set_minecraft_directory(str(directory))

        zip_path = next((path for path in local_paths if path.is_file() and path.suffix.casefold() == ".zip"), None)
        if zip_path is not None:
            answer = QMessageBox.question(
                self,
                t("dialog.drop_zip_title"),
                t("dialog.drop_zip_text", path=str(zip_path)),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._open_migration(str(zip_path))
        event.acceptProposedAction()

    def _close_log_sink(self) -> None:
        if self._log_file is None:
            return
        try:
            self._log_file.flush()
            self._log_file.close()
        except OSError:
            pass
        self._log_file = None

    def closeEvent(self, event) -> None:
        if self._allow_close or not (self._worker and self._worker.is_alive()):
            self._close_log_sink()
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
        self._set_status(t("status.closing"), None)
        self.footer_status.setText(t("footer.closing"))
        QTimer.singleShot(60, self._poll_close)

    def _poll_close(self) -> None:
        if self._worker and self._worker.is_alive():
            QTimer.singleShot(60, self._poll_close)
            return
        self._allow_close = True
        self.close()


def run() -> int:
    translator.set_language(settings.get("GENERAL", "ui_language"))
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("MineAI Translator")
    app.setStyleSheet(theme_qss(settings.get("GENERAL", "theme")))
    window = TranslatorQtWindow()
    window.show()
    return app.exec()
