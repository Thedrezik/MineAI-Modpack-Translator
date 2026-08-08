"""Qt versions of Settings, Prompt Editor and Migration dialogs.

They use the same configuration, prompt and migration APIs as the current beta.
No translation engine or processor behavior is reimplemented here.
"""

from __future__ import annotations

import os
import threading
import traceback

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from mineai.constants import DEFAULT_OPENROUTER_MODEL, LANGUAGES
from mineai.engines.llm_common import get_default_prompts, load_prompts, save_prompts
from mineai.processors.migration import run_migration
from mineai.gui_qt.bridge import MigrationSignals


class SettingsDialog(QDialog):
    def __init__(self, config, on_saved, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.on_saved = on_saved
        self.setWindowTitle("Настройки MineAI")
        self.resize(760, 720)
        self.setMinimumSize(650, 600)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        tabs = QTabWidget()
        root.addWidget(tabs, 1)

        ai_tab, ai_layout = self._scroll_tab()
        or_tab, or_layout = self._scroll_tab()
        general_tab, general_layout = self._scroll_tab()
        tabs.addTab(ai_tab, "Локальный ИИ")
        tabs.addTab(or_tab, "OpenRouter")
        tabs.addTab(general_tab, "Общие и API")

        self.ai_exe = self._file_row(ai_layout, "Исполняемый файл KoboldCPP (.exe)", config.get("AI", "exe_path"), "Executables (*.exe)")
        self.ai_model = self._file_row(ai_layout, "Модель (.gguf)", config.get("AI", "model_path"), "GGUF Models (*.gguf)")
        self.gpu_layers = self._slider_row(ai_layout, "Слои GPU", config.getint("AI", "gpu_layers", 99), 0, 99)
        ai_layout.addStretch(1)

        note = QLabel("Ключ можно создать на openrouter.ai/keys. API URL также подходит для OpenAI-compatible шлюзов.")
        note.setObjectName("MutedLabel")
        note.setWordWrap(True)
        or_layout.addWidget(note)
        self.or_url = self._line_row(or_layout, "API URL", config.get("OPENROUTER", "api_url"))
        self.or_key = self._line_row(or_layout, "API ключ OpenRouter", config.get("OPENROUTER", "api_key"), secret=True)
        self.or_model = self._line_row(or_layout, "ID модели", config.get("OPENROUTER", "model") or DEFAULT_OPENROUTER_MODEL)
        self.or_site = self._line_row(or_layout, "Site URL (необязательно)", config.get("OPENROUTER", "site_url"))
        self.or_app = self._line_row(or_layout, "Название приложения (X-Title)", config.get("OPENROUTER", "app_name"))
        or_layout.addStretch(1)

        self.smart_glue = QCheckBox("Умный склейщик предложений")
        self.smart_glue.setChecked(config.getboolean("GENERAL", "smart_glue"))
        general_layout.addWidget(self.smart_glue)
        self.ai_retries = self._spin_row(general_layout, "Повторы ИИ при ошибке", config.getint("AI", "ai_retries", 3), 0, 5)
        self.google_workers = self._spin_row(general_layout, "Потоки Google Translate", config.getint("GENERAL", "google_workers", 5), 1, 10)
        self.deepl_key = self._line_row(general_layout, "API ключ DeepL", config.get("API", "deepl_key"), secret=True)
        general_layout.addStretch(1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = QPushButton("Отмена")
        save = QPushButton("Сохранить настройки")
        save.setObjectName("PrimaryButton")
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self._save)
        actions.addWidget(cancel)
        actions.addWidget(save)
        root.addLayout(actions)

    @staticmethod
    def _scroll_tab():
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        root_layout.addWidget(scroll)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        scroll.setWidget(content)
        return root, layout

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("FieldLabel")
        return label

    def _line_row(self, layout, label: str, value: str, *, secret: bool = False) -> QLineEdit:
        layout.addWidget(self._field_label(label))
        edit = QLineEdit(value)
        if secret:
            edit.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(edit)
        return edit

    def _file_row(self, layout, label: str, value: str, file_filter: str) -> QLineEdit:
        layout.addWidget(self._field_label(label))
        row = QHBoxLayout()
        edit = QLineEdit(value)
        browse = QPushButton("Обзор")
        browse.setFixedWidth(88)
        browse.clicked.connect(lambda: self._browse_file(edit, file_filter))
        row.addWidget(edit, 1)
        row.addWidget(browse)
        layout.addLayout(row)
        return edit

    def _slider_row(self, layout, label: str, value: int, minimum: int, maximum: int) -> QSlider:
        value_label = self._field_label(f"{label}: {value}")
        layout.addWidget(value_label)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setValue(value)
        slider.valueChanged.connect(lambda v: value_label.setText(f"{label}: {v}"))
        layout.addWidget(slider)
        return slider

    def _spin_row(self, layout, label: str, value: int, minimum: int, maximum: int) -> QSpinBox:
        layout.addWidget(self._field_label(label))
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        layout.addWidget(spin)
        return spin

    def _browse_file(self, edit: QLineEdit, file_filter: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Выберите файл", edit.text(), file_filter)
        if path:
            edit.setText(path)

    def _save(self) -> None:
        self.config.set_many("AI", {
            "exe_path": self.ai_exe.text(),
            "model_path": self.ai_model.text(),
            "gpu_layers": self.gpu_layers.value(),
            "ai_retries": self.ai_retries.value(),
        })
        self.config.set_many("OPENROUTER", {
            "api_key": self.or_key.text(),
            "api_url": self.or_url.text().strip(),
            "model": self.or_model.text().strip(),
            "site_url": self.or_site.text().strip(),
            "app_name": self.or_app.text().strip(),
        })
        self.config.set_many("GENERAL", {
            "smart_glue": self.smart_glue.isChecked(),
            "google_workers": self.google_workers.value(),
        })
        self.config.set_many("API", {"deepl_key": self.deepl_key.text()})
        self.on_saved()
        self.accept()


class PromptEditorDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Редактор промптов ИИ")
        self.resize(940, 680)
        self.setMinimumSize(720, 520)
        self._dirty = False
        self.prompts = load_prompts()
        defaults = get_default_prompts()

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)
        tabs = QTabWidget()
        root.addWidget(tabs, 1)

        self.editors: dict[str, QPlainTextEdit] = {}
        for key, title in (
            ("mods", "Интерфейс (Моды)"),
            ("books", "Книги / Справочники"),
            ("quests", "Квесты"),
        ):
            editor = self._prompt_tab(tabs, title, "Переменные: {lang_name} (язык), {context} (название мода/файла)")
            editor.setPlainText(self.prompts.get(key, defaults[key]))
            self.editors[key] = editor

        tech = self._prompt_tab(
            tabs,
            "Тех. правила (ОПАСНО)",
            "Изменение технических правил может сломать JSON и маркеры. {markers} — точный список [#N#] текущего запроса.",
            danger=True,
        )
        tech.setPlainText(self.prompts.get("technical", defaults["technical"]))
        self.editors["technical"] = tech

        for editor in self.editors.values():
            editor.textChanged.connect(self._mark_dirty)

        footer = QHBoxLayout()
        self.dirty_label = QLabel("Все изменения сохранены")
        self.dirty_label.setObjectName("MutedLabel")
        footer.addWidget(self.dirty_label, 1)
        reset = QPushButton("Сбросить")
        reset.setObjectName("DangerButton")
        save = QPushButton("Сохранить")
        save.setObjectName("PrimaryButton")
        reset.clicked.connect(self._reset)
        save.clicked.connect(lambda: self._save(close=True))
        footer.addWidget(reset)
        footer.addWidget(save)
        root.addLayout(footer)
        self._dirty = False

    @staticmethod
    def _prompt_tab(tabs: QTabWidget, title: str, note: str, *, danger: bool = False) -> QPlainTextEdit:
        page = QWidget()
        layout = QVBoxLayout(page)
        note_label = QLabel(note)
        note_label.setWordWrap(True)
        note_label.setObjectName("DangerText" if danger else "MutedLabel")
        editor = QPlainTextEdit()
        layout.addWidget(note_label)
        layout.addWidget(editor, 1)
        tabs.addTab(page, title)
        return editor

    def _mark_dirty(self) -> None:
        self._dirty = True
        self.dirty_label.setText("● Есть несохранённые изменения")
        self.dirty_label.setObjectName("WarningText")
        self.dirty_label.style().unpolish(self.dirty_label)
        self.dirty_label.style().polish(self.dirty_label)

    def _reset(self) -> None:
        answer = QMessageBox.question(
            self,
            "Сбросить промпты?",
            "Все четыре промпта будут заменены значениями по умолчанию. Продолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        defaults = get_default_prompts()
        for key, editor in self.editors.items():
            editor.setPlainText(defaults[key])
        self._mark_dirty()

    def _save(self, *, close: bool) -> None:
        for key, editor in self.editors.items():
            self.prompts[key] = editor.toPlainText().strip()
        save_prompts(self.prompts)
        self._dirty = False
        self.dirty_label.setText("Все изменения сохранены")
        self.dirty_label.setObjectName("MutedLabel")
        if close:
            self.accept()

    def closeEvent(self, event) -> None:
        if not self._dirty:
            event.accept()
            return
        box = QMessageBox(self)
        box.setWindowTitle("Сохранить изменения?")
        box.setText("В редакторе промптов есть несохранённые изменения.")
        box.setStandardButtons(
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel
        )
        answer = box.exec()
        if answer == QMessageBox.StandardButton.Cancel:
            event.ignore()
        elif answer == QMessageBox.StandardButton.Save:
            self._save(close=False)
            event.accept()
        else:
            event.accept()


class MigrationDialog(QDialog):
    def __init__(self, mc_dir: str, lang_label: str, cache_std, cache_ai, log_callback, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Миграция перевода")
        self.resize(650, 500)
        self.setMinimumSize(560, 430)
        self.mc_dir = mc_dir
        self.lang_api_code = LANGUAGES[lang_label]["api"]
        self.cache_std = cache_std
        self.cache_ai = cache_ai
        self.log_callback = log_callback
        self.signals = MigrationSignals()
        self.signals.finished.connect(self._show_result)
        self._worker: threading.Thread | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(11)

        title = QLabel("Миграция готового перевода")
        title.setObjectName("AppTitle")
        root.addWidget(title)
        note = QLabel("Импортирует строки из существующего Resource Pack в отдельный imported cache. Исходный ZIP не изменяется.")
        note.setObjectName("MutedLabel")
        note.setWordWrap(True)
        root.addWidget(note)

        label = QLabel("RESOURCE PACK (.ZIP)")
        label.setObjectName("FieldLabel")
        root.addWidget(label)
        zip_row = QHBoxLayout()
        self.zip_edit = QLineEdit()
        browse = QPushButton("Обзор")
        browse.clicked.connect(self._browse)
        zip_row.addWidget(self.zip_edit, 1)
        zip_row.addWidget(browse)
        root.addLayout(zip_row)

        cache_label = QLabel("КЭШ НАЗНАЧЕНИЯ")
        cache_label.setObjectName("FieldLabel")
        root.addWidget(cache_label)
        self.ai_radio = QRadioButton("Нейросети · imported_caches/ai")
        self.std_radio = QRadioButton("Google / DeepL · imported_caches/std")
        self.ai_radio.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.ai_radio)
        group.addButton(self.std_radio)
        root.addWidget(self.ai_radio)
        root.addWidget(self.std_radio)

        self.result = QFrame()
        self.result.setObjectName("InnerCard")
        result_layout = QVBoxLayout(self.result)
        self.result_title = QLabel("")
        self.result_title.setObjectName("StrongLabel")
        self.result_details = QLabel("")
        self.result_details.setObjectName("MutedLabel")
        self.result_details.setWordWrap(True)
        result_layout.addWidget(self.result_title)
        result_layout.addWidget(self.result_details)
        self.result.hide()
        root.addWidget(self.result)
        root.addStretch(1)

        self.run_button = QPushButton("Начать миграцию")
        self.run_button.setObjectName("PrimaryButton")
        self.run_button.clicked.connect(self._run)
        root.addWidget(self.run_button)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Resource Pack", "", "ZIP Archives (*.zip)")
        if path:
            self.zip_edit.setText(path)

    def _run(self) -> None:
        path = self.zip_edit.text().strip()
        if not path or not os.path.exists(path):
            QMessageBox.critical(self, "Ошибка", "Выберите валидный ZIP-архив.")
            return
        if self._worker and self._worker.is_alive():
            return
        cache_type = "ai" if self.ai_radio.isChecked() else "std"
        self.run_button.setEnabled(False)
        self.run_button.setText("Выполнение…")
        self.result.hide()

        def task() -> None:
            count = 0
            error = None
            try:
                count = run_migration(path, self.mc_dir, cache_type, self.lang_api_code, self.log_callback)
                if count > 0:
                    if cache_type == "ai":
                        self.cache_ai.load_imported_caches()
                    else:
                        self.cache_std.load_imported_caches()
            except Exception:
                error = traceback.format_exc()
            self.signals.finished.emit(count, error, cache_type)

        self._worker = threading.Thread(target=task, daemon=False)
        self._worker.start()

    def _show_result(self, count: int, error, cache_type: str) -> None:
        self.result.show()
        if error:
            self.result_title.setText("Миграция завершилась с ошибкой")
            self.result_title.setObjectName("DangerText")
            self.result_details.setText(str(error))
        else:
            destination = "Нейросети" if cache_type == "ai" else "Google / DeepL"
            self.result_title.setText("✓ Миграция завершена")
            self.result_title.setObjectName("ReadyText")
            self.result_details.setText(
                f"Импортировано: {count} уникальных строк\n"
                f"Кэш назначения: {destination}\n"
                "Детализация пропусков/конфликтов недоступна в текущем migration API."
            )
        self.result_title.style().unpolish(self.result_title)
        self.result_title.style().polish(self.result_title)
        self.run_button.setEnabled(True)
        self.run_button.setText("Запустить снова")

    def closeEvent(self, event) -> None:
        if self._worker and self._worker.is_alive():
            QMessageBox.information(
                self,
                "Миграция выполняется",
                "Дождитесь завершения миграции перед закрытием окна.",
            )
            event.ignore()
            return
        event.accept()
