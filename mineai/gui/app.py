import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

from mineai import __version__
from mineai.cache import load_both_caches
from mineai.config import settings
from mineai.constants import LANGUAGES, MC_VERSIONS
from mineai.gui.settings import SettingsWindow
from mineai.runtime.job import TranslationJob, TranslationOptions
from mineai.runtime.state import JobState


class TranslatorApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"MineAI Translator v{__version__}")
        self.geometry("1150x850")

        if os.path.exists("icon.ico"):
            try:
                self.iconbitmap("icon.ico")
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
            self.log(f"✨ Кэш отполирован: исправлено {polish_total} строк.", "magenta")

    def _build_ui(self) -> None:
        left = ctk.CTkScrollableFrame(self, width=370)
        left.pack(side="left", fill="y", padx=10, pady=10)

        ctk.CTkButton(left, text="⚙ НАСТРОЙКИ", fg_color="#555", command=self._open_settings).pack(
            fill="x", padx=10, pady=(0, 15)
        )

        ctk.CTkLabel(left, text="ПАПКА MINECRAFT", font=("", 14, "bold")).pack(pady=(5, 5))
        self.lbl_folder = ctk.CTkLabel(left, text="Не выбрана", text_color="gray")
        self.lbl_folder.pack()
        ctk.CTkButton(left, text="📁 Выбрать папку", fg_color="#444", command=self._select_folder).pack(
            pady=5, fill="x", padx=20
        )

        ctk.CTkLabel(left, text="ЦЕЛЕВОЙ ЯЗЫК", font=("", 14, "bold")).pack(pady=(15, 5))
        self.var_lang = ctk.StringVar(value="Русский")
        ctk.CTkOptionMenu(left, variable=self.var_lang, values=list(LANGUAGES.keys())).pack(fill="x", padx=20)

        ctk.CTkLabel(left, text="ВЕРСИЯ ИГРЫ", font=("", 14, "bold")).pack(pady=(15, 5))
        self.var_mc_ver = ctk.StringVar(value="1.20.1")
        ctk.CTkOptionMenu(left, variable=self.var_mc_ver, values=MC_VERSIONS).pack(fill="x", padx=20)

        ctk.CTkLabel(left, text="МЕТОД СОХРАНЕНИЯ", font=("", 14, "bold")).pack(pady=(15, 5))
        self.var_output = ctk.StringVar(value="resourcepack")
        ctk.CTkRadioButton(
            left,
            text="📦 Resource Pack + Datapack (рекомендуется)",
            variable=self.var_output,
            value="resourcepack",
            command=self._update_output_ui,
        ).pack(anchor="w", padx=20, pady=5)
        self.entry_rp_name = ctk.CTkEntry(left, placeholder_text="Имя zip-файлов...")
        self.entry_rp_name.insert(0, "MineAI_Pack")
        self.entry_rp_name.pack(fill="x", padx=40, pady=(0, 5))
        ctk.CTkRadioButton(
            left,
            text="⚠️ Перезаписать .jar (ломает подписи)",
            variable=self.var_output,
            value="inplace",
            command=self._update_output_ui,
        ).pack(anchor="w", padx=20, pady=5)

        ctk.CTkLabel(left, text="ЧТО ПЕРЕВОДИМ?", font=("", 14, "bold")).pack(pady=(15, 5))
        self.var_mods = ctk.BooleanVar(value=True)
        self.var_books = ctk.BooleanVar(value=True)
        self.var_quests = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(left, text="Интерфейс (моды)", variable=self.var_mods).pack(anchor="w", padx=20, pady=2)
        ctk.CTkCheckBox(left, text="Справочники и исследования", variable=self.var_books).pack(anchor="w", padx=20, pady=2)
        ctk.CTkCheckBox(left, text="Квесты (FTB + KubeJS)", variable=self.var_quests).pack(anchor="w", padx=20, pady=2)

        ctk.CTkLabel(left, text="ДВИЖОК ПЕРЕВОДА", font=("", 14, "bold")).pack(pady=(15, 5))
        self.var_engine = ctk.StringVar(value="google")
        self.frame_google = ctk.CTkFrame(left, fg_color="transparent")
        self.var_google_mode = ctk.StringVar(value="single")
        ctk.CTkRadioButton(left, text="Google Translate", variable=self.var_engine, value="google", command=self._update_engine_ui).pack(
            anchor="w", padx=20, pady=5
        )
        ctk.CTkRadioButton(self.frame_google, text="Построчно (точно)", variable=self.var_google_mode, value="single").pack(anchor="w", pady=2)
        ctk.CTkRadioButton(self.frame_google, text="Пачками (быстро)", variable=self.var_google_mode, value="batch").pack(anchor="w", pady=2)
        ctk.CTkRadioButton(left, text="DeepL API", variable=self.var_engine, value="deepl", command=self._update_engine_ui).pack(
            anchor="w", padx=20, pady=5
        )
        self.frame_ai = ctk.CTkFrame(left, fg_color="transparent")
        self.var_ai_mode = ctk.StringVar(value="safe")
        ctk.CTkRadioButton(left, text="Нейросеть (ИИ)", variable=self.var_engine, value="ai", command=self._update_engine_ui).pack(
            anchor="w", padx=20, pady=5
        )
        self.var_ai_provider = ctk.StringVar(value=settings.get("AI", "ai_provider") or "local")
        ctk.CTkLabel(self.frame_ai, text="Провайдер:", font=("", 11, "bold")).pack(anchor="w", pady=(0, 2))
        ctk.CTkRadioButton(
            self.frame_ai, text="Локально (KoboldCPP)", variable=self.var_ai_provider, value="local"
        ).pack(anchor="w", pady=1)
        ctk.CTkRadioButton(
            self.frame_ai, text="OpenRouter (облако)", variable=self.var_ai_provider, value="openrouter"
        ).pack(anchor="w", pady=1)
        
        # --- НОВЫЙ БЛОК НАСТРОЕК ИИ С ПОЛЗУНКОМ ---
        ctk.CTkLabel(self.frame_ai, text="Режим ИИ:", font=("", 11, "bold")).pack(anchor="w", pady=(6, 0))
        ctk.CTkRadioButton(self.frame_ai, text="Стандартный", variable=self.var_ai_mode, value="safe").pack(anchor="w", pady=2)
        ctk.CTkRadioButton(self.frame_ai, text="Контекст + лор", variable=self.var_ai_mode, value="context").pack(anchor="w", pady=2)
        
        # Динамический текст для ползунка
        self.lbl_batch = ctk.CTkLabel(self.frame_ai, text="Размер пачки: 20 строк", font=("", 11, "bold"))
        self.lbl_batch.pack(anchor="w", pady=(6, 0))
        
        # Сам ползунок (от 1 до 40 с шагом 1)
        self.slider_ai_batch = ctk.CTkSlider(
            self.frame_ai, from_=1, to=40, number_of_steps=39,
            command=lambda val: self.lbl_batch.configure(text=f"Размер пачки: {int(val)} строк")
        )
        self.slider_ai_batch.set(20)
        self.slider_ai_batch.pack(fill="x", pady=2)

        # --- НОВАЯ ГАЛОЧКА ДЛЯ ПОДСТРАХОВКИ GOOGLE ---
        try:
            fallback_val = settings.getboolean("AI", "fallback_google")
        except Exception:
            fallback_val = False
            
        self.var_ai_fallback = ctk.BooleanVar(value=fallback_val)
        ctk.CTkCheckBox(
            self.frame_ai, 
            text="Переводить непереведенное через Google", 
            variable=self.var_ai_fallback
        ).pack(anchor="w", pady=(10, 0))

        ctk.CTkLabel(left, text="РЕЖИМ ОБРАБОТКИ", font=("", 14, "bold")).pack(pady=(15, 5))
        self.var_mode = ctk.StringVar(value="append")
        ctk.CTkRadioButton(left, text="Доперевод (сохранить старое)", variable=self.var_mode, value="append").pack(anchor="w", padx=20, pady=2)
        ctk.CTkRadioButton(left, text="Пропуск (от 90%)", variable=self.var_mode, value="skip").pack(anchor="w", padx=20, pady=2)
        ctk.CTkRadioButton(left, text="С нуля (перезапись)", variable=self.var_mode, value="force").pack(anchor="w", padx=20, pady=2)

        self.btn_analyze = ctk.CTkButton(
            left, text="Анализ сборки", fg_color="#0066cc", hover_color="#004c99", command=self._start_analysis
        )
        self.btn_analyze.pack(pady=(20, 10), fill="x", padx=20)
        self.btn_start = ctk.CTkButton(
            left,
            text="▶ НАЧАТЬ ПЕРЕВОД",
            fg_color="#28a745",
            hover_color="#218838",
            height=40,
            font=("", 14, "bold"),
            command=self._start_translation,
        )
        self.btn_start.pack(pady=5, fill="x", padx=20)
        self.btn_pause = ctk.CTkButton(
            left,
            text="⏸ ПАУЗА",
            fg_color="#ffc107",
            text_color="black",
            hover_color="#e0a800",
            height=40,
            command=self._toggle_pause,
            state="disabled",
        )
        self.btn_pause.pack(pady=5, fill="x", padx=20)
        self.btn_stop = ctk.CTkButton(
            left,
            text="⏹ ОСТАНОВИТЬ",
            fg_color="#dc3545",
            hover_color="#c82333",
            height=40,
            command=self._stop,
            state="disabled",
        )
        self.btn_stop.pack(pady=(5, 10), fill="x", padx=20)
        
        # --- НОВАЯ КНОПКА ЛОГОВ ---
        self.btn_log = ctk.CTkButton(
            left,
            text="📜 ОТКРЫТЬ ЛОГ",
            fg_color="#555",
            hover_color="#444",
            height=30,
            command=self._open_log_file,
        )
        self.btn_log.pack(pady=(0, 10), fill="x", padx=20)
        
        right = ctk.CTkFrame(self)
        right.pack(side="right", fill="both", expand=True, padx=(0, 10), pady=10)
        self.textbox = ctk.CTkTextbox(right, state="disabled", font=("Consolas", 13))
        self.textbox.pack(fill="both", expand=True, padx=10, pady=10)
        for tag, color in [
            ("green", "#2ecc71"),
            ("yellow", "#f1c40f"),
            ("red", "#e74c3c"),
            ("cyan", "#00e5ff"),
            ("magenta", "#b000ff"),
            ("dim", "#888888"),
            ("white", "#ffffff"),
        ]:
            self.textbox.tag_config(tag, foreground=color)
        self.textbox.bind("<Button-1>", lambda _e: self._on_scroll_interaction())
        self.textbox.bind("<MouseWheel>", lambda _e: self._on_scroll_interaction())

        self.progress = ctk.CTkProgressBar(right)
        self.progress.pack(fill="x", padx=10, pady=(0, 5))
        self.progress.set(0)
        self.lbl_status = ctk.CTkLabel(right, text="Ожидание...", font=("", 14))
        self.lbl_status.pack(pady=(0, 10))

        self._update_engine_ui()
        self._update_output_ui()

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
        SettingsWindow(self, settings, self._refresh_folder_label)

    def _refresh_folder_label(self) -> None:
        path = settings.get("GENERAL", "mc_dir")
        self.lbl_folder.configure(text=f"...{path[-25:]}" if len(path) > 25 else path)

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
            self.frame_ai.pack(fill="x", padx=40, pady=5)
        elif self.var_engine.get() == "google":
            self.frame_google.pack(fill="x", padx=40, pady=0)

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
            callback(*args)
        self.after(50, self._drain_ui_queue)

    def log(self, message: str, tag: str = "white") -> None:
        if not self._ensure_ui_thread(self.log, message, tag):
            return
        self.textbox.configure(state="normal")
        at_bottom = self.textbox.yview()[1] >= 0.99
        self.textbox.insert("end", message + "\n", tag)
        if self.auto_scroll or at_bottom:
            self.textbox.see("end")
        self.textbox.configure(state="disabled")
        
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
        self.textbox.configure(state="normal")
        at_bottom = self.textbox.yview()[1] >= 0.99
        self.textbox.insert("end", f"{icon} {name[:34]:<35}", "cyan")
        self.textbox.insert("end", f"[{kind}]".ljust(15), "magenta")
        self.textbox.insert("end", f"{trans_c}/{en_c}".ljust(12), "white")
        color = "green" if pct >= 90 else ("yellow" if pct >= 50 else "red")
        self.textbox.insert("end", f"{pct}%\n", color)
        if self.auto_scroll or at_bottom:
            self.textbox.see("end")
        self.textbox.configure(state="disabled")

    def set_status(self, text: str, progress: float | None) -> None:
        if not self._ensure_ui_thread(self.set_status, text, progress):
            return
        if progress is not None:
            self.progress.set(progress)
        self.lbl_status.configure(text=text)

    def _lock_ui(self, locked: bool) -> None:
        if not self._ensure_ui_thread(self._lock_ui, locked):
            return
        state = "disabled" if locked else "normal"
        rev = "normal" if locked else "disabled"
        self.btn_analyze.configure(state=state)
        self.btn_start.configure(state=state)
        self.btn_stop.configure(state=rev)
        self.btn_pause.configure(state=rev)

    def _toggle_pause(self) -> None:
        self.job_state.is_paused = not self.job_state.is_paused
        if self.job_state.is_paused:
            self.btn_pause.configure(text="▶ ПРОДОЛЖИТЬ", fg_color="#17a2b8", text_color="white")
            self.log("⏸ Пауза", "yellow")
        else:
            self.btn_pause.configure(text="⏸ ПАУЗА", fg_color="#ffc107", text_color="black")
            self.log("▶ Продолжение", "green")

    def _stop(self) -> None:
        active_job = self._job
        if active_job is not None:
            active_job.stop()
        else:
            self.job_state.stop()
        self.btn_stop.configure(state="disabled")
        self.btn_pause.configure(state="disabled")
        self.set_status("🛑 Остановка...", 1.0)

    def _clear_log(self) -> None:
        self.textbox.configure(state="normal")
        self.textbox.delete("1.0", "end")
        self.textbox.configure(state="disabled")

    def _start_analysis(self) -> None:
        self._lock_ui(True)
        self.job_state.is_running = True
        self.job_state.is_paused = False
        self._clear_log()
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
        finally:
            self.job_state.is_running = False
            self._job = None
            self._lock_ui(False)

    def _start_translation(self) -> None:
        if not settings.get("GENERAL", "mc_dir"):
            messagebox.showerror("Ошибка", "Выберите папку Minecraft!")
            return
        if self.var_engine.get() == "ai":
            settings.set("AI", "ai_provider", self.var_ai_provider.get())
            settings.set("AI", "fallback_google", self.var_ai_fallback.get()) # <-- СОХРАНЯЕМ ВЫБОР
        self._lock_ui(True)
        self.job_state.is_running = True
        self.job_state.is_paused = False
        self.btn_pause.configure(text="⏸ ПАУЗА", fg_color="#ffc107", text_color="black")
        self._clear_log()
        self._job = self._job_instance()
        options = self._translation_options()
        threading.Thread(
            target=lambda: self._run_translation_thread(options),
            daemon=True,
        ).start()
    
    
    def _open_log_file(self) -> None:
        log_path = "mineai_log.txt"
        if os.path.exists(log_path):
            try:
                # Так как ты работаешь на Windows, используем os.startfile
                os.startfile(log_path)
            except Exception as e:
                self.log(f"❌ Не удалось открыть лог: {e}", "red")
        else:
            self.log("❌ Лог-файл еще не создан.", "yellow")
            
            
    def _run_translation_thread(self, options: TranslationOptions) -> None:
        try:
            if self._job is not None:
                self._job.run_translation(options)
        finally:
            self.job_state.is_running = False
            self._job = None
            self._lock_ui(False)


def run() -> None:
    app = TranslatorApp()
    app.mainloop()
