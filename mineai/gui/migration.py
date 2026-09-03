import os
import threading
import traceback

import customtkinter as ctk
from tkinter import filedialog, messagebox

from mineai.constants import LANGUAGES
from mineai.gui.style import UI
from mineai.processors.migration import run_migration


class MigrationWindow(ctk.CTkToplevel):
    def __init__(self, parent, mc_dir: str, lang_label: str, cache_std, cache_ai, log_callback):
        super().__init__(parent)
        self.title("📦 Миграция перевода")
        self.geometry("620x500")
        self.minsize(520, 420)
        self.resizable(True, True)
        self.configure(fg_color=UI.APP_BG)
        self.grab_set()

        self.mc_dir = mc_dir
        self.lang_api_code = LANGUAGES[lang_label]["api"]
        self.cache_std = cache_std
        self.cache_ai = cache_ai
        self.log_callback = log_callback

        card = ctk.CTkFrame(
            self,
            corner_radius=14,
            fg_color=UI.CARD_BG,
            border_width=1,
            border_color=UI.BORDER,
        )
        card.pack(fill="both", expand=True, padx=16, pady=16)

        ctk.CTkLabel(
            card,
            text="Миграция готового перевода",
            anchor="w",
            font=("Segoe UI", 18, "bold"),
            text_color=UI.TEXT,
        ).pack(fill="x", padx=16, pady=(16, 2))
        ctk.CTkLabel(
            card,
            text=(
                "Импортирует строки из существующего Resource Pack в отдельный imported cache. "
                "Исходный ZIP не изменяется."
            ),
            anchor="w",
            justify="left",
            wraplength=540,
            font=("Segoe UI", 10),
            text_color=UI.MUTED,
        ).pack(fill="x", padx=16, pady=(0, 12))

        ctk.CTkLabel(
            card,
            text="RESOURCE PACK (.ZIP)",
            anchor="w",
            font=("Segoe UI", 10, "bold"),
            text_color=UI.MUTED,
        ).pack(fill="x", padx=16)
        zip_row = ctk.CTkFrame(card, fg_color="transparent")
        zip_row.pack(fill="x", padx=16, pady=(4, 12))
        self.ent_zip = ctk.CTkEntry(zip_row)
        self.ent_zip.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            zip_row,
            text="Обзор",
            width=82,
            fg_color=UI.NEUTRAL,
            hover_color=UI.NEUTRAL_HOVER,
            command=self._browse,
        ).pack(side="right", padx=(8, 0))

        ctk.CTkLabel(
            card,
            text="КЭШ НАЗНАЧЕНИЯ",
            anchor="w",
            font=("Segoe UI", 10, "bold"),
            text_color=UI.MUTED,
        ).pack(fill="x", padx=16)
        self.var_cache = ctk.StringVar(value="ai")
        ctk.CTkRadioButton(
            card,
            text="Нейросети · imported_caches/ai",
            variable=self.var_cache,
            value="ai",
        ).pack(anchor="w", padx=16, pady=(6, 3))
        ctk.CTkRadioButton(
            card,
            text="Google / DeepL · imported_caches/std",
            variable=self.var_cache,
            value="std",
        ).pack(anchor="w", padx=16, pady=3)

        self.result_frame = ctk.CTkFrame(
            card,
            corner_radius=10,
            fg_color=UI.CARD_ALT_BG,
            border_width=1,
            border_color=UI.BORDER,
        )
        self.lbl_result_title = ctk.CTkLabel(
            self.result_frame,
            text="",
            anchor="w",
            font=("Segoe UI", 12, "bold"),
        )
        self.lbl_result_title.pack(fill="x", padx=12, pady=(10, 2))
        self.lbl_result_details = ctk.CTkLabel(
            self.result_frame,
            text="",
            anchor="w",
            justify="left",
            wraplength=520,
            font=("Segoe UI", 10),
            text_color=UI.MUTED,
        )
        self.lbl_result_details.pack(fill="x", padx=12, pady=(0, 10))

        self.btn_run = ctk.CTkButton(
            card,
            text="Начать миграцию",
            height=38,
            fg_color=UI.SUCCESS,
            hover_color=UI.SUCCESS_HOVER,
            command=self._run,
        )
        self.btn_run.pack(side="bottom", fill="x", padx=16, pady=16)

    def _browse(self):
        path = filedialog.askopenfilename(filetypes=[("ZIP Archives", "*.zip")])
        if path:
            self.ent_zip.delete(0, "end")
            self.ent_zip.insert(0, path)

    def _run(self):
        zip_path = self.ent_zip.get().strip()
        if not zip_path or not os.path.exists(zip_path):
            messagebox.showerror("Ошибка", "Выберите валидный ZIP-архив!")
            return

        self.btn_run.configure(state="disabled", text="Выполнение...")
        self.result_frame.pack_forget()
        cache_type = self.var_cache.get()
        finished = threading.Event()
        result = {"count": 0, "error": None}

        def poll_finished() -> None:
            if finished.is_set():
                self._show_result(
                    count=result["count"],
                    error=result["error"],
                    cache_type=cache_type,
                )
                return
            self.after(50, poll_finished)

        def task():
            try:
                count = run_migration(
                    zip_path,
                    self.mc_dir,
                    cache_type,
                    self.lang_api_code,
                    self.log_callback,
                )
                result["count"] = count
                if count > 0:
                    if cache_type == "ai":
                        self.cache_ai.load_imported_caches()
                    else:
                        self.cache_std.load_imported_caches()
            except Exception:
                result["error"] = traceback.format_exc()
            finally:
                # Worker thread deliberately touches no Tk object.
                finished.set()

        self.after(50, poll_finished)
        threading.Thread(target=task, daemon=False).start()

    def _show_result(self, *, count: int, error: str | None, cache_type: str) -> None:
        self.result_frame.pack(fill="x", padx=16, pady=(14, 0), before=self.btn_run)
        if error:
            self.lbl_result_title.configure(
                text="❌ Миграция завершилась с ошибкой",
                text_color="#ff7373",
            )
            self.lbl_result_details.configure(text=error)
        else:
            destination = "Нейросети" if cache_type == "ai" else "Google / DeepL"
            self.lbl_result_title.configure(
                text="✓ Миграция завершена",
                text_color="#79d89a",
            )
            self.lbl_result_details.configure(
                text=(
                    f"Импортировано: {count} уникальных строк\n"
                    f"Кэш назначения: {destination}\n"
                    "Детализация пропусков/конфликтов недоступна в текущем migration API."
                )
            )
        self.btn_run.configure(state="normal", text="Запустить снова")
