import os
import threading

import customtkinter as ctk
from tkinter import filedialog, messagebox

from mineai.constants import LANGUAGES
from mineai.processors.migration import run_migration


class MigrationWindow(ctk.CTkToplevel):
    def __init__(self, parent, mc_dir: str, lang_label: str, cache_std, cache_ai, log_callback):
        super().__init__(parent)
        self.title("📦 Миграция перевода")
        self.geometry("450x320")
        self.resizable(False, False)
        self.grab_set()

        self.mc_dir = mc_dir
        self.lang_api_code = LANGUAGES[lang_label]["api"]
        self.cache_std = cache_std
        self.cache_ai = cache_ai
        self.log_callback = log_callback

        ctk.CTkLabel(
            self,
            text="Выберите ресурс-пак с переводом (.zip):",
            font=("", 12, "bold"),
        ).pack(pady=(15, 5), padx=20, anchor="w")

        self.ent_zip = ctk.CTkEntry(self, width=300)
        self.ent_zip.pack(side="top", fill="x", padx=20, pady=5)

        ctk.CTkButton(self, text="Обзор", command=self._browse).pack(
            pady=5, padx=20, anchor="e"
        )

        ctk.CTkLabel(
            self,
            text="Для какого движка использовать этот архив?",
            font=("", 12, "bold"),
        ).pack(pady=(10, 5), padx=20, anchor="w")
        self.var_cache = ctk.StringVar(value="ai")
        ctk.CTkRadioButton(
            self,
            text="Для Нейросетей (папка imported_caches/ai)",
            variable=self.var_cache,
            value="ai",
        ).pack(anchor="w", padx=20, pady=2)
        ctk.CTkRadioButton(
            self,
            text="Для Google/DeepL (папка imported_caches/std)",
            variable=self.var_cache,
            value="std",
        ).pack(anchor="w", padx=20, pady=2)

        self.btn_run = ctk.CTkButton(
            self,
            text="Начать миграцию",
            fg_color="#28a745",
            hover_color="#218838",
            command=self._run,
        )
        self.btn_run.pack(pady=15, fill="x", padx=20)

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
        cache_type = self.var_cache.get()

        def task():
            try:
                count = run_migration(
                    zip_path,
                    self.mc_dir,
                    cache_type,
                    self.lang_api_code,
                    self.log_callback,
                )
                if count > 0:
                    if cache_type == "ai":
                        self.cache_ai.load_imported_caches()
                    else:
                        self.cache_std.load_imported_caches()
            finally:
                self.after(0, self.destroy)

        threading.Thread(target=task, daemon=True).start()
