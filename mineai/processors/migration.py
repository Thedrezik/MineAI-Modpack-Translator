import os
import json
import zipfile

from mineai.io_utils import atomic_write_text
from mineai.json_utils import load_lenient_json
from mineai.text_processing import is_technical_term, looks_like_source_language, polish_translation
from mineai.processors.discovery import discover_loose_lang_files


def run_migration(zip_path: str, mc_dir: str, cache_type: str, lang_api_code: str, on_log) -> int:
    if not os.path.exists(zip_path):
        on_log(f"❌ Файл не найден: {zip_path}", "red")
        return 0
        
    mods_dir = os.path.join(mc_dir, "mods")
    if not os.path.isdir(mods_dir):
        on_log(f"❌ Папка mods не найдена в {mc_dir}", "red")
        return 0
        
    on_log(f"📦 Чтение ресурс-пака {os.path.basename(zip_path)}...", "yellow")
    rp_translations = {}
    
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            for item in zf.infolist():
                name_lower = item.filename.lower()
                if name_lower.endswith(".json") and "assets/" in name_lower and "/lang/" in name_lower:
                    parts = name_lower.split("/")
                    try:
                        assets_idx = parts.index("assets")
                        namespace = parts[assets_idx + 1]
                        
                        data = load_lenient_json(zf.read(item))
                        if isinstance(data, dict):
                            if namespace not in rp_translations:
                                rp_translations[namespace] = {}
                            for k, v in data.items():
                                if isinstance(v, str) and v.strip():
                                    rp_translations[namespace][k] = polish_translation(v)
                    except (ValueError, json.JSONDecodeError, OSError):
                        continue
    except (OSError, zipfile.BadZipFile):
        on_log("❌ Ошибка чтения ZIP-архива.", "red")
        return 0
        
    if not rp_translations:
        on_log("⚠️ В архиве не найдено файлов перевода (assets/*/lang/*.json).", "yellow")
        return 0

    on_log("🔍 Сопоставление с оригинальными модами (поиск en_us.json)...", "yellow")
    mapped_pairs = {}
    
    # 1. Поиск в JAR-файлах
    jars = [os.path.join(mods_dir, f) for f in os.listdir(mods_dir) if f.endswith(".jar")]
    for jar_path in jars:
        try:
            with zipfile.ZipFile(jar_path, 'r') as zf:
                for item in zf.infolist():
                    name_lower = item.filename.lower()
                    if name_lower.endswith("en_us.json") and "assets/" in name_lower and "/lang/" in name_lower:
                        parts = name_lower.split("/")
                        try:
                            assets_idx = parts.index("assets")
                            namespace = parts[assets_idx + 1]
                            
                            if namespace in rp_translations:
                                en_data = load_lenient_json(zf.read(item))
                                tr_data = rp_translations[namespace]
                                
                                for k, en_text in en_data.items():
                                    if k in tr_data:
                                        tr_text = tr_data[k]
                                        if isinstance(en_text, str) and looks_like_source_language(en_text) and not is_technical_term(en_text):
                                            key = f"{lang_api_code}_{en_text}"
                                            mapped_pairs[key] = tr_text
                        except (ValueError, json.JSONDecodeError, OSError):
                            continue
        except (OSError, zipfile.BadZipFile):
            continue
            
    # 2. Поиск в открытых словарях (KubeJS и др.)
    loose_files = discover_loose_lang_files(mc_dir)
    for loose_path in loose_files:
        try:
            with open(loose_path, "r", encoding="utf-8") as f:
                en_data = load_lenient_json(f.read())
            
            normalized_path = loose_path.replace("\\", "/").lower()
            if "assets/" in normalized_path and "/lang/" in normalized_path:
                parts = normalized_path.split("/")
                assets_idx = parts.index("assets")
                namespace = parts[assets_idx + 1]
                
                if namespace in rp_translations:
                    tr_data = rp_translations[namespace]
                    for k, en_text in en_data.items():
                        if k in tr_data:
                            tr_text = tr_data[k]
                            if isinstance(en_text, str) and looks_like_source_language(en_text) and not is_technical_term(en_text):
                                key = f"{lang_api_code}_{en_text}"
                                mapped_pairs[key] = tr_text
        except (ValueError, json.JSONDecodeError, OSError):
            pass
            
    if mapped_pairs:
        import_dir = os.path.join(os.getcwd(), "imported_caches", cache_type)
        os.makedirs(import_dir, exist_ok=True)
        
        base_name = os.path.basename(zip_path).replace(".zip", "")
        out_file = os.path.join(import_dir, f"{base_name}.json")
        
        counter = 1
        while os.path.exists(out_file):
            out_file = os.path.join(import_dir, f"{base_name}_{counter}.json")
            counter += 1
            
        atomic_write_text(out_file, json.dumps(mapped_pairs, ensure_ascii=False, indent=2))
        on_log(f"✅ Миграция завершена! Добавлено {len(mapped_pairs)} уникальных строк.", "green")
        on_log(f"📂 Файл сохранён: imported_caches/{cache_type}/{os.path.basename(out_file)}", "magenta")
        return len(mapped_pairs)
    else:
        on_log("⚠️ Не удалось сопоставить ни одной строки (оригиналы не найдены в папке mods).", "yellow")
        return 0