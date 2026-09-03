import ctypes
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
import traceback
import webbrowser
from tkinter import filedialog, messagebox

import customtkinter as ctk

from mineai import __version__
from mineai.cache import load_both_caches
from mineai.config import settings
from mineai.constants import LANGUAGES, MC_VERSIONS
from mineai.gui.migration import MigrationWindow
from mineai.gui.settings import SettingsWindow
from mineai.gui.style import UI, ToolTip
from mineai.runtime.job import TranslationJob, TranslationOptions
from mineai.runtime.state import JobState


def _resolve_icon_path() -> str | None:
    """Find icon.ico in PyInstaller resources, next to the EXE, or in cwd."""
    candidates: list[str] = []
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        candidates.append(os.path.join(base, "icon.ico"))
        candidates.append(os.path.join(os.path.dirname(sys.executable), "icon.ico"))
    candidates.append(os.path.join(os.getcwd(), "icon.ico"))
    return next((path for path in candidates if os.path.exists(path)), None)


class TranslatorApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"MineAI Translator v{__version__}")
        self.geometry("1150x850")

        icon_path = _resolve_icon_path()
        if icon_path:
            try:
                self.iconbitmap(icon_path)
                self.iconbitmap(default=icon_path)
            except tk.TclError:
                pass

        ctk.set_appearance_mode(settings.get("GENERAL", "theme"))
        ctk.set_default_color_theme(settings.get("GENERAL", "color"))

        self.job_state = JobState()
        self.cache_std, self.cache_ai, polish_total = load_both_caches()
        self._job: TranslationJob | None = None
        self._ui_thread_id = threading.get_ident()
        self._ui_queue: queue.Queue[tuple[object, tuple]] = queue.Queue()
        self.auto_scroll = True

        self._build_ui()
        self._refresh_folder_label()
        self.after(50, self._drain_ui_queue)

        if polish_total:
            self.log(
                f"✨ Кэш проверен: исправлено/удалено ошибок: {polish_total}.",
                "magenta",
            )

    def _build_ui(self) -> None:
        self.geometry("1320x860")
        self.minsize(1080, 720)
        self.configure(fg_color=UI.APP_BG)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)

        def card(parent, title: str, subtitle: str | None = None):
            frame = ctk.CTkFrame(
                parent,
                corner_radius=14,
                fg_color=UI.CARD_BG,
                border_width=1,
                border_color=UI.BORDER,
            )
            ctk.CTkLabel(
                frame, text=title.upper(), anchor="w",
                font=("Segoe UI", 12, "bold"), text_color=UI.TEXT,
            ).pack(fill="x", padx=14, pady=(12, 0))
            if subtitle:
                ctk.CTkLabel(
                    frame, text=subtitle, anchor="w", justify="left", wraplength=335,
                    font=("Segoe UI", 10), text_color=UI.MUTED,
                ).pack(fill="x", padx=14, pady=(2, 4))
            body = ctk.CTkFrame(frame, fg_color="transparent")
            body.pack(fill="x", padx=14, pady=(8, 14))
            return frame, body

        def section_label(parent, text: str):
            ctk.CTkLabel(
                parent, text=text, anchor="w", font=("Segoe UI", 10, "bold"),
                text_color=UI.MUTED,
            ).pack(fill="x", pady=(8, 3))

        sidebar = ctk.CTkScrollableFrame(
            self, width=390, corner_radius=0, fg_color=UI.SIDEBAR_BG,
            scrollbar_button_color=UI.NEUTRAL,
        )
        sidebar.grid(row=0, column=0, sticky="nsew")

        brand = ctk.CTkFrame(sidebar, fg_color="transparent")
        brand.pack(fill="x", padx=14, pady=(16, 12))
        ctk.CTkLabel(
            brand, text="MineAI Translator", anchor="w",
            font=("Segoe UI", 22, "bold"), text_color=UI.TEXT,
        ).pack(fill="x")
        ctk.CTkLabel(
            brand, text=__version__, anchor="w",
            font=("Segoe UI", 11), text_color=UI.MUTED,
        ).pack(fill="x", pady=(1, 8))
        self.btn_settings = ctk.CTkButton(
            brand, text="⚙  Настройки", height=32,
            fg_color=UI.NEUTRAL, hover_color=UI.NEUTRAL_HOVER,
            command=self._open_settings,
        )
        self.btn_settings.pack(fill="x")

        project, body = card(sidebar, "Проект", "Источник, целевой язык и версия Minecraft")
        project.pack(fill="x", padx=12, pady=6)
        section_label(body, "ПАПКА MINECRAFT")
        folder_row = ctk.CTkFrame(body, fg_color="transparent")
        folder_row.pack(fill="x")
        self.lbl_folder = ctk.CTkLabel(
            folder_row, text="Не выбрана", anchor="w",
            font=("Segoe UI", 11), text_color=UI.MUTED,
        )
        self.lbl_folder.pack(side="left", fill="x", expand=True)
        folder_button = ctk.CTkButton(
            folder_row, text="Выбрать", width=82, height=28,
            fg_color=UI.NEUTRAL, hover_color=UI.NEUTRAL_HOVER,
            command=self._select_folder,
        )
        folder_button.pack(side="right", padx=(8, 0))
        ToolTip(folder_button, "Minecraft instance", "Выберите корневую папку сборки/instance, где находятся mods и config.")

        selectors = ctk.CTkFrame(body, fg_color="transparent")
        selectors.pack(fill="x", pady=(8, 0))

        for col in (0, 1):
            selectors.grid_columnconfigure(col, weight=1)
        self.var_lang = ctk.StringVar(value="Русский")
        self.var_mc_ver = ctk.StringVar(value="1.20.1")
        ctk.CTkLabel(selectors, text="Язык", anchor="w", font=("Segoe UI", 10), text_color=UI.MUTED).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ctk.CTkLabel(selectors, text="Minecraft", anchor="w", font=("Segoe UI", 10), text_color=UI.MUTED).grid(row=0, column=1, sticky="ew", padx=(4, 0))
        ctk.CTkOptionMenu(selectors, variable=self.var_lang, values=list(LANGUAGES.keys())).grid(row=1, column=0, sticky="ew", padx=(0, 4), pady=(2, 0))
        ctk.CTkOptionMenu(selectors, variable=self.var_mc_ver, values=MC_VERSIONS).grid(row=1, column=1, sticky="ew", padx=(4, 0), pady=(2, 0))

        output, body = card(sidebar, "Результат", "Resource Pack безопаснее; inplace меняет JAR")
        output.pack(fill="x", padx=12, pady=6)
        self.var_output = ctk.StringVar(value="resourcepack")
        ctk.CTkRadioButton(
            body, text="📦 Resource Pack + Datapack", variable=self.var_output,
            value="resourcepack", command=self._update_output_ui,
        ).pack(anchor="w", pady=3)
        self.entry_rp_name = ctk.CTkEntry(body, placeholder_text="Имя пакета")
        self.entry_rp_name.insert(0, "MineAI_Pack")
        self.entry_rp_name.pack(fill="x", padx=(22, 0), pady=(2, 6))
        inplace = ctk.CTkRadioButton(
            body, text="⚠  Перезаписать .jar", variable=self.var_output,
            value="inplace", command=self._update_output_ui,
        )
        inplace.pack(anchor="w", pady=3)
        ToolTip(inplace, "In-place режим", "Изменяет JAR-файлы. Перед стартом MineAI дополнительно попросит подтверждение.")

        scope, body = card(sidebar, "Что переводим")
        scope.pack(fill="x", padx=12, pady=6)
        self.var_mods = ctk.BooleanVar(value=True)
        self.var_books = ctk.BooleanVar(value=True)
        self.var_quests = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(body, text="Интерфейс модов", variable=self.var_mods).pack(anchor="w", pady=3)
        ctk.CTkCheckBox(body, text="Книги и исследования", variable=self.var_books).pack(anchor="w", pady=3)
        ctk.CTkCheckBox(body, text="Квесты (FTB + KubeJS)", variable=self.var_quests).pack(anchor="w", pady=3)

        engine, body = card(sidebar, "Движок", "Переводчик и режим обработки")
        engine.pack(fill="x", padx=12, pady=6)
        self.var_engine = ctk.StringVar(value="google")
        self.var_google_mode = ctk.StringVar(value="single")
        self.var_ai_mode = ctk.StringVar(value="safe")
        self.var_ai_provider = ctk.StringVar(value=settings.get("AI", "ai_provider") or "local")
        ctk.CTkRadioButton(body, text="Google Translate", variable=self.var_engine, value="google", command=self._update_engine_ui).pack(anchor="w", pady=3)
        self.frame_google = ctk.CTkFrame(body, fg_color="transparent")
        ctk.CTkRadioButton(self.frame_google, text="Построчно", variable=self.var_google_mode, value="single").pack(anchor="w", pady=2)
        ctk.CTkRadioButton(self.frame_google, text="Пачками", variable=self.var_google_mode, value="batch").pack(anchor="w", pady=2)
        ctk.CTkRadioButton(body, text="DeepL API", variable=self.var_engine, value="deepl", command=self._update_engine_ui).pack(anchor="w", pady=3)
        ctk.CTkRadioButton(body, text="Нейросеть (ИИ)", variable=self.var_engine, value="ai", command=self._update_engine_ui).pack(anchor="w", pady=3)
        self.frame_ai = ctk.CTkFrame(body, fg_color="transparent")
        ctk.CTkLabel(self.frame_ai, text="Провайдер", anchor="w", font=("Segoe UI", 10, "bold"), text_color=UI.MUTED).pack(fill="x", pady=(2, 2))
        ctk.CTkRadioButton(self.frame_ai, text="Локальный KoboldCPP", variable=self.var_ai_provider, value="local", command=self._update_engine_ui).pack(anchor="w", pady=2)
        ctk.CTkRadioButton(self.frame_ai, text="LM Studio", variable=self.var_ai_provider, value="lmstudio", command=self._update_engine_ui).pack(anchor="w", pady=2)
        ctk.CTkRadioButton(self.frame_ai, text="Ollama", variable=self.var_ai_provider, value="ollama", command=self._update_engine_ui).pack(anchor="w", pady=2)
        ctk.CTkRadioButton(self.frame_ai, text="Llama", variable=self.var_ai_provider, value="llama", command=self._update_engine_ui).pack(anchor="w", pady=2)
        ctk.CTkRadioButton(self.frame_ai, text="OpenRouter", variable=self.var_ai_provider, value="openrouter", command=self._update_engine_ui).pack(anchor="w", pady=2)
        ctk.CTkLabel(self.frame_ai, text="Режим", anchor="w", font=("Segoe UI", 10, "bold"), text_color=UI.MUTED).pack(fill="x", pady=(7, 2))
        ctk.CTkRadioButton(self.frame_ai, text="Стандартный", variable=self.var_ai_mode, value="safe").pack(anchor="w", pady=2)
        ctk.CTkRadioButton(self.frame_ai, text="Контекст + лор", variable=self.var_ai_mode, value="context").pack(anchor="w", pady=2)
        self.lbl_batch = ctk.CTkLabel(self.frame_ai, text="Размер пачки: 20 строк", anchor="w", font=("Segoe UI", 10, "bold"), text_color=UI.MUTED)
        self.lbl_batch.pack(fill="x", pady=(7, 0))
        self.slider_ai_batch = ctk.CTkSlider(
            self.frame_ai, from_=1, to=40, number_of_steps=39,
            command=lambda val: self.lbl_batch.configure(text=f"Размер пачки: {int(val)} строк"),
        )
        self.slider_ai_batch.set(20)
        self.slider_ai_batch.pack(fill="x", pady=3)
        try:
            fallback_val = settings.getboolean("AI", "fallback_google")
        except Exception:
            fallback_val = False
        self.var_ai_fallback = ctk.BooleanVar(value=fallback_val)
        ctk.CTkCheckBox(
            self.frame_ai, text="Fallback через Google", variable=self.var_ai_fallback,
        ).pack(anchor="w", pady=(7, 2))

        readiness = ctk.CTkFrame(body, corner_radius=9, fg_color=UI.INFO_BG)
        readiness.pack(fill="x", pady=(9, 0))
        self.lbl_engine_ready = ctk.CTkLabel(
            readiness, text="Проверка...", anchor="w",
            font=("Segoe UI", 10, "bold"), text_color=UI.INFO_TEXT,
        )
        self.lbl_engine_ready.pack(side="left", fill="x", expand=True, padx=9, pady=7)
        self.btn_engine_settings = ctk.CTkButton(
            readiness, text="Настроить", width=82, height=26,
            fg_color=UI.NEUTRAL, hover_color=UI.NEUTRAL_HOVER, command=self._open_settings,
        )
        self.btn_engine_settings.pack(side="right", padx=6, pady=5)

        mode, body = card(sidebar, "Режим обработки")
        mode.pack(fill="x", padx=12, pady=6)
        self.var_mode = ctk.StringVar(value="append")
        ctk.CTkRadioButton(body, text="Доперевод · сохранить готовое", variable=self.var_mode, value="append").pack(anchor="w", pady=3)
        ctk.CTkRadioButton(body, text="Пропуск · готовность ≥90%", variable=self.var_mode, value="skip").pack(anchor="w", pady=3)
        ctk.CTkRadioButton(body, text="С нуля · перезапись", variable=self.var_mode, value="force").pack(anchor="w", pady=3)

        actions = ctk.CTkFrame(sidebar, corner_radius=14, fg_color=UI.CARD_BG, border_width=1, border_color=UI.BORDER)
        actions.pack(fill="x", padx=12, pady=(6, 16))
        self.btn_migrate = ctk.CTkButton(actions, text="📦 Миграция ресурс-пака", fg_color=UI.CYAN, hover_color=UI.CYAN_HOVER, command=self._open_migration)
        self.btn_migrate.pack(fill="x", padx=12, pady=(12, 5))
        self.btn_analyze = ctk.CTkButton(actions, text="Анализ сборки", fg_color=UI.PRIMARY, hover_color=UI.PRIMARY_HOVER, command=self._start_analysis)
        self.btn_analyze.pack(fill="x", padx=12, pady=5)
        self.btn_start = ctk.CTkButton(actions, text="▶  НАЧАТЬ ПЕРЕВОД", height=42, font=("Segoe UI", 13, "bold"), fg_color=UI.SUCCESS, hover_color=UI.SUCCESS_HOVER, command=self._start_translation)
        self.btn_start.pack(fill="x", padx=12, pady=5)
        run_controls = ctk.CTkFrame(actions, fg_color="transparent")
        run_controls.pack(fill="x", padx=12, pady=(5, 12))

        for col in (0, 1):
            run_controls.grid_columnconfigure(col, weight=1)
        self.btn_pause = ctk.CTkButton(run_controls, text="⏸ ПАУЗА", height=36, fg_color=UI.WARNING, hover_color=UI.WARNING_HOVER, text_color="black", command=self._toggle_pause, state="disabled")
        self.btn_pause.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.btn_stop = ctk.CTkButton(run_controls, text="⏹ СТОП", height=36, fg_color=UI.DANGER, hover_color=UI.DANGER_HOVER, command=self._stop, state="disabled")
        self.btn_stop.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.grid(row=0, column=1, sticky="nsew", padx=16, pady=16)
        content.grid_rowconfigure(1, weight=1)
        content.grid_columnconfigure(0, weight=1)

        status_card = ctk.CTkFrame(content, corner_radius=16, fg_color=UI.CARD_BG, border_width=1, border_color=UI.BORDER)
        status_card.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        status_card.grid_columnconfigure(0, weight=1)
        status_header = ctk.CTkFrame(status_card, fg_color="transparent")
        status_header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        status_header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(status_header, text="СТАТУС", anchor="w", font=("Segoe UI", 12, "bold"), text_color=UI.TEXT).grid(row=0, column=0, sticky="w")
        self.lbl_status = ctk.CTkLabel(status_header, text="Ожидание...", anchor="e", font=("Segoe UI", 12), text_color=UI.MUTED)
        self.lbl_status.grid(row=0, column=1, sticky="e")

        metrics = ctk.CTkFrame(status_card, fg_color="transparent")
        metrics.grid(row=1, column=0, sticky="ew", padx=12, pady=4)
        for col in range(4):
            metrics.grid_columnconfigure(col, weight=1)

        def metric(col: int, title: str):
            frame = ctk.CTkFrame(metrics, corner_radius=11, fg_color=UI.CARD_ALT_BG, border_width=1, border_color=UI.BORDER)
            frame.grid(row=0, column=col, sticky="ew", padx=4)
            ctk.CTkLabel(frame, text=title.upper(), anchor="w", font=("Segoe UI", 9, "bold"), text_color=UI.MUTED).pack(fill="x", padx=10, pady=(8, 1))
            value = ctk.CTkLabel(frame, text="—", anchor="w", font=("Segoe UI", 17, "bold"), text_color=UI.TEXT)
            value.pack(fill="x", padx=10, pady=(0, 8))
            return value

        self.lbl_kpi_processed = metric(0, "Обработано")
        self.lbl_kpi_ok = metric(1, "Успешно")
        self.lbl_kpi_errors = metric(2, "Ошибки")
        self.lbl_kpi_eta = metric(3, "Осталось")
        self.progress = ctk.CTkProgressBar(status_card, height=10, progress_color=UI.PRIMARY)
        self.progress.grid(row=2, column=0, sticky="ew", padx=16, pady=(10, 16))
        self.progress.set(0)

        log_card = ctk.CTkFrame(content, corner_radius=16, fg_color=UI.CARD_BG, border_width=1, border_color=UI.BORDER)
        log_card.grid(row=1, column=0, sticky="nsew")
        log_card.grid_rowconfigure(1, weight=1)
        log_card.grid_columnconfigure(0, weight=1)
        log_header = ctk.CTkFrame(log_card, fg_color="transparent")
        log_header.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        log_header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(log_header, text="ЖУРНАЛ", anchor="w", font=("Segoe UI", 12, "bold"), text_color=UI.TEXT).grid(row=0, column=0, sticky="w")
        self.btn_log = ctk.CTkButton(log_header, text="Открыть файл", width=105, height=28, fg_color=UI.NEUTRAL, hover_color=UI.NEUTRAL_HOVER, command=self._open_log_file)
        self.btn_log.grid(row=0, column=1, padx=(8, 0))
        ctk.CTkButton(log_header, text="Очистить", width=82, height=28, fg_color="transparent", border_width=1, border_color=UI.BORDER, command=self._clear_log).grid(row=0, column=2, padx=(8, 0))

        self.textbox = ctk.CTkTextbox(log_card, font=("Consolas", 12), fg_color=UI.LOG_BG, corner_radius=10, wrap="none")
        self.textbox.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))

        def _is_ctrl_pressed() -> bool:
            if sys.platform == "win32":
                try:
                    return bool(ctypes.windll.user32.GetAsyncKeyState(0x11) & 0x8000)
                except Exception:
                    return False
            return False

        def prevent_typing(event):
            if event.state & 4 or _is_ctrl_pressed():
                return None
            if event.keysym in ["Up", "Down", "Left", "Right", "Prior", "Next", "Home", "End"]:
                return None
            return "break"

        self.textbox.bind("<Key>", prevent_typing)

        def _fix_ctrl_copy(event):
            ctrl = bool(event.state & 4)
            if sys.platform == "win32":
                try:
                    ctrl = ctrl or bool(ctypes.windll.user32.GetAsyncKeyState(0x11) & 0x8000)
                except Exception:
                    pass
            if not ctrl:
                return None
            actions = {67: "<<Copy>>", 65: "<<SelectAll>>"}
            if event.keycode in actions:
                event.widget.event_generate(actions[event.keycode])
                return "break"
            return None

        inner = getattr(self.textbox, "_textbox", self.textbox)
        inner.bind("<Key>", _fix_ctrl_copy, add="+")
        for tag, color in [
            ("green", "#2ecc71"), ("lime", "#7bed9f"), ("yellow", "#f1c40f"),
            ("gold", "#ffd700"), ("orange", "#ff9f43"), ("red", "#ff4d4d"),
            ("pink", "#ff6b81"), ("cyan", "#00e5ff"), ("blue", "#54a0ff"),
            ("magenta", "#c084fc"), ("dim", "#7f8c8d"), ("gray", "#b2bec3"),
            ("white", "#ffffff"),
        ]:
            self.textbox.tag_config(tag, foreground=color)
        self.textbox.bind("<Button-1>", lambda _e: self._on_scroll_interaction())
        self.textbox.bind("<MouseWheel>", lambda _e: self._on_scroll_interaction())

        self._update_engine_ui()
        self._update_output_ui()
        self._refresh_kpis()

    def _refresh_kpis(self) -> None:
        required = (
            "lbl_kpi_processed",
            "lbl_kpi_ok",
            "lbl_kpi_errors",
            "lbl_kpi_eta",
        )
        if not all(hasattr(self, name) for name in required):
            return
        snapshot = self.job_state.snapshot()
        if snapshot.total_strings > 0:
            processed = min(snapshot.translated_strings, snapshot.total_strings)
            processed_text = f"{processed:,} / {snapshot.total_strings:,}".replace(",", " ")
        else:
            processed_text = "—"
        self.lbl_kpi_processed.configure(text=processed_text)
        self.lbl_kpi_ok.configure(text=f"{snapshot.ok_strings:,}".replace(",", " "))
        self.lbl_kpi_errors.configure(text=f"{snapshot.failed_strings:,}".replace(",", " "))
        self.lbl_kpi_eta.configure(text=self.job_state.eta_text())

    def _validate_minecraft_dir(self, *, require_mods: bool = False) -> bool:
        raw = settings.get("GENERAL", "mc_dir").strip()
        if not raw:
            messagebox.showwarning(
                "Папка Minecraft не выбрана",
                "Сначала выберите папку Minecraft instance/сборки.",
            )
            return False
        root = Path(raw)
        if not root.is_dir():
            messagebox.showwarning(
                "Папка недоступна",
                f"Каталог не существует:\n{root}",
            )
            return False
        if require_mods and not (root / "mods").is_dir():
            messagebox.showwarning(
                "Папка mods не найдена",
                "Для миграции выбранный instance должен содержать папку mods.",
            )
            return False
        return True

    def _validate_scope(self) -> bool:
        if self.var_mods.get() or self.var_books.get() or self.var_quests.get():
            return True
        messagebox.showwarning(
            "Нечего обрабатывать",
            "Выберите хотя бы одну область: моды, книги или квесты.",
        )
        return False

    def _engine_readiness(self) -> tuple[bool, str]:
        engine = self.var_engine.get()
        if engine == "google":
            return True, "Google готов"
        if engine == "deepl":
            if settings.get("API", "deepl_key").strip():
                return True, "DeepL настроен"
            return False, "Не указан API-ключ DeepL"
        if engine == "ai":
            if self.var_ai_provider.get() == "openrouter":
                if not settings.get("OPENROUTER", "api_key").strip():
                    return False, "Не указан ключ OpenRouter"
                model = settings.get("OPENROUTER", "model").strip()
                if not model:
                    return False, "Не выбрана модель OpenRouter"
                return True, f"OpenRouter · {model}"
            if self.var_ai_provider.get() == "lmstudio":
                base_url = settings.get("LMSTUDIO", "base_url").strip()
                model = settings.get("LMSTUDIO", "model").strip()
                if not base_url:
                    return False, "Не указан адрес LM Studio"
                if not model:
                    return False, "Не выбрана модель LM Studio"
                return True, f"LM Studio · {model}"
            if self.var_ai_provider.get() == "ollama":
                base_url = settings.get("OLLAMA", "base_url").strip()
                model = settings.get("OLLAMA", "model").strip()
                if not base_url:
                    return False, "Не указан адрес Ollama"
                if not model:
                    return False, "Не выбрана модель Ollama"
                return True, f"Ollama · {model}"
            if self.var_ai_provider.get() == "llama":
                base_url = settings.get("LLAMA", "base_url").strip()
                model = settings.get("LLAMA", "model").strip()
                if not base_url:
                    return False, "Не указан адрес Llama"
                if not model:
                    return False, "Не выбрана модель Llama"
                return True, f"Llama · {model}"
            model_path = settings.get("AI", "model_path").strip()
            if not model_path:
                return False, "Не выбрана локальная GGUF-модель"
            if not Path(model_path).is_file():
                return False, "Файл GGUF-модели недоступен"
            return True, f"KoboldCPP · {Path(model_path).name}"
        return False, "Неизвестный движок"

    def _refresh_engine_readiness(self) -> None:
        if not hasattr(self, "lbl_engine_ready"):
            return
        ready, text = self._engine_readiness()
        self.lbl_engine_ready.configure(
            text=("✓ " if ready else "⚠ ") + text,
            text_color=("#79d89a" if ready else "#f6c85f"),
        )
        if hasattr(self, "btn_engine_settings"):
            self.btn_engine_settings.configure(state="disabled" if ready else "normal")

    def _validate_engine_ready(self) -> bool:
        ready, text = self._engine_readiness()
        if ready:
            return True
        messagebox.showwarning(
            "Движок не настроен",
            text + "\n\nПроверьте настройки перед запуском.",
        )
        return False

    def _confirm_inplace(self) -> bool:
        if self.var_output.get() != "inplace":
            return True
        return messagebox.askyesno(
            "Подтвердить изменение JAR",
            "Режим inplace изменяет файлы модов напрямую.\n\n"
            "Безопасный Resource Pack рекомендуется для финального релиза.\n\n"
            "Продолжить?",
            icon="warning",
        )

    def _reset_run_ui(self) -> None:
        self._clear_log()
        self.progress.set(0.0)
        self.lbl_status.configure(text="Подготовка...")
        self._refresh_kpis()

    def _after_settings_saved(self) -> None:
        self._refresh_folder_label()
        self._refresh_engine_readiness()

    def _job_instance(self) -> TranslationJob:
        return TranslationJob(
            settings,
            self.cache_std,
            self.cache_ai,
            self.job_state,
            on_log=self.log,
            on_status=self.set_status,
            on_row=self.log_row,
        )

    def _translation_options(self) -> TranslationOptions:
        return TranslationOptions(
            mc_dir=settings.get("GENERAL", "mc_dir"),
            language_label=self.var_lang.get(),
            mc_version=self.var_mc_ver.get(),
            output_mode=self.var_output.get(),
            pack_name=self.entry_rp_name.get().strip(),
            engine=self.var_engine.get(),
            google_mode=self.var_google_mode.get(),
            ai_mode=self.var_ai_mode.get(),
            ai_batch=int(self.slider_ai_batch.get()),
            ai_provider=self.var_ai_provider.get(),
            process_mode=self.var_mode.get(),
            translate_mods=self.var_mods.get(),
            translate_books=self.var_books.get(),
            translate_quests=self.var_quests.get(),
        )

    def _open_settings(self) -> None:
        SettingsWindow(self, settings, self._after_settings_saved)

    def _refresh_folder_label(self) -> None:
        path = settings.get("GENERAL", "mc_dir")

        if not path:
            self.lbl_folder.configure(text="Не выбрана", text_color=UI.MUTED)
            return
        display = f"...{path[-32:]}" if len(path) > 32 else path
        self.lbl_folder.configure(text=display, text_color=UI.TEXT)

    def _select_folder(self) -> None:
        path = filedialog.askdirectory()
        if path:
            settings.set("GENERAL", "mc_dir", path)
            self._refresh_folder_label()

    def _update_output_ui(self) -> None:
        state = "normal" if self.var_output.get() == "resourcepack" else "disabled"
        self.entry_rp_name.configure(state=state)

    def _update_engine_ui(self) -> None:
        self.frame_ai.pack_forget()
        self.frame_google.pack_forget()
        if self.var_engine.get() == "ai":
            self.frame_ai.pack(fill="x", padx=(22, 0), pady=(2, 4))
        elif self.var_engine.get() == "google":
            self.frame_google.pack(fill="x", padx=(22, 0), pady=(2, 4))
        self._refresh_engine_readiness()

    def _on_scroll_interaction(self) -> None:
        self.auto_scroll = self.textbox.yview()[1] >= 0.99

    def _ensure_ui_thread(self, callback, *args) -> bool:
        """Queue Tk work without calling any Tk API from a worker thread."""
        ui_thread_id = getattr(self, "_ui_thread_id", threading.main_thread().ident)
        if threading.get_ident() != ui_thread_id:
            self._ui_queue.put((callback, args))
            return False
        return True

    def _drain_ui_queue(self) -> None:
        while True:
            try:
                callback, args = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback(*args)
            except Exception:
                error = traceback.format_exc()
                try:
                    self.log(f"❌ Ошибка UI callback:\n{error}", "red")
                except Exception:
                    pass
        self.after(50, self._drain_ui_queue)

    def log(self, message: str, tag: str = "white") -> None:
        if not self._ensure_ui_thread(self.log, message, tag):
            return

        at_bottom = self.textbox.yview()[1] >= 0.99
        self.textbox.insert("end", message + "\n", tag)
        if self.auto_scroll or at_bottom:
            self.textbox.see("end")


        # Запись в общий текстовый лог
        try:
            with open("mineai_log.txt", "a", encoding="utf-8") as f:
                f.write(message + "\n")
        except Exception:
            pass

    def log_row(self, icon: str, name: str, kind: str, trans_c: int, en_c: int, pct: int) -> None:
        if not self._ensure_ui_thread(
            self.log_row,
            icon,
            name,
            kind,
            trans_c,
            en_c,
            pct,
        ):
            return

        at_bottom = self.textbox.yview()[1] >= 0.99
        self.textbox.insert("end", f"{icon} {name[:34]:<35}", "cyan")
        self.textbox.insert("end", f"[{kind}]".ljust(15), "magenta")
        self.textbox.insert("end", f"{trans_c}/{en_c}".ljust(12), "white")
        color = "green" if pct >= 90 else ("yellow" if pct >= 50 else "red")
        self.textbox.insert("end", f"{pct}%\n", color)
        if self.auto_scroll or at_bottom:
            self.textbox.see("end")


    def set_status(self, text: str, progress: float | None) -> None:
        if not self._ensure_ui_thread(self.set_status, text, progress):
            return
        if progress is not None:
            self.progress.set(progress)
        self.lbl_status.configure(text=text)
        self._refresh_kpis()

    def _lock_ui(self, locked: bool) -> None:
        if not self._ensure_ui_thread(self._lock_ui, locked):
            return
        state = "disabled" if locked else "normal"
        rev = "normal" if locked else "disabled"
        self.btn_settings.configure(state=state)
        self.btn_analyze.configure(state=state)
        self.btn_start.configure(state=state)
        self.btn_stop.configure(state=rev)
        self.btn_pause.configure(state=rev)
        if hasattr(self, "btn_migrate"):
            self.btn_migrate.configure(state=state)
        if hasattr(self, "btn_engine_settings"):
            if locked:
                self.btn_engine_settings.configure(state="disabled")
            else:
                self._refresh_engine_readiness()

    def _toggle_pause(self) -> None:
        is_paused = self.job_state.toggle_pause()
        if is_paused:
            self.btn_pause.configure(text="▶ ПРОДОЛЖИТЬ", fg_color=UI.CYAN, text_color="white")
            self.log("⏸ Пауза", "yellow")
        else:
            self.btn_pause.configure(text="⏸ ПАУЗА", fg_color=UI.WARNING, text_color="black")
            self.log("▶ Продолжение", "green")

    def _stop(self) -> None:
        active_job = self._job
        if active_job is not None:
            active_job.stop()
        else:
            self.job_state.stop()
        self.btn_stop.configure(state="disabled")
        self.btn_pause.configure(state="disabled")
        self.set_status("🛑 Остановка...", None)

    def _clear_log(self) -> None:

        self.textbox.delete("1.0", "end")


    def _start_analysis(self) -> None:
        if not self._validate_minecraft_dir():
            return
        if not self._validate_scope():
            return
        self._lock_ui(True)
        self.job_state.start()
        self._reset_run_ui()
        self._job = self._job_instance()
        options = self._translation_options()
        threading.Thread(
            target=lambda: self._run_analysis_thread(options),
            daemon=True,
        ).start()

    def _run_analysis_thread(self, options: TranslationOptions) -> None:
        try:
            if self._job is not None:
                self._job.run_analysis(options)
        except Exception:
            error = traceback.format_exc()
            self.log(f"❌ Ошибка анализа:\n{error}", "red")
            self.set_status("❌ Ошибка анализа", None)
        finally:
            self.job_state.finish()
            self._job = None
            self._lock_ui(False)

    def _start_translation(self) -> None:
        if not self._validate_minecraft_dir():
            return
        if not self._validate_scope():
            return
        if not self._validate_engine_ready():
            return
        if not self._confirm_inplace():
            return
        if self.var_engine.get() == "ai":
            settings.set("AI", "ai_provider", self.var_ai_provider.get())
            settings.set("AI", "fallback_google", self.var_ai_fallback.get())
        self._lock_ui(True)
        self.job_state.start()
        self.btn_pause.configure(text="⏸ ПАУЗА", fg_color=UI.WARNING, text_color="black")
        self._reset_run_ui()
        self._job = self._job_instance()
        options = self._translation_options()
        threading.Thread(
            target=lambda: self._run_translation_thread(options),
            daemon=True,
        ).start()


    def _open_log_file(self) -> None:
        log_path = Path("mineai_log.txt").resolve()
        if not log_path.exists():
            self.log("❌ Лог-файл еще не создан.", "yellow")
            return

        try:
            if sys.platform == "win32":
                os.startfile(str(log_path))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(log_path)])
            else:
                try:
                    subprocess.Popen(["xdg-open", str(log_path)])
                except OSError:
                    if not webbrowser.open(log_path.as_uri()):
                        raise RuntimeError("не найдено приложение для открытия файла")
        except Exception as exc:
            self.log(f"❌ Не удалось открыть лог: {exc}", "red")

    def _run_translation_thread(self, options: TranslationOptions) -> None:
        try:
            if self._job is not None:
                self._job.run_translation(options)
        except Exception:
            error = traceback.format_exc()
            self.log(f"❌ Ошибка перевода:\n{error}", "red")
            self.set_status("❌ Ошибка перевода", None)
        finally:
            self.job_state.finish()
            self._job = None
            self._lock_ui(False)

    def _open_migration(self) -> None:
        if not self._validate_minecraft_dir(require_mods=True):
            return
        mc_dir = settings.get("GENERAL", "mc_dir")
        MigrationWindow(
            self,
            mc_dir,
            self.var_lang.get(),
            self.cache_std,
            self.cache_ai,
            self.log,
        )


def run() -> None:
    # Keep legacy callers safe: lifecycle owns WM_DELETE_WINDOW and cleanup.
    from mineai.gui.lifecycle import run as lifecycle_run

    lifecycle_run()
