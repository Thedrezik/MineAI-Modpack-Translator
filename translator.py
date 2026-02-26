import os
import re
import json
import time
import zipfile
import shutil
import requests
import subprocess
import threading
import configparser
import tkinter as tk
from tkinter import filedialog
import customtkinter as ctk
from concurrent.futures import ThreadPoolExecutor, as_completed

# ================= НАСТРОЙКИ (settings.ini) =================
config = configparser.ConfigParser()
settings_file = "settings.ini"

if not os.path.exists(settings_file):
    config["GENERAL"] = {
        "ai_dir": "AI", 
        "theme": "Dark", 
        "color": "green",
        "google_workers": "5"
    }
    with open(settings_file, "w", encoding="utf-8") as f:
        config.write(f)

config.read(settings_file, encoding="utf-8")
AI_DIR = config.get("GENERAL", "ai_dir", fallback="AI")
APP_THEME = config.get("GENERAL", "theme", fallback="Dark")
APP_COLOR = config.get("GENERAL", "color", fallback="green")
GOOGLE_WORKERS = config.getint("GENERAL", "google_workers", fallback=5)

ctk.set_appearance_mode(APP_THEME)
ctk.set_default_color_theme(APP_COLOR)

# Константы
CACHE_FILE = "cache.json"
KOBOLD_API = "http://localhost:5001/v1/chat/completions"

FORMAT_PATTERN = re.compile(r'(\$\([^)]+\)|§[0-9a-fk-orlmn]|\&[0-9a-fk-orlmn]|<br>|\n|%[0-9]*\$?[a-zA-Z\.])')
KEYS_TO_TRANSLATE = {"name", "title", "text", "description", "subtitle"}

IGNORE_TERMS = [
    "RF", "FE", "EU", "J", "mB", "mB/t", "RF/t", "FE/t", "AE", "kW", "kRF", "mB/tick", "ticks",
    "GUI", "UI", "HUD", "JEI", "REI", "EMI", "API", "JSON", "NBT",
    "FPS", "TPS", "HP", "XP", "MP", "XP/t", "XYZ", "RGB", "ID",
    "II", "III", "IV", "VI", "VII", "VIII", "IX", "XI", "XII"
]
IGNORE_TERMS.sort(key=len, reverse=True)
_escaped_terms = [re.escape(t) for t in IGNORE_TERMS]
IGNORE_PATTERN = re.compile(r'(?<![a-zA-Z])(' + '|'.join(_escaped_terms) + r')(?![a-zA-Z])')

LANGUAGES = {
    "Русский": {"file": "ru_ru", "api": "ru", "deepl": "RU", "name": "Russian", "regex": r'[А-Яа-яЁё]'},
    "English (UK)": {"file": "en_gb", "api": "en", "deepl": "EN-GB", "name": "English", "regex": r'[a-zA-Z]'},
    "Español": {"file": "es_es", "api": "es", "deepl": "ES", "name": "Spanish", "regex": r'[áéíóúüñÁÉÍÓÚÜÑ]'},
    "Deutsch": {"file": "de_de", "api": "de", "deepl": "DE", "name": "German", "regex": r'[äöüßÄÖÜẞ]'},
    "Français": {"file": "fr_fr", "api": "fr", "deepl": "FR", "name": "French", "regex": r'[àâæçéèêëîïôœùûüÿÀÂÆÇÉÈÊËÎÏÔŒÙÛÜŸ]'},
    "中文 (Упрощ.)": {"file": "zh_cn", "api": "zh-CN", "deepl": "ZH", "name": "Simplified Chinese", "regex": r'[\u4e00-\u9fff]'},
    "日本語": {"file": "ja_jp", "api": "ja", "deepl": "JA", "name": "Japanese", "regex": r'[\u3040-\u30ff\u4e00-\u9fff]'},
    "Português": {"file": "pt_br", "api": "pt", "deepl": "PT-BR", "name": "Portuguese", "regex": r'[ãáâéêíóôõúçÃÁÂÉÊÍÓÔÕÚÇ]'},
    "Italiano": {"file": "it_it", "api": "it", "deepl": "IT", "name": "Italian", "regex": r'[àèéìíîòóùúÀÈÉÌÍÎÒÓÙÚ]'},
    "Polski": {"file": "pl_pl", "api": "pl", "deepl": "PL", "name": "Polish", "regex": r'[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]'},
    "한국어": {"file": "ko_kr", "api": "ko", "deepl": "KO", "name": "Korean", "regex": r'[\u3131-\uD79D]'}
}

translation_cache = {}

def load_cache():
    global translation_cache
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                translation_cache = json.load(f)
        except: translation_cache = {}

def save_cache():
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(translation_cache, f, ensure_ascii=False, indent=2)

def get_mod_name(filepath):
    return os.path.basename(filepath).replace('.jar', '').split('-0')[0].split('-1')[0].replace('_', ' ').title()

def is_translation_key(text):
    t = text.strip()
    if not t or ' ' in t or '\n' in t: return False
    return bool(re.match(r'^[a-zA-Z0-9_-]+[.:][a-zA-Z0-9_.-]+$', t))

def load_lenient_json(raw_bytes):
    # Исправление кодировки UTF-8 BOM
    text = raw_bytes.decode('utf-8-sig', errors='ignore')
    # Безопасное удаление комментариев (чтобы не сломать http://)
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL) 
    text = re.sub(r'(?m)^\s*//.*$', '', text) 
    text = re.sub(r',\s*([\]}])', r'\1', text) 
    return json.loads(text, strict=False)

def extract_book_strings(data):
    strings = []
    if isinstance(data, dict):
        for k, v in data.items():
            if k in KEYS_TO_TRANSLATE and isinstance(v, str): strings.append(v)
            elif k in KEYS_TO_TRANSLATE and isinstance(v, list) and all(isinstance(i, str) for i in v): strings.extend(v)
            elif isinstance(v, (dict, list)): strings.extend(extract_book_strings(v))
    elif isinstance(data, list):
        for item in data: strings.extend(extract_book_strings(item))
    return strings

def inject_book_strings(data, t_iter):
    if isinstance(data, dict):
        for k, v in data.items():
            if k in KEYS_TO_TRANSLATE and isinstance(v, str): data[k] = next(t_iter)
            elif k in KEYS_TO_TRANSLATE and isinstance(v, list) and all(isinstance(i, str) for i in v): data[k] = [next(t_iter) for _ in v]
            elif isinstance(v, (dict, list)): inject_book_strings(v, t_iter)
    elif isinstance(data, list):
        for item in data: inject_book_strings(item, t_iter)

def is_technical_term(text):
    if not text or len(text) < 5: return True
    lower = text.lower()
    if re.match(r'^[a-z0-9_.-]+$', lower) and any(c in lower for c in '._'):
        return True
    if any(prefix in lower for prefix in [
        'glyph_', 'ritual_', 'familiar_', 'source_', 'mana_', 'spell_', 'effect_',
        'rune_', 'altar_', 'pedestal_', 'summon_', 'ritual', 'glyph'
    ]):
        return True
    return False


class TranslatorApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("MineAI Translator 4.4 (Ultimate Edition)")
        self.geometry("1150x800")
        self.resizable(False, False)
        
        if os.path.exists("icon.ico"):
            try: self.iconbitmap("icon.ico")
            except: pass
        
        self.ai_process = None
        self.mc_dir = os.getcwd()
        self.ai_model_path = ""
        self.is_running = False
        
        self.start_time = None
        self.total_strings = 0
        self.translated_strings = 0
        self.last_eta_update = 0
        
        self.auto_scroll = True
        
        load_cache()
        self.build_ui()

    def build_ui(self):
        self.frame_left = ctk.CTkScrollableFrame(self, width=370)
        self.frame_left.pack(side="left", fill="y", padx=10, pady=10)
        
        ctk.CTkLabel(self.frame_left, text="ПАПКА MINECRAFT", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(5, 5))
        self.lbl_folder = ctk.CTkLabel(self.frame_left, text=self.mc_dir[-30:], text_color="gray")
        self.lbl_folder.pack()
        ctk.CTkButton(self.frame_left, text="📁 Выбрать папку", command=self.select_folder, fg_color="#444").pack(pady=5, fill="x", padx=20)

        ctk.CTkLabel(self.frame_left, text="ЦЕЛЕВОЙ ЯЗЫК", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(15, 5))
        self.var_lang = ctk.StringVar(value="Русский")
        ctk.CTkOptionMenu(self.frame_left, variable=self.var_lang, values=list(LANGUAGES.keys())).pack(fill="x", padx=20)

        ctk.CTkLabel(self.frame_left, text="МЕТОД СОХРАНЕНИЯ", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(15, 5))
        self.var_output = ctk.StringVar(value="resourcepack")
        ctk.CTkRadioButton(self.frame_left, text="📦 Создать Resource Pack\n(Безопасно, не трогает моды)", variable=self.var_output, value="resourcepack").pack(anchor="w", padx=20, pady=5)
        ctk.CTkRadioButton(self.frame_left, text="⚠️ Перезаписать файлы\n(Изменить .jar моды напрямую)", variable=self.var_output, value="inplace").pack(anchor="w", padx=20, pady=5)

        ctk.CTkLabel(self.frame_left, text="ЧТО ПЕРЕВОДИМ?", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(15, 5))
        self.var_mods = ctk.BooleanVar(value=True)
        self.var_books = ctk.BooleanVar(value=True)
        self.var_quests = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(self.frame_left, text="Интерфейс (Моды)", variable=self.var_mods).pack(anchor="w", padx=20, pady=2)
        ctk.CTkCheckBox(self.frame_left, text="Справочники (Книги)", variable=self.var_books).pack(anchor="w", padx=20, pady=2)
        ctk.CTkCheckBox(self.frame_left, text="Квесты (FTB Quests)", variable=self.var_quests).pack(anchor="w", padx=20, pady=2)

        ctk.CTkLabel(self.frame_left, text="ДВИЖОК ПЕРЕВОДА", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(15, 5))
        self.var_engine = ctk.StringVar(value="google")
        ctk.CTkRadioButton(self.frame_left, text="Google (Быстро, ИИ-потоки)", variable=self.var_engine, value="google", command=self.update_engine_ui).pack(anchor="w", padx=20, pady=5)
        ctk.CTkRadioButton(self.frame_left, text="DeepL (API Ключ)", variable=self.var_engine, value="deepl", command=self.update_engine_ui).pack(anchor="w", padx=20, pady=5)
        ctk.CTkRadioButton(self.frame_left, text="Локальная Нейросеть (Лор)", variable=self.var_engine, value="ai", command=self.update_engine_ui).pack(anchor="w", padx=20, pady=5)

        self.frame_deepl = ctk.CTkFrame(self.frame_left, fg_color="transparent")
        self.entry_deepl_key = ctk.CTkEntry(self.frame_deepl, placeholder_text="Введите API ключ DeepL...")
        self.entry_deepl_key.pack(fill="x")

        self.frame_ai = ctk.CTkFrame(self.frame_left, fg_color="transparent")
        self.lbl_ai_model = ctk.CTkLabel(self.frame_ai, text="Модель не выбрана", text_color="yellow")
        self.lbl_ai_model.pack()
        ctk.CTkButton(self.frame_ai, text="Выбрать .gguf модель", command=self.select_model, fg_color="#555").pack(fill="x")

        ctk.CTkLabel(self.frame_left, text="РЕЖИМ ОБРАБОТКИ", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(15, 5))
        self.var_mode = ctk.StringVar(value="append")
        ctk.CTkRadioButton(self.frame_left, text="Доперевод (Сохранить старое)", variable=self.var_mode, value="append").pack(anchor="w", padx=20, pady=2)
        ctk.CTkRadioButton(self.frame_left, text="Пропуск (От 90% готовности)", variable=self.var_mode, value="skip").pack(anchor="w", padx=20, pady=2)
        ctk.CTkRadioButton(self.frame_left, text="С нуля (Полная перезапись)", variable=self.var_mode, value="force").pack(anchor="w", padx=20, pady=2)

        self.btn_analyze = ctk.CTkButton(self.frame_left, text="Анализ сборки", fg_color="#0066cc", hover_color="#004c99", command=self.start_analysis)
        self.btn_analyze.pack(pady=(20, 10), fill="x", padx=20)
        
        self.btn_start = ctk.CTkButton(self.frame_left, text="▶ НАЧАТЬ ПЕРЕВОД", fg_color="#28a745", hover_color="#218838", height=40, font=ctk.CTkFont(weight="bold"), command=self.start_translation)
        self.btn_start.pack(pady=5, fill="x", padx=20)

        self.btn_stop = ctk.CTkButton(self.frame_left, text="⏹ ОСТАНОВИТЬ", fg_color="#dc3545", hover_color="#c82333", height=40, font=ctk.CTkFont(weight="bold"), command=self.stop_process, state="disabled")
        self.btn_stop.pack(pady=(5, 10), fill="x", padx=20)

        self.frame_right = ctk.CTkFrame(self)
        self.frame_right.pack(side="right", fill="both", expand=True, padx=(0, 10), pady=10)
        
        self.textbox = ctk.CTkTextbox(self.frame_right, state="disabled", font=ctk.CTkFont(family="Consolas", size=14, weight="bold"))
        self.textbox.pack(fill="both", expand=True, padx=10, pady=10)
        
        self.textbox.tag_config("green", foreground="#2ecc71")
        self.textbox.tag_config("yellow", foreground="#f1c40f")
        self.textbox.tag_config("red", foreground="#e74c3c")
        self.textbox.tag_config("cyan", foreground="#00e5ff")
        self.textbox.tag_config("magenta", foreground="#b000ff")
        self.textbox.tag_config("dim", foreground="#888888")
        self.textbox.tag_config("white", foreground="#ffffff")
        
        self.textbox.bind("<Button-1>", self.on_user_interaction)
        self.textbox.bind("<Key>", self.on_user_interaction)
        self.textbox.bind("<MouseWheel>", self.on_user_interaction)
        
        self.progress_bar = ctk.CTkProgressBar(self.frame_right)
        self.progress_bar.pack(fill="x", padx=10, pady=(0, 5))
        self.progress_bar.set(0)
        
        self.lbl_status = ctk.CTkLabel(self.frame_right, text="Ожидание действий...", font=ctk.CTkFont(size=14))
        self.lbl_status.pack(pady=(0, 10))

        self.update_engine_ui()

    def on_user_interaction(self, event=None):
        self.auto_scroll = (self.textbox.yview()[1] >= 0.99)

    def update_engine_ui(self):
        engine = self.var_engine.get()
        self.frame_deepl.pack_forget()
        self.frame_ai.pack_forget()
        if engine == "deepl": self.frame_deepl.pack(fill="x", padx=20, pady=5)
        elif engine == "ai": self.frame_ai.pack(fill="x", padx=20, pady=5)

    def select_folder(self):
        folder = filedialog.askdirectory(title="Выберите папку профиля Minecraft")
        if folder:
            self.mc_dir = folder
            self.lbl_folder.configure(text=f"...{folder[-25:]}" if len(folder) > 25 else folder)

    def select_model(self):
        file = filedialog.askopenfilename(title="Выберите модель ИИ", filetypes=[("GGUF Models", "*.gguf")])
        if file:
            self.ai_model_path = file
            self.lbl_ai_model.configure(text=os.path.basename(file), text_color="green")

    def log_colored(self, message, color_tag="white"):
        self.textbox.configure(state="normal")
        at_bottom = self.textbox.yview()[1] >= 0.99
        self.textbox.insert("end", message + "\n", color_tag)
        if self.auto_scroll or at_bottom:
            self.textbox.see("end")
        self.textbox.configure(state="disabled")

    def log_table_row(self, icon, name, m_type, trans_c, en_c, pct):
        color = "green" if pct >= 90 else ("yellow" if pct >= 50 else "red")
        name_str = f"{icon} {name[:34]:<35}"
        type_str = f"[{m_type}]".ljust(15)
        count_str = f"{trans_c}/{en_c}".ljust(12)
        pct_str = f"{pct}%"

        self.textbox.configure(state="normal")
        at_bottom = self.textbox.yview()[1] >= 0.99
        self.textbox.insert("end", name_str, "cyan")
        self.textbox.insert("end", type_str, "magenta")
        self.textbox.insert("end", count_str, "white")
        self.textbox.insert("end", pct_str + "\n", color)
        if self.auto_scroll or at_bottom:
            self.textbox.see("end")
        self.textbox.configure(state="disabled")

    def set_status(self, text, val=None):
        if val is not None:
            self.progress_bar.set(val)
        self.lbl_status.configure(text=text)

    def update_eta(self):
        if not self.start_time or self.translated_strings == 0:
            return "расчёт ETA..."
        elapsed = time.time() - self.start_time
        if elapsed < 5: return "расчёт ETA..."
        speed = self.translated_strings / elapsed
        remaining = self.total_strings - self.translated_strings
        if remaining <= 0: return "готово"
        eta_seconds = remaining / speed
        if eta_seconds < 60:
            return f"≈ {int(eta_seconds)} сек"
        elif eta_seconds < 3600:
            return f"≈ {int(eta_seconds//60)} мин {int(eta_seconds%60)} сек"
        else:
            return f"≈ {int(eta_seconds//3600)} ч {int((eta_seconds%3600)//60)} мин"

    def lock_ui(self, lock=True):
        self.btn_analyze.configure(state="disabled" if lock else "normal")
        self.btn_start.configure(state="disabled" if lock else "normal")
        self.btn_stop.configure(state="normal" if lock else "disabled")

    def stop_process(self):
        self.is_running = False
        self.set_status("Остановка процесса... Пожалуйста, подождите.", 1.0)
        self.btn_stop.configure(state="disabled")

    # ================= ЛОГИКА АНАЛИЗА =================
    def start_analysis(self):
        self.lock_ui(True)
        self.is_running = True
        self.textbox.configure(state="normal")
        self.textbox.delete("1.0", "end")
        self.textbox.configure(state="disabled")
        threading.Thread(target=self.run_analysis, daemon=True).start()

    def run_analysis(self):
        lang_settings = LANGUAGES[self.var_lang.get()]
        target_file = f"{lang_settings['file']}.json"
        l_regex = lang_settings.get('regex', r'[А-Яа-яЁё]')
        
        mods_dir = os.path.join(self.mc_dir, "mods")
        quests_dir = os.path.join(self.mc_dir, "config", "ftbquests", "quests")

        self.log_colored(f"🚀 Сканирование сборки (Язык: {lang_settings['name']})...\n", "yellow")
        header = f"{'ФАЙЛ / МОД':<37}{'ТИП':<15}{'СТРОКИ':<12}ПРОГРЕСС"
        self.log_colored(header, "white")
        self.log_colored("-" * 75, "dim")
        
        total_en, total_trans = 0, 0
        
        jar_files = []
        if os.path.exists(mods_dir) and (self.var_mods.get() or self.var_books.get()):
            jar_files = [os.path.join(mods_dir, f) for f in os.listdir(mods_dir) if f.endswith('.jar')]

        for i, filepath in enumerate(jar_files):
            if not self.is_running: break
            mod_name = get_mod_name(filepath)
            self.set_status(f"Анализ мода: {mod_name}...", i / (len(jar_files) + 1))
            try:
                with zipfile.ZipFile(filepath, 'r') as zin:
                    trans_files = {item.filename.lower(): item for item in zin.infolist() if target_file in item.filename.lower() or f"/{lang_settings['file']}/" in item.filename.lower()}
                    
                    if self.var_mods.get():
                        for item in zin.infolist():
                            if item.filename.lower().endswith('en_us.json') and 'patchouli' not in item.filename.lower() and 'lexicon' not in item.filename.lower():
                                try:
                                    en_data = load_lenient_json(zin.read(item))
                                    trans_t = item.filename.lower().replace('en_us.json', target_file)
                                    trans_data = load_lenient_json(zin.read(trans_files[trans_t])) if trans_t in trans_files else {}
                                    en_c = len([k for k, v in en_data.items() if isinstance(v, str) and re.search(r'[a-zA-Z]', v)])
                                    trans_c = sum(1 for k, v in en_data.items() if isinstance(v, str) and re.search(r'[a-zA-Z]', v) and (str(trans_data.get(k,"")) != v and str(trans_data.get(k,"")).strip() != ""))
                                    if en_c > 0:
                                        total_en += en_c; total_trans += trans_c
                                        self.log_table_row("📦", mod_name, "Интерфейс", trans_c, en_c, int(trans_c/en_c*100))
                                except: pass

                    if self.var_books.get():
                        for item in zin.infolist():
                            f_lower = item.filename.lower()
                            if '/en_us/' in f_lower and f_lower.endswith('.json') and any(x in f_lower for x in ('patchouli', 'lexicon', 'guide')):
                                try:
                                    en_data = load_lenient_json(zin.read(item))
                                    trans_t = f_lower.replace('/en_us/', f"/{lang_settings['file']}/")
                                    trans_data = load_lenient_json(zin.read(trans_files[trans_t])) if trans_t in trans_files else {}
                                    en_strings = [s for s in extract_book_strings(en_data) if s.strip() and re.search(r'[a-zA-Z]', s)]
                                    trans_strings = [s for s in extract_book_strings(trans_data) if s.strip()] if trans_data else []
                                    en_c = len(en_strings)
                                    trans_c = sum(1 for idx, s in enumerate(en_strings) if idx < len(trans_strings) and trans_strings[idx] != s)
                                    if en_c > 0:
                                        total_en += en_c; total_trans += trans_c
                                        self.log_table_row("📖", mod_name, "Книга", trans_c, en_c, int(trans_c/en_c*100))
                                except: pass
            except: pass

        snbt_files = []
        if os.path.exists(quests_dir) and self.var_quests.get():
            for root, _, files in os.walk(quests_dir):
                snbt_files.extend([os.path.join(root, f) for f in files if f.endswith('.snbt')])
                
        for i, filepath in enumerate(snbt_files):
            if not self.is_running: break
            self.set_status(f"Анализ квеста: {os.path.basename(filepath)}...", (len(jar_files) + i) / (len(jar_files) + len(snbt_files)))
            try:
                with open(filepath, 'r', encoding='utf-8') as f: content = f.read()
                strings = re.findall(r'(?:"|)(?:title|subtitle|text)(?:"|)\s*:\s*"((?:[^"\\]|\\.)*)"', content, re.IGNORECASE)
                desc_blocks = re.findall(r'(?:"|)description(?:"|)\s*:\s*\[(.*?)\]', content, re.DOTALL | re.IGNORECASE)
                for b in desc_blocks: strings.extend(re.findall(r'"((?:[^"\\]|\\.)*)"', b))
                valid_str = list(set([s for s in strings if s.strip() and not is_translation_key(s) and re.search(r'[a-zA-Z]', s)]))
                en_c = len(valid_str)
                trans_c = sum(1 for s in valid_str if re.search(l_regex, s))
                if en_c > 0:
                    total_en += en_c; total_trans += trans_c
                    self.log_table_row("📜", os.path.basename(filepath), "Квесты", trans_c, en_c, int(trans_c/en_c*100))
            except: pass

        self.log_colored("-" * 75, "dim")
        if not self.is_running:
            self.log_colored("🛑 АНАЛИЗ ПРЕРВАН ПОЛЬЗОВАТЕЛЕМ", "red")
        elif total_en > 0:
            pct = int((total_trans / total_en) * 100)
            c_color = "green" if pct >= 90 else ("yellow" if pct >= 50 else "red")
            self.log_colored(f"✅ АНАЛИЗ ЗАВЕРШЕН!", c_color)
            self.log_colored(f"Общая готовность: {pct}% | Всего строк: {total_en}", c_color)
        else:
            self.log_colored("❌ Не найдено файлов для перевода!", "red")
            
        self.set_status("Готово", 1.0)
        self.lock_ui(False)

    # ================= ЛОГИКА ПЕРЕВОДА =================
    def start_translation(self):
        self.lock_ui(True)
        self.is_running = True
        self.textbox.configure(state="normal")
        self.textbox.delete("1.0", "end")
        self.textbox.configure(state="disabled")
        threading.Thread(target=self.run_translation, daemon=True).start()

    def estimate_total_strings(self, jar_files, snbt_files, lang_settings, mode_overwrite):
        total = 0
        target_file = f"{lang_settings['file']}.json"
        l_regex = lang_settings.get('regex', r'[А-Яа-яЁё]')

        for filepath in jar_files:
            if not self.is_running: return total
            try:
                with zipfile.ZipFile(filepath, 'r') as zin:
                    trans_files = {item.filename.lower(): item for item in zin.infolist() 
                                   if target_file in item.filename.lower() or f"/{lang_settings['file']}/" in item.filename.lower()}
                    for item in zin.infolist():
                        f_lower = item.filename.lower()
                        is_book = ('/en_us/' in f_lower and f_lower.endswith('.json') and any(x in f_lower for x in ('patchouli', 'lexicon', 'guide')))
                        is_lang = (f_lower.endswith('en_us.json') and not is_book)

                        if self.var_mods.get() and is_lang:
                            en_data = load_lenient_json(zin.read(item))
                            trans_data = load_lenient_json(zin.read(trans_files.get(f_lower.replace('en_us.json', target_file), None))) if f_lower.replace('en_us.json', target_file) in trans_files else {}
                            for k, v in en_data.items():
                                if isinstance(v, str) and re.search(r'[a-zA-Z]', v) and not is_technical_term(v):
                                    if mode_overwrite == "force" or not (k in trans_data and trans_data[k].strip()):
                                        total += 1

                        elif self.var_books.get() and is_book:
                            en_data = load_lenient_json(zin.read(item))
                            en_strings = [s for s in extract_book_strings(en_data) if s.strip() and re.search(r'[a-zA-Z]', s) and not is_technical_term(s)]
                            total += len(en_strings)
            except: pass

        for filepath in snbt_files:
            if not self.is_running: return total
            try:
                with open(filepath, 'r', encoding='utf-8') as f: content = f.read()
                strings = re.findall(r'(?:"|)(?:title|subtitle|text)(?:"|)\s*:\s*"((?:[^"\\]|\\.)*)"', content, re.IGNORECASE)
                desc_blocks = re.findall(r'(?:"|)description(?:"|)\s*:\s*\[(.*?)\]', content, re.DOTALL | re.IGNORECASE)
                for b in desc_blocks: strings.extend(re.findall(r'"((?:[^"\\]|\\.)*)"', b))
                valid = [s for s in strings if s.strip() and not is_translation_key(s) and re.search(r'[a-zA-Z]', s)]
                if mode_overwrite == "force":
                    total += len(valid)
                else:
                    total += sum(1 for s in valid if not re.search(l_regex, s))
            except: pass
        return total

    def run_translation(self):
        lang_settings = LANGUAGES[self.var_lang.get()]
        engine = self.var_engine.get()
        mode_overwrite = self.var_mode.get()
        output_mode = self.var_output.get()
        
        mods_dir = os.path.join(self.mc_dir, "mods")
        quests_dir = os.path.join(self.mc_dir, "config", "ftbquests", "quests")
        rp_dir = os.path.join(self.mc_dir, "resourcepacks")

        if engine == "deepl" and not self.entry_deepl_key.get().strip():
            self.log_colored("❌ Ошибка: Введите API ключ для DeepL!", "red")
            self.lock_ui(False); return
        if engine == "ai" and not self.ai_model_path:
            self.log_colored("❌ Ошибка: Выберите файл модели .gguf!", "red")
            self.lock_ui(False); return

        jar_files = []
        if os.path.exists(mods_dir) and (self.var_mods.get() or self.var_books.get()):
            jar_files = [os.path.join(mods_dir, f) for f in os.listdir(mods_dir) if f.endswith('.jar')]
        snbt_files = []
        if self.var_quests.get() and os.path.exists(quests_dir):
            for root, _, files in os.walk(quests_dir):
                snbt_files.extend([os.path.join(root, f) for f in files if f.endswith('.snbt')])

        total_files = len(jar_files) + len(snbt_files)
        if total_files == 0:
            self.log_colored("❌ Нечего переводить!", "red")
            self.lock_ui(False); return

        self.log_colored("📊 Подсчёт строк для точного ETA...", "yellow")
        self.total_strings = self.estimate_total_strings(jar_files, snbt_files, lang_settings, mode_overwrite)
        self.log_colored(f"   Найдено строк для перевода: {self.total_strings}", "cyan")

        if engine == "ai" and not self.setup_and_start_ai():
            self.lock_ui(False); return

        rp_zip_path = None
        if output_mode == "resourcepack":
            if not os.path.exists(rp_dir): os.makedirs(rp_dir)
            rp_zip_path = os.path.join(rp_dir, f"MineAI_{lang_settings['name']}_Pack.zip")
            
            if os.path.exists(rp_zip_path):
                self.log_colored("📦 Найден старый ресурспак — сохраняем все предыдущие переводы...", "yellow")
                backup = rp_zip_path + ".backup"
                if os.path.exists(backup): os.remove(backup)
                shutil.copy2(rp_zip_path, backup)
                
                with zipfile.ZipFile(rp_zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as new_z:
                    with zipfile.ZipFile(backup, 'r') as old_z:
                        for item in old_z.infolist():
                            new_z.writestr(item, old_z.read(item))
                    mcmeta = {"pack": {"pack_format": 15, "description": f"Auto-translated by MineAI ({lang_settings['name']}) — Updated {time.strftime('%Y-%m-%d')}" }}
                    new_z.writestr("pack.mcmeta", json.dumps(mcmeta, indent=2))
                self.log_colored("   Все старые переводы успешно перенесены!", "green")
            else:
                with zipfile.ZipFile(rp_zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as zout:
                    mcmeta = {"pack": {"pack_format": 15, "description": f"Auto-translated by MineAI ({lang_settings['name']})"}}
                    zout.writestr("pack.mcmeta", json.dumps(mcmeta, indent=2))
            self.log_colored(f"📦 Умный ресурспак: {rp_zip_path}", "cyan")

        self.log_colored(f"🚀 ЗАПУСК ПЕРЕВОДА ({lang_settings['name']})...\n", "yellow")
        
        self.start_time = time.time()
        self.translated_strings = 0
        self.last_eta_update = time.time()
        self.auto_scroll = True

        processed = 0
        for filepath in jar_files:
            if not self.is_running: break
            self.process_jar(filepath, engine, mode_overwrite, output_mode, lang_settings, rp_zip_path)
            processed += 1
            self.set_status(f"Обработано модов: {processed}/{len(jar_files)} | {self.update_eta()}", processed / total_files)
            
        for filepath in snbt_files:
            if not self.is_running: break
            self.process_snbt(filepath, engine, mode_overwrite, lang_settings)
            processed += 1
            self.set_status(f"Обработано квестов: {processed}/{total_files} | {self.update_eta()}", processed / total_files)

        save_cache()
        if not self.is_running:
            self.log_colored("\n🛑 ПРОЦЕСС ОСТАНОВЛЕН. Кэш сохранен.", "red")
        else:
            self.log_colored("\n✅ ГЛОБАЛЬНЫЙ ПЕРЕВОД УСПЕШНО ЗАВЕРШЕН!", "green")
            if output_mode == "resourcepack":
                self.log_colored("💡 Зайдите в настройки игры и включите созданный ресурспак!", "yellow")
                if len(snbt_files) > 0:
                    self.log_colored("📜 Квесты сохранены напрямую в config.", "dim")
        
        self.set_status("Все задачи выполнены!" if self.is_running else "Остановлено", 1.0)
        if self.ai_process: self.ai_process.terminate()
        self.lock_ui(False)

    def setup_and_start_ai(self):
        try:
            if requests.get(KOBOLD_API.replace("chat/completions", "models"), timeout=1).status_code == 200:
                self.log_colored("✅ Сервер ИИ уже работает", "green")
                return True
        except: pass

        self.log_colored(f"🤖 Запуск ИИ: {os.path.basename(self.ai_model_path)}...", "cyan")
        kobold_exe = os.path.join(AI_DIR, "koboldcpp.exe") if os.path.exists(os.path.join(AI_DIR, "koboldcpp.exe")) else "koboldcpp"
        try:
            self.ai_process = subprocess.Popen([kobold_exe, self.ai_model_path, "--port", "5001", "--quiet"], stdout=subprocess.DEVNULL)
        except Exception as e:
            self.log_colored(f"❌ Ошибка запуска ИИ: {e}", "red")
            return False
            
        for i in range(60):
            if not self.is_running: return False
            self.set_status(f"Прогрев нейросети... ({i}/60 сек)")
            try:
                if requests.get(KOBOLD_API.replace("chat/completions", "models"), timeout=1).status_code == 200:
                    self.log_colored("✅ ИИ успешно запущен!\n", "green")
                    return True
            except: time.sleep(1)
        self.log_colored("❌ Сервер ИИ не отвечает", "red")
        return False

    def translate_engine(self, data_dict, engine, lang_settings):
        keys = list(data_dict.keys())
        result = {}
        to_translate = {}
        
        for k in keys:
            if not self.is_running: break
            text = data_dict[k]
            cache_key = f"{lang_settings['api']}_{text}"
            if cache_key in translation_cache:
                result[k] = translation_cache[cache_key]
                continue
                
            mapping = {}
            def mask_format(m):
                marker = f" [#{len(mapping)}#] "
                mapping[marker.strip()] = m.group(0)
                return marker
                
            masked = FORMAT_PATTERN.sub(mask_format, text)
            masked = IGNORE_PATTERN.sub(mask_format, masked)
            masked = re.sub(r'\s+', ' ', masked).strip()
            
            if not masked:
                result[k] = text
                continue
                
            to_translate[k] = {"original": text, "masked": masked, "mapping": mapping}

        if not to_translate or not self.is_running: return result

        if engine == "google":
            chunks = []
            curr_keys, curr_text = [], ""
            for k, val in to_translate.items():
                if len(curr_text) + len(val["masked"]) > 2000 or len(curr_keys) >= 20:
                    chunks.append((curr_keys, curr_text))
                    curr_keys, curr_text = [k], val["masked"]
                else:
                    curr_keys.append(k)
                    curr_text = curr_text + " |~| " + val["masked"] if curr_text else val["masked"]
            if curr_keys: chunks.append((curr_keys, curr_text))

            def translate_chunk(chunk_keys, text_to_send):
                for _ in range(3):
                    if not self.is_running: return chunk_keys, None
                    try:
                        res = requests.get("https://translate.googleapis.com/translate_a/single", params={"client": "gtx", "sl": "en", "tl": lang_settings['api'], "dt": "t", "q": text_to_send}, timeout=10)
                        if res.status_code == 429: time.sleep(3); continue
                        parts = re.split(r'\s*\|\s*~\s*\|\s*', "".join([p[0] for p in res.json()[0] if p[0]]))
                        if len(parts) == len(chunk_keys): return chunk_keys, parts
                    except: time.sleep(1)
                return chunk_keys, None

            with ThreadPoolExecutor(max_workers=GOOGLE_WORKERS) as executor:
                futures = [executor.submit(translate_chunk, ck, txt) for ck, txt in chunks]
                for future in as_completed(futures):
                    if not self.is_running: break
                    c_keys, c_parts = future.result()
                    if c_parts:
                        for idx, k in enumerate(c_keys):
                            trans = c_parts[idx].strip()
                            for m_idx, (m, orig) in enumerate(to_translate[k]["mapping"].items()):
                                trans = re.sub(rf'\[\s*#\s*{m_idx}\s*#\s*\]', lambda x, o=orig: o, trans)
                            result[k] = trans
                            translation_cache[f"{lang_settings['api']}_{to_translate[k]['original']}"] = trans 
                            self.translated_strings += 1
                            if time.time() - self.last_eta_update > 2:
                                self.set_status(f"Перевод строк: {self.translated_strings}/{self.total_strings} | {self.update_eta()}")
                                self.last_eta_update = time.time()
                            self.log_colored(f" > {to_translate[k]['original'][:40]} -> {trans[:40]}", "dim")
                    else:
                        for k in c_keys:
                            if not self.is_running: break
                            try:
                                res = requests.get("https://translate.googleapis.com/translate_a/single", params={"client": "gtx", "sl": "en", "tl": lang_settings['api'], "dt": "t", "q": to_translate[k]["masked"]}, timeout=5).json()
                                trans = "".join([p[0] for p in res[0] if p[0]])
                                for m_idx, (m, orig) in enumerate(to_translate[k]["mapping"].items()):
                                    trans = re.sub(rf'\[\s*#\s*{m_idx}\s*#\s*\]', lambda x, o=orig: o, trans)
                                result[k] = trans
                                translation_cache[f"{lang_settings['api']}_{to_translate[k]['original']}"] = trans
                                self.translated_strings += 1
                                if time.time() - self.last_eta_update > 2:
                                    self.set_status(f"Перевод строк: {self.translated_strings}/{self.total_strings} | {self.update_eta()}")
                                    self.last_eta_update = time.time()
                                self.log_colored(f" > {to_translate[k]['original'][:40]} -> {trans[:40]}", "dim")
                            except: result[k] = to_translate[k]["original"]
                            time.sleep(0.3)
                            
        elif engine == "deepl":
            api_key = self.entry_deepl_key.get().strip()
            url = "https://api.deepl.com/v2/translate" if not api_key.endswith(":fx") else "https://api-free.deepl.com/v2/translate"
            b_keys = list(to_translate.keys())
            for i in range(0, len(b_keys), 40):
                if not self.is_running: break
                chunk_keys = b_keys[i:i+40]
                texts = [to_translate[k]["masked"] for k in chunk_keys]
                try:
                    res = requests.post(url, headers={"Authorization": f"DeepL-Auth-Key {api_key}"}, json={"text": texts, "target_lang": lang_settings['deepl']}).json()
                    for idx, k in enumerate(chunk_keys):
                        trans = res["translations"][idx]["text"]
                        for m_idx, (m, orig) in enumerate(to_translate[k]["mapping"].items()):
                            trans = re.sub(rf'\[\s*#\s*{m_idx}\s*#\s*\]', lambda x, o=orig: o, trans)
                        result[k] = trans
                        translation_cache[f"{lang_settings['api']}_{to_translate[k]['original']}"] = trans
                        self.translated_strings += 1
                        if time.time() - self.last_eta_update > 2:
                            self.set_status(f"Перевод строк: {self.translated_strings}/{self.total_strings} | {self.update_eta()}")
                            self.last_eta_update = time.time()
                        self.log_colored(f" > {to_translate[k]['original'][:40]} -> {trans[:40]}", "dim")
                except Exception as e:
                    self.log_colored(f"❌ Ошибка DeepL: {e}", "red")
                    for k in chunk_keys: result[k] = to_translate[k]["original"]
                time.sleep(0.5)

        else:  # AI
            batch_keys = list(to_translate.keys())
            for i in range(0, len(batch_keys), 20):
                if not self.is_running: break
                b_keys = batch_keys[i:i+20]
                b_dict = {k: to_translate[k]["masked"] for k in b_keys}
                prompt = f"Translate the following JSON string values from English to {lang_settings['name']}. RULES: Do not translate keys. Preserve [#0#] tags exactly. Return ONLY valid JSON. Text: {json.dumps(b_dict, ensure_ascii=False)}"
                try:
                    res = requests.post(KOBOLD_API, json={"messages": [{"role": "user", "content": prompt}], "temperature": 0.1, "max_tokens": 2048}, timeout=120).json()
                    trans_text = re.sub(r'^```json\s*|^```\s*|```$', '', res['choices'][0]['message']['content'].strip(), flags=re.IGNORECASE).strip()
                    trans_dict = json.loads(trans_text, strict=False)
                    for k in b_keys:
                        if k in trans_dict:
                            trans = trans_dict[k]
                            for m_idx, (m, orig) in enumerate(to_translate[k]["mapping"].items()):
                                trans = re.sub(rf'\[\s*#\s*{m_idx}\s*#\s*\]', lambda x, o=orig: o, trans)
                            result[k] = trans
                            translation_cache[f"{lang_settings['api']}_{to_translate[k]['original']}"] = trans
                            self.translated_strings += 1
                            if time.time() - self.last_eta_update > 2:
                                self.set_status(f"Перевод строк: {self.translated_strings}/{self.total_strings} | {self.update_eta()}")
                                self.last_eta_update = time.time()
                            self.log_colored(f" > {to_translate[k]['original'][:40]} -> {trans[:40]}", "dim")
                        else: result[k] = to_translate[k]["original"]
                except:
                    for k in b_keys: result[k] = to_translate[k]["original"]

        if len(translation_cache) % 50 == 0: save_cache()
        return result

    def process_jar(self, filepath, engine, mode_overwrite, output_mode, lang_settings, rp_zip_path):
        mod_name = get_mod_name(filepath)
        target_file = f"{lang_settings['file']}.json"
        temp_filepath = filepath + ".temp"
        translated_any = False
        
        try:
            with zipfile.ZipFile(filepath, 'r') as zin:
                zout = zipfile.ZipFile(temp_filepath, 'w', compression=zipfile.ZIP_DEFLATED) if output_mode == "inplace" else None
                try:
                    ru_files_written = set()
                    trans_files = {item.filename.lower(): item for item in zin.infolist() if target_file in item.filename.lower() or f"/{lang_settings['file']}/" in item.filename.lower()}

                    for item in zin.infolist():
                        if not self.is_running: break
                        f_lower = item.filename.lower()
                        is_book = ('/en_us/' in f_lower and f_lower.endswith('.json') and any(x in f_lower for x in ('patchouli', 'lexicon', 'guide')))
                        is_lang = (f_lower.endswith('en_us.json') and not is_book)
                        
                        if output_mode == "inplace" and target_file not in f_lower and f"/{lang_settings['file']}/" not in f_lower:
                            zout.writestr(item, zin.read(item))

                        if self.var_mods.get() and is_lang:
                            trans_filename = re.sub(r'en_us\.json$', target_file, item.filename, flags=re.IGNORECASE)
                            trans_t = trans_filename.lower()
                            
                            try:
                                en_data = load_lenient_json(zin.read(item))
                            except Exception as e:
                                self.log_colored(f"⚠️ Пропуск файла {item.filename} (ОШИБКА АВТОРА МОДА: сломанный синтаксис JSON)", "yellow")
                                continue
                                
                            try:
                                trans_data = load_lenient_json(zin.read(trans_files[trans_t])) if trans_t in trans_files else {}
                            except:
                                trans_data = {}
                            
                            final_data = en_data.copy()
                            keys_to_translate = {}
                            
                            for k, en_text in en_data.items():
                                if not isinstance(en_text, str) or not en_text.strip(): continue
                                if is_technical_term(en_text):
                                    final_data[k] = en_text
                                    continue
                                if mode_overwrite == "append" and k in trans_data and isinstance(trans_data[k], str) and trans_data[k].strip():
                                    final_data[k] = trans_data[k]
                                    if final_data[k] == en_text and re.search(r'[a-zA-Z]', en_text): keys_to_translate[k] = en_text
                                elif re.search(r'[a-zA-Z]', en_text): keys_to_translate[k] = en_text

                            total_en = len([k for k, v in en_data.items() if isinstance(v, str) and re.search(r'[a-zA-Z]', v) and not is_technical_term(v)])
                            if total_en > 0:
                                if mode_overwrite == "skip" and (total_en - len(keys_to_translate)) >= total_en * 0.9:
                                    self.log_colored(f"⏩ {mod_name} [Интерфейс]: Пропуск", "yellow")
                                    if output_mode == "resourcepack" and trans_t in trans_files:
                                        with zipfile.ZipFile(rp_zip_path, 'a', compression=zipfile.ZIP_DEFLATED) as rz:
                                            rz.writestr(trans_filename, zin.read(trans_files[trans_t]))
                                elif len(keys_to_translate) == 0 and mode_overwrite == "append":
                                    if output_mode == "resourcepack":
                                        with zipfile.ZipFile(rp_zip_path, 'a', compression=zipfile.ZIP_DEFLATED) as rz:
                                            rz.writestr(trans_filename, json.dumps(final_data, ensure_ascii=False, indent=2).encode('utf-8'))
                                    translated_any = True
                                else:
                                    self.log_colored(f"⚡ Перевод {mod_name} [Интерфейс] - {len(keys_to_translate)} строк", "cyan")
                                    trans_dict = self.translate_engine(keys_to_translate, engine, lang_settings)
                                    for k, v in trans_dict.items(): final_data[k] = v
                                    out_data = json.dumps(final_data, ensure_ascii=False, indent=2).encode('utf-8')
                                    if output_mode == "resourcepack":
                                        with zipfile.ZipFile(rp_zip_path, 'a', compression=zipfile.ZIP_DEFLATED) as rz:
                                            rz.writestr(trans_filename, out_data)
                                    else:
                                        zout.writestr(trans_filename, out_data)
                                        ru_files_written.add(trans_filename)
                                    translated_any = True

                        elif self.var_books.get() and is_book:
                            trans_filename = re.sub(r'/en_us/', f"/{lang_settings['file']}/", item.filename, flags=re.IGNORECASE)
                            trans_t = trans_filename.lower()
                            
                            try:
                                en_data = load_lenient_json(zin.read(item))
                            except Exception as e:
                                self.log_colored(f"⚠️ Пропуск книги {item.filename} (ОШИБКА АВТОРА МОДА: сломанный синтаксис JSON)", "yellow")
                                continue
                                
                            try:
                                trans_data = load_lenient_json(zin.read(trans_files[trans_t])) if trans_t in trans_files else {}
                            except:
                                trans_data = {}
                            
                            en_strings = [s for s in extract_book_strings(en_data) if s.strip()]
                            trans_strings = [s for s in extract_book_strings(trans_data) if s.strip()] if trans_data else []
                            
                            keys_to_translate = {}
                            final_strings = []
                            
                            for i, en_s in enumerate(en_strings):
                                if is_technical_term(en_s):
                                    final_strings.append(en_s)
                                    continue
                                if mode_overwrite == "append" and i < len(trans_strings) and trans_strings[i].strip():
                                    final_strings.append(trans_strings[i])
                                    if trans_strings[i] == en_s and re.search(r'[a-zA-Z]', en_s): keys_to_translate[str(i)] = en_s
                                else:
                                    final_strings.append(en_s)
                                    if re.search(r'[a-zA-Z]', en_s): keys_to_translate[str(i)] = en_s

                            total_en = len([s for s in en_strings if re.search(r'[a-zA-Z]', s) and not is_technical_term(s)])
                            if total_en > 0:
                                if mode_overwrite == "skip" and (total_en - len(keys_to_translate)) >= total_en * 0.9:
                                    self.log_colored(f"⏩ {mod_name} [Книга]: Пропуск", "yellow")
                                    if output_mode == "resourcepack" and trans_t in trans_files:
                                        with zipfile.ZipFile(rp_zip_path, 'a', compression=zipfile.ZIP_DEFLATED) as rz:
                                            rz.writestr(trans_filename, zin.read(trans_files[trans_t]))
                                elif len(keys_to_translate) == 0 and mode_overwrite == "append":
                                    if output_mode == "resourcepack":
                                        inject_book_strings(en_data, iter(final_strings))
                                        with zipfile.ZipFile(rp_zip_path, 'a', compression=zipfile.ZIP_DEFLATED) as rz:
                                            rz.writestr(trans_filename, json.dumps(en_data, ensure_ascii=False, indent=2).encode('utf-8'))
                                    translated_any = True
                                else:
                                    self.log_colored(f"⚡ Перевод {mod_name} [Книга] - {len(keys_to_translate)} строк", "magenta")
                                    trans_dict = self.translate_engine(keys_to_translate, engine, lang_settings)
                                    for i in range(len(final_strings)):
                                        if str(i) in trans_dict: final_strings[i] = trans_dict[str(i)]
                                    inject_book_strings(en_data, iter(final_strings))
                                    out_data = json.dumps(en_data, ensure_ascii=False, indent=2).encode('utf-8')
                                    if output_mode == "resourcepack":
                                        with zipfile.ZipFile(rp_zip_path, 'a', compression=zipfile.ZIP_DEFLATED) as rz:
                                            rz.writestr(trans_filename, out_data)
                                    else:
                                        zout.writestr(trans_filename, out_data)
                                        ru_files_written.add(trans_filename)
                                    translated_any = True

                    if output_mode == "inplace":
                        for item in zin.infolist():
                            if (target_file in item.filename.lower() or f"/{lang_settings['file']}/" in item.filename.lower()) and item.filename not in ru_files_written:
                                try: zout.writestr(item, zin.read(item))
                                except: pass
                finally:
                    if zout: zout.close()

            if output_mode == "inplace":
                if translated_any and self.is_running: shutil.move(temp_filepath, filepath)
                else: os.remove(temp_filepath)
            else:
                if os.path.exists(temp_filepath): os.remove(temp_filepath)

        except Exception as e:
            if os.path.exists(temp_filepath): os.remove(temp_filepath)
            self.log_colored(f"❌ Критическая ошибка в {mod_name}: {e}", "red")

    def process_snbt(self, filepath, engine, mode_overwrite, lang_settings):
        if not self.var_quests.get(): return
        filename = os.path.basename(filepath)
        l_regex = lang_settings.get('regex', r'[А-Яа-яЁё]')
        bak_path = filepath + ".bak"
        if not os.path.exists(bak_path): shutil.copy2(filepath, bak_path)
        content_path = filepath if mode_overwrite == "append" else bak_path
            
        try:
            with open(content_path, 'r', encoding='utf-8') as f: content = f.read()
                
            strings_to_translate = []
            for m in re.finditer(r'(?:"|)(title|subtitle|text)(?:"|)\s*:\s*"((?:[^"\\]|\\.)*)"', content, re.IGNORECASE):
                val = m.group(2)
                if val.strip() and not is_translation_key(val) and re.search(r'[a-zA-Z]', val): 
                    if mode_overwrite == "append" and re.search(l_regex, val): continue
                    strings_to_translate.append(val)
                
            for m in re.finditer(r'(?:"|)description(?:"|)\s*:\s*\[(.*?)\]', content, re.DOTALL | re.IGNORECASE):
                for str_m in re.finditer(r'"((?:[^"\\]|\\.)*)"', m.group(1)):
                    val = str_m.group(1)
                    if val.strip() and not is_translation_key(val) and re.search(r'[a-zA-Z]', val): 
                        if mode_overwrite == "append" and re.search(l_regex, val): continue
                        strings_to_translate.append(val)
                    
            strings_to_translate = list(set(strings_to_translate))
            
            if len(strings_to_translate) == 0:
                if mode_overwrite == "append": self.log_colored(f"⏩ {filename} [Квесты]: Полностью допереведен", "dim")
                return
                
            if mode_overwrite == "skip":
                with open(filepath, 'r', encoding='utf-8') as f:
                    if re.search(l_regex, f.read()):
                        self.log_colored(f"⏩ {filename} [Квесты]: Пропуск", "yellow")
                        return

            self.log_colored(f"⚡ Перевод {filename} [Квесты] - {len(strings_to_translate)} строк", "yellow")
            
            chunk_dict = {str(i): val for i, val in enumerate(strings_to_translate)}
            trans_dict = self.translate_engine(chunk_dict, engine, lang_settings)
            trans_map = {strings_to_translate[i]: trans_dict.get(str(i), strings_to_translate[i]) for i in range(len(strings_to_translate))}
            
            def repl_single(m):
                key, val = m.group(1), m.group(2)
                new_val = trans_map.get(val, val).replace('\\"', '"').replace('"', '\\"')
                return f'{key}: "{new_val}"'
                
            content = re.sub(r'(?:"|)(title|subtitle|text)(?:"|)\s*:\s*"((?:[^"\\]|\\.)*)"', repl_single, content, flags=re.IGNORECASE)
            
            def repl_desc(m):
                def repl_inner(str_m):
                    val = str_m.group(1)
                    new_val = trans_map.get(val, val).replace('\\"', '"').replace('"', '\\"')
                    return f'"{new_val}"'
                new_desc_content = re.sub(r'"((?:[^"\\]|\\.)*)"', repl_inner, m.group(1))
                return f'description: [{new_desc_content}]'
                
            content = re.sub(r'(?:"|)description(?:"|)\s*:\s*\[(.*?)\]', repl_desc, content, flags=re.DOTALL | re.IGNORECASE)
            
            with open(filepath, 'w', encoding='utf-8') as f: f.write(content)
        except Exception as e: self.log_colored(f"❌ Ошибка квеста {filename}: {e}", "red")

if __name__ == '__main__':
    app = TranslatorApp()
    app.mainloop()
