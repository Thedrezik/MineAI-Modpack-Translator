import customtkinter as ctk
from tkinter import filedialog

from mineai.config import ConfigManager
from mineai.constants import DEFAULT_OPENROUTER_MODEL
from mineai.engines.llm_common import load_prompts, save_prompts, get_default_prompts

class SettingsWindow(ctk.CTkToplevel):
    def __init__(self, parent, config: ConfigManager, on_saved) -> None:
        super().__init__(parent)
        self.config = config
        self.on_saved = on_saved
        self.title("⚙ Настройки MineAI")
        self.geometry("540x720")
        self.resizable(False, False)
        self.grab_set()

        tabs = ctk.CTkTabview(self)
        tabs.pack(fill="both", expand=True, padx=10, pady=10)
        tab_ai = tabs.add("Локальный ИИ")
        tab_or = tabs.add("OpenRouter")
        tab_gen = tabs.add("Общие и API")

        ctk.CTkLabel(tab_ai, text="Исполняемый файл KoboldCPP (.exe):", font=("", 12, "bold")).pack(
            anchor="w", pady=(10, 0), padx=10
        )
        self.ent_ai_exe = ctk.CTkEntry(tab_ai, width=360)
        self.ent_ai_exe.insert(0, config.get("AI", "exe_path"))
        self.ent_ai_exe.pack(fill="x", padx=10, pady=5)
        ctk.CTkButton(
            tab_ai, text="Обзор", width=80, command=lambda: self._browse(self.ent_ai_exe, [("Executables", "*.exe")])
        ).pack(anchor="e", padx=10)

        ctk.CTkLabel(tab_ai, text="Модель (.gguf):", font=("", 12, "bold")).pack(anchor="w", pady=(10, 0), padx=10)
        self.ent_ai_mod = ctk.CTkEntry(tab_ai, width=360)
        self.ent_ai_mod.insert(0, config.get("AI", "model_path"))
        self.ent_ai_mod.pack(fill="x", padx=10, pady=5)
        ctk.CTkButton(
            tab_ai, text="Обзор", width=80, command=lambda: self._browse(self.ent_ai_mod, [("GGUF Models", "*.gguf")])
        ).pack(anchor="e", padx=10)

        gpu_val = config.getint("AI", "gpu_layers", 99)
        self.lbl_gpu = ctk.CTkLabel(tab_ai, text=f"Слои GPU: {gpu_val}", font=("", 12, "bold"))
        self.lbl_gpu.pack(anchor="w", pady=(10, 0), padx=10)
        self.slider_gpu = ctk.CTkSlider(
            tab_ai,
            from_=0,
            to=99,
            number_of_steps=99,
            command=lambda v: self.lbl_gpu.configure(text=f"Слои GPU: {int(v)}"),
        )
        self.slider_gpu.set(gpu_val)
        self.slider_gpu.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(
            tab_or,
            text="Ключ: openrouter.ai/keys",
            font=("", 11),
            text_color="gray",
        ).pack(anchor="w", padx=10, pady=(10, 0))
        
        ctk.CTkLabel(tab_or, text="API URL (для Ollama, vLLM и др.):", font=("", 12, "bold")).pack(anchor="w", padx=10, pady=(10, 0))
        self.ent_or_url = ctk.CTkEntry(tab_or)
        self.ent_or_url.insert(0, config.get("OPENROUTER", "api_url"))
        self.ent_or_url.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(tab_or, text="API ключ OpenRouter:", font=("", 12, "bold")).pack(anchor="w", padx=10, pady=(5, 0))
        self.ent_or_key = ctk.CTkEntry(tab_or, show="*")
        self.ent_or_key.insert(0, config.get("OPENROUTER", "api_key"))
        self.ent_or_key.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(tab_or, text="ID модели (напр. qwen/qwen-2.5-72b-instruct):", font=("", 12, "bold")).pack(
            anchor="w", padx=10, pady=(10, 0)
        )
        self.ent_or_model = ctk.CTkEntry(tab_or)
        self.ent_or_model.insert(0, config.get("OPENROUTER", "model") or DEFAULT_OPENROUTER_MODEL)
        self.ent_or_model.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(tab_or, text="Site URL (необязательно, для статистики):", font=("", 12, "bold")).pack(
            anchor="w", padx=10, pady=(10, 0)
        )
        self.ent_or_site = ctk.CTkEntry(tab_or)
        self.ent_or_site.insert(0, config.get("OPENROUTER", "site_url"))
        self.ent_or_site.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(tab_or, text="Название приложения (X-Title):", font=("", 12, "bold")).pack(
            anchor="w", padx=10, pady=(10, 0)
        )
        self.ent_or_app = ctk.CTkEntry(tab_or)
        self.ent_or_app.insert(0, config.get("OPENROUTER", "app_name"))
        self.ent_or_app.pack(fill="x", padx=10, pady=5)

        self.var_smart = ctk.BooleanVar(value=config.getboolean("GENERAL", "smart_glue"))
        ctk.CTkSwitch(tab_gen, text="✨ Умный склейщик предложений", variable=self.var_smart).pack(
            anchor="w", padx=10, pady=15
        )

        # --- БЕЗОПАСНЫЙ БЛОК ЧТЕНИЯ НАСТРОЕК ---
        try:
            retries_val = config.getint("AI", "ai_retries")
        except Exception:
            retries_val = 3
            
        self.lbl_retries = ctk.CTkLabel(
            tab_gen, 
            text=f"Повторы ИИ при ошибке: {retries_val}" if retries_val > 0 else "Повторы ИИ при ошибке: Отключены", 
            font=("", 12, "bold")
        )
        self.lbl_retries.pack(anchor="w", padx=10)
        self.slider_retries = ctk.CTkSlider(
            tab_gen, from_=0, to=3, number_of_steps=3,
            command=lambda v: self.lbl_retries.configure(text=f"Повторы ИИ при ошибке: {int(v)}" if int(v) > 0 else "Повторы ИИ при ошибке: Отключены")
        )
        self.slider_retries.set(retries_val)
        self.slider_retries.pack(fill="x", padx=10, pady=(5, 15))

        # Здесь мы тоже убрали fallback, заменив на безопасный подход, который используется в твоем проекте
        try:
            workers = config.getint("GENERAL", "google_workers")
        except Exception:
            workers = 5
            
        ctk.CTkLabel(tab_gen, text="Потоки Google Translate:", font=("", 12, "bold")).pack(anchor="w", padx=10)
        self.slider_thr = ctk.CTkSlider(tab_gen, from_=1, to=10, number_of_steps=9)
        self.slider_thr.set(workers)
        self.slider_thr.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(tab_gen, text="API ключ DeepL:", font=("", 12, "bold")).pack(anchor="w", pady=(10, 0), padx=10)
        self.ent_deepl = ctk.CTkEntry(tab_gen, show="*")
        self.ent_deepl.insert(0, config.get("API", "deepl_key"))
        self.ent_deepl.pack(fill="x", padx=10, pady=5)
        
        ctk.CTkButton(
            self,
            text="📝 Редактор промптов ИИ",
            fg_color="#17a2b8",
            hover_color="#138496",
            command=self._open_prompt_editor,
        ).pack(fill="x", padx=20, pady=(10, 0))
        
        ctk.CTkButton(
            self,
            text="💾 Сохранить настройки",
            fg_color="#28a745",
            hover_color="#218838",
            command=self._save,
        ).pack(fill="x", padx=20, pady=10)
    
    def _open_prompt_editor(self) -> None:
        PromptEditorWindow(self)
        
    def _browse(self, entry: ctk.CTkEntry, filetypes) -> None:
        path = filedialog.askopenfilename(filetypes=filetypes)
        if path:
            entry.delete(0, "end")
            entry.insert(0, path)

    def _save(self) -> None:
        self.config.set("AI", "exe_path", self.ent_ai_exe.get())
        self.config.set("AI", "model_path", self.ent_ai_mod.get())
        self.config.set("AI", "gpu_layers", int(self.slider_gpu.get()))
        self.config.set("OPENROUTER", "api_key", self.ent_or_key.get())
        self.config.set("OPENROUTER", "api_url", self.ent_or_url.get().strip())
        self.config.set("OPENROUTER", "model", self.ent_or_model.get().strip())
        self.config.set("OPENROUTER", "site_url", self.ent_or_site.get().strip())
        self.config.set("OPENROUTER", "app_name", self.ent_or_app.get().strip())
        self.config.set("GENERAL", "smart_glue", self.var_smart.get())
        self.config.set("AI", "ai_retries", int(self.slider_retries.get()))
        self.config.set("GENERAL", "google_workers", int(self.slider_thr.get()))
        self.config.set("API", "deepl_key", self.ent_deepl.get())
        self.on_saved()
        self.destroy()

class PromptEditorWindow(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("📝 Редактор промптов ИИ")
        self.geometry("750x550")
        self.grab_set()

        self.prompts = load_prompts()

        tabs = ctk.CTkTabview(self)
        tabs.pack(fill="both", expand=True, padx=10, pady=10)
        
        self.txt_mods = self._create_tab(tabs, "mods", "Интерфейс (Моды)")
        self.txt_books = self._create_tab(tabs, "books", "Книги / Справочники")
        self.txt_quests = self._create_tab(tabs, "quests", "Квесты")
        
        # --- ВКЛАДКА "РИСКОВАННЫЙ РЕЖИМ" ---
        tab_tech = tabs.add("⚙️ Тех. правила (ОПАСНО)")
        lbl_warn = ctk.CTkLabel(
            tab_tech, 
            text="ВНИМАНИЕ: Изменение этих правил может сломать парсинг JSON и маркеров!\nИспользуйте только для экспериментов с нестандартными моделями.", 
            text_color="#e74c3c", 
            font=("", 12, "bold"),
            justify="left"
        )
        lbl_warn.pack(anchor="w", pady=(0, 5))
        self.txt_tech = ctk.CTkTextbox(tab_tech, wrap="word", font=("Consolas", 13))
        self.txt_tech.pack(fill="both", expand=True)
        self.txt_tech.insert("1.0", self.prompts.get("technical", get_default_prompts()["technical"]))

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=10, pady=10)

        ctk.CTkButton(btn_frame, text="Сбросить всё по умолчанию", fg_color="#dc3545", hover_color="#c82333", command=self._reset).pack(side="left")
        ctk.CTkButton(btn_frame, text="💾 Сохранить", fg_color="#28a745", hover_color="#218838", command=self._save).pack(side="right")

    def _create_tab(self, tabs, key, title):
        tab = tabs.add(title)
        lbl = ctk.CTkLabel(tab, text="Переменные: {lang_name} (язык), {context} (название мода/файла)", text_color="gray")
        lbl.pack(anchor="w", pady=(0, 5))
        txt = ctk.CTkTextbox(tab, wrap="word", font=("", 14))
        txt.pack(fill="both", expand=True)
        txt.insert("1.0", self.prompts.get(key, get_default_prompts()[key]))
        return txt

    def _reset(self):
        defaults = get_default_prompts()
        self.txt_mods.delete("1.0", "end")
        self.txt_mods.insert("1.0", defaults["mods"])
        self.txt_books.delete("1.0", "end")
        self.txt_books.insert("1.0", defaults["books"])
        self.txt_quests.delete("1.0", "end")
        self.txt_quests.insert("1.0", defaults["quests"])
        self.txt_tech.delete("1.0", "end")
        self.txt_tech.insert("1.0", defaults["technical"])

    def _save(self):
        self.prompts["mods"] = self.txt_mods.get("1.0", "end").strip()
        self.prompts["books"] = self.txt_books.get("1.0", "end").strip()
        self.prompts["quests"] = self.txt_quests.get("1.0", "end").strip()
        self.prompts["technical"] = self.txt_tech.get("1.0", "end").strip()
        save_prompts(self.prompts)
        self.destroy()
