import json
import os
import re
import zipfile

from mineai.constants import BOOK_PATH_MARKERS, MD_PATH_MARKERS, RESEARCH_PATH_MARKERS
from mineai.json_utils import iter_translatable_strings, load_lenient_json
from mineai.mod_names import get_mod_name
from mineai.processors.snbt_extract import extract_snbt_strings
from mineai.runtime.state import JobState
from mineai.text_processing import (
    already_translated,
    is_technical_term,
    is_translation_key,
    looks_like_source_language,
)


class ModpackAnalyzer:
    def __init__(self, state: JobState) -> None:
        self.state = state

    def analyze(
        self,
        mc_dir: str,
        *,
        target_lang: dict,
        translate_mods: bool,
        translate_books: bool,
        translate_quests: bool,
        on_row,
        on_log,
        on_status,
    ) -> tuple[int, int]:
        target_file = f"{target_lang['file']}.json"
        target_regex = target_lang["regex"]
        mods_dir = os.path.join(mc_dir, "mods")
        quests_dir = os.path.join(mc_dir, "config", "ftbquests", "quests")

        total_en = 0
        total_tr = 0
        jars: list[str] = []
        if os.path.isdir(mods_dir) and (translate_mods or translate_books):
            jars = [os.path.join(mods_dir, f) for f in os.listdir(mods_dir) if f.endswith(".jar")]

        for index, path in enumerate(jars):
            if not self.state.should_run():
                break
            self.state.wait_if_paused()
            mod_name = get_mod_name(path)
            on_status(f"Анализ: {mod_name}...", index / max(len(jars), 1))
            en, tr = self._analyze_jar(
                path, target_file, target_regex, translate_mods, translate_books, on_row, mod_name
            )
            total_en += en
            total_tr += tr

        snbt_files: list[str] = []
        if os.path.isdir(quests_dir) and translate_quests:
            for root, _, files in os.walk(quests_dir):
                # Отсекаем папки других локализаций (es_es, pt_br и т.д.)
                parts = root.lower().split(os.sep)
                if "lang" in parts:
                    lang_idx = parts.index("lang")
                    if len(parts) > lang_idx + 1 and parts[lang_idx + 1] != "en_us":
                        continue

                for name in files:
                    if name.endswith(".snbt"):
                        nl = name.lower()
                        # Отсекаем файлы других локализаций в корне (ru_ru, es_es и т.д.)
                        if re.match(r"^[a-z]{2}_[a-z]{2}\.snbt$", nl) and nl != "en_us.snbt":
                            continue
                        
                        # Отсекаем огромный резервный en_us.snbt, если рядом есть папка en_us
                        if nl == "en_us.snbt" and os.path.isdir(os.path.join(root, "en_us")):
                            continue

                        snbt_files.append(os.path.join(root, name))
        
        # ПОИСК BQ
        bq_dir = os.path.join(mc_dir, "config", "betterquesting", "DefaultQuests")
        bq_files: list[str] = []
        if os.path.isdir(bq_dir) and translate_quests:
            for root, _, files in os.walk(bq_dir):
                for name in files:
                    if name.endswith(".json") and ("QuestLines" in root or "Quests" in root):
                        bq_files.append(os.path.join(root, name))
                        
        for index, path in enumerate(snbt_files):
            if not self.state.should_run():
                break
            self.state.wait_if_paused()
            on_status(f"Анализ: {os.path.basename(path)}...", (len(jars) + index) / max(len(jars) + len(snbt_files), 1))
            en, tr = self._analyze_snbt(path, target_regex, on_row)
            total_en += en
            total_tr += tr
        # АНАЛИЗ BQ
        for index, path in enumerate(bq_files):
            if not self.state.should_run():
                break
            self.state.wait_if_paused()
            on_status(f"Анализ: BQ {os.path.basename(path)}...", (len(jars) + len(snbt_files) + index) / max(len(jars) + len(snbt_files) + len(bq_files), 1))
            en, tr = self._analyze_bq(path, target_regex, on_row)
            total_en += en
            total_tr += tr
        return total_en, total_tr

    def _analyze_jar(self, path, target_file, target_regex, translate_mods, translate_books, on_row, mod_name):
        total_en = 0
        total_tr = 0
        try:
            with zipfile.ZipFile(path, "r") as zin:
                locale = {
                    i.filename.lower(): i
                    for i in zin.infolist()
                    if target_file in i.filename.lower()
                    or f"/{target_file.replace('.json','')}/" in i.filename.lower()
                }
                if translate_mods:
                    en, tr = self._analyze_mods_ui(zin, locale, target_file, mod_name, on_row)
                    total_en += en
                    total_tr += tr
                if translate_books:
                    en, tr = self._analyze_books(zin, locale, target_file, target_regex, mod_name, on_row)
                    total_en += en
                    total_tr += tr
        except (OSError, zipfile.BadZipFile):
            pass
        return total_en, total_tr

    def _analyze_mods_ui(self, zin, locale, target_file, mod_name, on_row):
        en_c = tr_c = 0
        for item in zin.infolist():
            fl = item.filename.lower()
            if not fl.endswith("en_us.json") or any(x in fl for x in BOOK_PATH_MARKERS):
                continue
            try:
                en = load_lenient_json(zin.read(item))
                tr_key = fl.replace("en_us.json", target_file)
                tr = load_lenient_json(zin.read(locale[tr_key])) if tr_key in locale else {}
                for key, value in en.items():
                    if not isinstance(value, str) or not looks_like_source_language(value) or is_technical_term(value):
                        continue
                    en_c += 1
                    existing = str(tr.get(key, ""))
                    if existing.strip() and existing != value:
                        tr_c += 1
            except (json.JSONDecodeError, OSError):
                continue
        if en_c:
            on_row("📦", mod_name, "Интерфейс", tr_c, en_c, int(tr_c / en_c * 100))
        return en_c, tr_c

    def _analyze_books(self, zin, locale, target_file, target_regex, mod_name, on_row):
        b_en = b_tr = m_en = m_tr = 0
        for item in zin.infolist():
            fl = item.filename.lower()
            is_jb = (
                fl.endswith(".json")
                and "/en_us/" in fl
                and (
                    any(x in fl for x in BOOK_PATH_MARKERS)
                    or any(x in fl for x in RESEARCH_PATH_MARKERS)
                )
            )
            is_mb = (
                (fl.endswith(".md") or fl.endswith(".txt"))
                and "/en_us/" in fl
                and any(x in fl for x in MD_PATH_MARKERS)
            )
            if is_jb:
                try:
                    en = load_lenient_json(zin.read(item))
                    tr_path = fl.replace("/en_us/", f"/{target_file.replace('.json','')}/")
                    tr = load_lenient_json(zin.read(locale[tr_path])) if tr_path in locale else {}
                    en_s = [s for p, s in iter_translatable_strings(en) if s.strip() and looks_like_source_language(s)]
                    tr_s = [s for p, s in iter_translatable_strings(tr)] if tr else []
                    b_en += len(en_s)
                    for idx, s in enumerate(en_s):
                        if idx < len(tr_s) and tr_s[idx] != s and tr_s[idx].strip():
                            b_tr += 1
                except (json.JSONDecodeError, OSError):
                    pass
            elif is_mb:
                try:
                    en_t = zin.read(item).decode("utf-8-sig", errors="ignore")
                    tr_path = fl.replace("/en_us/", f"/{target_file.replace('.json','')}/") if "/en_us/" in fl else fl
                    tr_t = zin.read(locale[tr_path]).decode("utf-8-sig", errors="ignore") if tr_path in locale else ""
                    tr_lines = tr_t.split("\n")
                    in_yaml = False
                    for idx, line in enumerate(en_t.split("\n")):
                        if line.strip() == "---":
                            in_yaml = not in_yaml
                            continue
                        if in_yaml:
                            m = re.match(r'^(\s*title\s*:\s*[\'"]?)(.*?)([\'"]?)$', line, re.IGNORECASE)
                            if m and looks_like_source_language(m.group(2)):
                                m_en += 1
                                if idx < len(tr_lines) and already_translated(tr_lines[idx], target_regex):
                                    m_tr += 1
                            continue
                        if line.strip().startswith("<") or line.strip().startswith("!["):
                            continue
                        if line.strip() and looks_like_source_language(line) and not is_technical_term(line):
                            m_en += 1
                            if idx < len(tr_lines) and already_translated(tr_lines[idx], target_regex):
                                m_tr += 1
                except OSError:
                    pass
        if b_en:
            on_row("📖", mod_name, "Книга(JSON)", b_tr, b_en, int(b_tr / b_en * 100))
        if m_en:
            on_row("📝", mod_name, "Книга(MD)", m_tr, m_en, int(m_tr / m_en * 100))
        return b_en + m_en, b_tr + m_tr

    def _analyze_snbt(self, path, target_regex, on_row):
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read()
        except OSError:
            return 0, 0
        strings = extract_snbt_strings(content)
        en_c = len(strings)
        tr_c = sum(1 for s in strings if re.search(target_regex, s))
        if en_c:
            on_row("📜", os.path.basename(path), "Квесты", tr_c, en_c, int(tr_c / en_c * 100))
        return en_c, tr_c
    
    
    def _analyze_bq(self, path: str, target_regex: str, on_row) -> tuple[int, int]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return 0, 0

        en_c = 0
        tr_c = 0
        props_key = next((k for k in data if k.startswith("properties")), None)
        if props_key and isinstance(data[props_key], dict):
            bq_key = next((k for k in data[props_key] if k.startswith("betterquesting")), None)
            if bq_key and isinstance(data[props_key][bq_key], dict):
                bq_data = data[props_key][bq_key]
                for key_prefix in ["name", "desc"]:
                    actual_key = next((k for k in bq_data if k.startswith(key_prefix)), None)
                    if actual_key and isinstance(bq_data[actual_key], str):
                        text = bq_data[actual_key].strip()
                        if text:
                            en_c += 1
                            if already_translated(text, target_regex):
                                tr_c += 1
                                
        if en_c:
            folder = os.path.basename(os.path.dirname(path))
            name = f"{folder}/{os.path.basename(path)}"
            on_row("📜", name, "BetterQuesting", tr_c, en_c, int(tr_c / en_c * 100))
            
        return en_c, tr_c