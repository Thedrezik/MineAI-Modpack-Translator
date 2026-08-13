import ctypes
import sys

import customtkinter as ctk
from tkinter import filedialog, messagebox

from mineai.config import ConfigManager
from mineai.constants import DEFAULT_OPENROUTER_MODEL
from mineai.engines.llm_common import get_default_prompts, load_prompts, save_prompts
from mineai.gui.style import UI


class SettingsWindow(ctk.CTkToplevel):
    def __init__(self, parent, config: ConfigManager, on_saved) -> None:
        super().__init__(parent)
        self.config = config
        self.on_saved = on_saved
        self.title("⚙ Настройки MineAI")
        self.geometry("720x780")
        self.minsize(620, 620)
        self.resizable(True, True)
        self.configure(fg_color=UI.APP_BG)
        self.grab_set()

        tabs = ctk.CTkTabview(self, fg_color=UI.CARD_BG)
        tabs.pack(fill="both", expand=True, padx=14, pady=(14, 8))
        tab_ai = self._scroll_tab(tabs, "Локальный ИИ")
        tab_or = self._scroll_tab(tabs, "OpenRouter")
        tab_gen = self._scroll_tab(tabs, "Общие и API")

        self._field_label(tab_ai, "Исполняемый файл KoboldCPP (.exe)")
        self.ent_ai_exe = self._entry_with_browse(
            tab_ai,
            config.get("AI", "exe_path"),
            [("Executables", "*.exe")],
        )

        self._field_label(tab_ai, "Модель (.gguf)")
        self.ent_ai_mod = self._entry_with_browse(
            tab_ai,
            config.get("AI", "model_path"),
            [("GGUF Models", "*.gguf")],
        )

        gpu_val = config.getint("AI", "gpu_layers", 99)
        self.lbl_gpu = ctk.CTkLabel(
            tab_ai,
            text=f"Слои GPU: {gpu_val}",
            anchor="w",
            font=("Segoe UI", 12, "bold"),
            text_color=UI.TEXT,
        )
        self.lbl_gpu.pack(fill="x", padx=12, pady=(14, 2))
        self.slider_gpu = ctk.CTkSlider(
            tab_ai,
            from_=0,
            to=99,
            number_of_steps=99,
            command=lambda v: self.lbl_gpu.configure(text=f"Слои GPU: {int(v)}"),
        )
        self.slider_gpu.set(gpu_val)
        self.slider_gpu.pack(fill="x", padx=12, pady=(2, 10))

        ctk.CTkLabel(
            tab_or,
            text="Ключ можно создать на openrouter.ai/keys. API URL также подходит для OpenAI-compatible шлюзов.",
            anchor="w",
            justify="left",
            wraplength=570,
            font=("Segoe UI", 10),
            text_color=UI.MUTED,
        ).pack(fill="x", padx=12, pady=(10, 4))

        self._field_label(tab_or, "API URL")
        self.ent_or_url = self._plain_entry(tab_or, config.get("OPENROUTER", "api_url"))
        self._field_label(tab_or, "API ключ OpenRouter")
        self.ent_or_key = self._plain_entry(tab_or, config.get("OPENROUTER", "api_key"), show="*")
        self._field_label(tab_or, "ID модели")
        self.ent_or_model = self._plain_entry(
            tab_or,
            config.get("OPENROUTER", "model") or DEFAULT_OPENROUTER_MODEL,
        )
        self._field_label(tab_or, "Site URL (необязательно)")
        self.ent_or_site = self._plain_entry(tab_or, config.get("OPENROUTER", "site_url"))
        self._field_label(tab_or, "Название приложения (X-Title)")
        self.ent_or_app = self._plain_entry(tab_or, config.get("OPENROUTER", "app_name"))

        self.var_smart = ctk.BooleanVar(value=config.getboolean("GENERAL", "smart_glue"))
        ctk.CTkSwitch(
            tab_gen,
            text="✨ Умный склейщик предложений",
            variable=self.var_smart,
        ).pack(anchor="w", padx=12, pady=(14, 16))

        retries_val = config.getint("AI", "ai_retries", 3)
        self.lbl_retries = ctk.CTkLabel(
            tab_gen,
            text=self._retry_text(retries_val),
            anchor="w",
            font=("Segoe UI", 12, "bold"),
            text_color=UI.TEXT,
        )
        self.lbl_retries.pack(fill="x", padx=12)
        self.slider_retries = ctk.CTkSlider(
            tab_gen,
            from_=0,
            to=5,
            number_of_steps=5,
            command=lambda v: self.lbl_retries.configure(text=self._retry_text(int(v))),
        )
        self.slider_retries.set(retries_val)
        self.slider_retries.pack(fill="x", padx=12, pady=(5, 16))

        workers = config.getint("GENERAL", "google_workers", 5)
        self.lbl_workers = ctk.CTkLabel(
            tab_gen,
            text=f"Потоки Google Translate: {workers}",
            anchor="w",
            font=("Segoe UI", 12, "bold"),
            text_color=UI.TEXT,
        )
        self.lbl_workers.pack(fill="x", padx=12)
        self.slider_thr = ctk.CTkSlider(
            tab_gen,
            from_=1,
            to=10,
            number_of_steps=9,
            command=lambda v: self.lbl_workers.configure(
                text=f"Потоки Google Translate: {int(v)}"
            ),
        )
        self.slider_thr.set(workers)
        self.slider_thr.pack(fill="x", padx=12, pady=(5, 16))

        self._field_label(tab_gen, "API ключ DeepL")
        self.ent_deepl = self._plain_entry(tab_gen, config.get("API", "deepl_key"), show="*")

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=14, pady=(0, 14))
        ctk.CTkButton(
            footer,
            text="📝 Редактор промптов ИИ",
            fg_color=UI.CYAN,
            hover_color=UI.CYAN_HOVER,
            command=self._open_prompt_editor,
        ).pack(side="left", fill="x", expand=True, padx=(0, 6))
        ctk.CTkButton(
            footer,
            text="💾 Сохранить настройки",
            fg_color=UI.SUCCESS,
            hover_color=UI.SUCCESS_HOVER,
            command=self._save,
        ).pack(side="right", fill="x", expand=True, padx=(6, 0))

    @staticmethod
    def _retry_text(value: int) -> str:
        return (
            f"Повторы ИИ при ошибке: {value}"
            if value > 0
            else "Повторы ИИ при ошибке: отключены"
        )

    @staticmethod
    def _scroll_tab(tabs, title: str):
        root = tabs.add(title)
        frame = ctk.CTkScrollableFrame(root, fg_color="transparent")
        frame.pack(fill="both", expand=True)
        return frame

    @staticmethod
    def _field_label(parent, text: str) -> None:
        ctk.CTkLabel(
            parent,
            text=text,
            anchor="w",
            font=("Segoe UI", 12, "bold"),
            text_color=UI.TEXT,
        ).pack(fill="x", padx=12, pady=(12, 2))

    @staticmethod
    def _plain_entry(parent, value: str, *, show: str | None = None):
        entry = ctk.CTkEntry(parent, show=show)
        entry.insert(0, value)
        entry.pack(fill="x", padx=12, pady=(2, 4))
        return entry

    def _entry_with_browse(self, parent, value: str, filetypes):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=(2, 4))
        entry = ctk.CTkEntry(row)
        entry.insert(0, value)
        entry.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            row,
            text="Обзор",
            width=82,
            fg_color=UI.NEUTRAL,
            hover_color=UI.NEUTRAL_HOVER,
            command=lambda: self._browse(entry, filetypes),
        ).pack(side="right", padx=(8, 0))
        return entry

    def _open_prompt_editor(self) -> None:
        PromptEditorWindow(self)

    def _browse(self, entry: ctk.CTkEntry, filetypes) -> None:
        path = filedialog.askopenfilename(filetypes=filetypes)
        if path:
            entry.delete(0, "end")
            entry.insert(0, path)

    def _save(self) -> None:
        self.config.set_many(
            "AI",
            {
                "exe_path": self.ent_ai_exe.get(),
                "model_path": self.ent_ai_mod.get(),
                "gpu_layers": int(self.slider_gpu.get()),
                "ai_retries": int(self.slider_retries.get()),
            },
        )
        self.config.set_many(
            "OPENROUTER",
            {
                "api_key": self.ent_or_key.get(),
                "api_url": self.ent_or_url.get().strip(),
                "model": self.ent_or_model.get().strip(),
                "site_url": self.ent_or_site.get().strip(),
                "app_name": self.ent_or_app.get().strip(),
            },
        )
        self.config.set_many(
            "GENERAL",
            {
                "smart_glue": self.var_smart.get(),
                "google_workers": int(self.slider_thr.get()),
            },
        )
        self.config.set_many("API", {"deepl_key": self.ent_deepl.get()})
        self.on_saved()
        self.destroy()


class PromptEditorWindow(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("📝 Редактор промптов ИИ")
        self.geometry("900x650")
        self.minsize(700, 500)
        self.resizable(True, True)
        self.configure(fg_color=UI.APP_BG)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._request_close)

        self.prompts = load_prompts()
        self._dirty = False

        tabs = ctk.CTkTabview(self, fg_color=UI.CARD_BG)
        tabs.pack(fill="both", expand=True, padx=14, pady=(14, 8))
        self.txt_mods = self._create_tab(tabs, "mods", "Интерфейс (Моды)")
        self.txt_books = self._create_tab(tabs, "books", "Книги / Справочники")
        self.txt_quests = self._create_tab(tabs, "quests", "Квесты")

        tab_tech = tabs.add("⚙️ Тех. правила (ОПАСНО)")
        ctk.CTkLabel(
            tab_tech,
            text=(
                "ВНИМАНИЕ: изменение технических правил может сломать JSON и маркеры.\n"
                "Используйте этот раздел только для осознанных экспериментов."
            ),
            text_color="#ff7373",
            font=("Segoe UI", 12, "bold"),
            justify="left",
        ).pack(anchor="w", pady=(2, 5), padx=4)
        ctk.CTkLabel(
            tab_tech,
            text=(
                "{markers} — точный список [#N#] текущего запроса. Если переменная не указана, "
                "список добавляется автоматически."
            ),
            text_color=UI.MUTED,
            font=("Segoe UI", 10),
            justify="left",
            wraplength=760,
        ).pack(anchor="w", pady=(0, 6), padx=4)
        self.txt_tech = ctk.CTkTextbox(tab_tech, wrap="word", font=("Consolas", 12))
        self.txt_tech.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self.txt_tech.insert(
            "1.0",
            self.prompts.get("technical", get_default_prompts()["technical"]),
        )

        for widget in [self.txt_mods, self.txt_books, self.txt_quests, self.txt_tech]:
            self._fix_ctrl_for_textbox(widget)
            self._track_changes(widget)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=14, pady=(0, 14))
        self.lbl_dirty = ctk.CTkLabel(
            footer,
            text="Все изменения сохранены",
            text_color=UI.MUTED,
            anchor="w",
        )
        self.lbl_dirty.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            footer,
            text="Сбросить",
            width=110,
            fg_color=UI.DANGER,
            hover_color=UI.DANGER_HOVER,
            command=self._reset,
        ).pack(side="right", padx=(6, 0))
        ctk.CTkButton(
            footer,
            text="💾 Сохранить",
            width=130,
            fg_color=UI.SUCCESS,
            hover_color=UI.SUCCESS_HOVER,
            command=self._save,
        ).pack(side="right", padx=(6, 0))

    def _create_tab(self, tabs, key, title):
        tab = tabs.add(title)
        ctk.CTkLabel(
            tab,
            text="Переменные: {lang_name} (язык), {context} (название мода/файла)",
            text_color=UI.MUTED,
            anchor="w",
        ).pack(fill="x", pady=(2, 5), padx=4)
        txt = ctk.CTkTextbox(tab, wrap="word", font=("Segoe UI", 13))
        txt.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        txt.insert("1.0", self.prompts.get(key, get_default_prompts()[key]))
        return txt

    @staticmethod
    def _fix_ctrl_for_textbox(widget) -> None:
        def handler(event):
            ctrl = bool(event.state & 4)
            if sys.platform == "win32":
                try:
                    ctrl = ctrl or bool(
                        ctypes.windll.user32.GetAsyncKeyState(0x11) & 0x8000
                    )
                except Exception:
                    pass
            if not ctrl:
                return None
            actions = {67: "<<Copy>>", 86: "<<Paste>>", 65: "<<SelectAll>>", 88: "<<Cut>>"}
            if event.keycode in actions:
                event.widget.event_generate(actions[event.keycode])
                return "break"
            return None

        target = getattr(widget, "_textbox", widget)
        target.bind("<Key>", handler, add="+")

    def _track_changes(self, widget) -> None:
        target = getattr(widget, "_textbox", widget)
        try:
            target.edit_modified(False)
        except Exception:
            return

        def modified(_event=None):
            try:
                if not target.edit_modified():
                    return
                target.edit_modified(False)
            except Exception:
                return
            self._set_dirty(True)

        target.bind("<<Modified>>", modified, add="+")

    def _set_dirty(self, dirty: bool) -> None:
        self._dirty = dirty
        if not hasattr(self, "lbl_dirty"):
            return
        self.lbl_dirty.configure(
            text="● Есть несохранённые изменения" if dirty else "Все изменения сохранены",
            text_color="#f6c85f" if dirty else UI.MUTED,
        )

    def _reset(self) -> None:
        if not messagebox.askyesno(
            "Сбросить промпты?",
            "Все четыре промпта будут заменены значениями по умолчанию. Продолжить?",
            parent=self,
            icon="warning",
        ):
            return
        defaults = get_default_prompts()
        for widget, key in (
            (self.txt_mods, "mods"),
            (self.txt_books, "books"),
            (self.txt_quests, "quests"),
            (self.txt_tech, "technical"),
        ):
            widget.delete("1.0", "end")
            widget.insert("1.0", defaults[key])
        self._set_dirty(True)

    def _save(self, *, close: bool = True) -> None:
        self.prompts["mods"] = self.txt_mods.get("1.0", "end").strip()
        self.prompts["books"] = self.txt_books.get("1.0", "end").strip()
        self.prompts["quests"] = self.txt_quests.get("1.0", "end").strip()
        self.prompts["technical"] = self.txt_tech.get("1.0", "end").strip()
        save_prompts(self.prompts)
        self._set_dirty(False)
        if close:
            self.destroy()

    def _request_close(self) -> None:
        if not self._dirty:
            self.destroy()
            return
        answer = messagebox.askyesnocancel(
            "Сохранить изменения?",
            "В редакторе промптов есть несохранённые изменения.",
            parent=self,
        )
        if answer is None:
            return
        if answer:
            self._save(close=False)
        self.destroy()
