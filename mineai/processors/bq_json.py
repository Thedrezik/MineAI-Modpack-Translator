import json
import os
import shutil

from mineai.engines.base import EngineCallbacks
from mineai.engines.service import TranslationService
from mineai.runtime.state import JobState
from mineai.text_processing import already_translated

class BQProcessor:
    def __init__(self, service: TranslationService, state: JobState, callbacks: EngineCallbacks) -> None:
        self.service = service
        self.state = state
        self.callbacks = callbacks

    def process(self, file_path: str, *, target_lang: dict, mode: str) -> None:
        backup = file_path + ".bak"
        if not os.path.exists(backup):
            shutil.copy2(file_path, backup)

        source_path = file_path if mode == "append" else backup
        target_regex = target_lang["regex"]

        try:
            with open(source_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            self.callbacks.on_log(f"❌ Ошибка чтения {file_path}: {exc}", "red")
            return

        # Ищем строки для перевода. В BetterQuesting они лежат в properties -> betterquesting
        strings_to_translate = {}
        
        # Получаем ключи безопасно (так как там могут быть цифры типа properties:10)
        props_key = next((k for k in data if k.startswith("properties")), None)
        if props_key and isinstance(data[props_key], dict):
            bq_key = next((k for k in data[props_key] if k.startswith("betterquesting")), None)
            if bq_key and isinstance(data[props_key][bq_key], dict):
                bq_data = data[props_key][bq_key]
                
                # Ищем name и desc
                for key_prefix in ["name", "desc"]:
                    actual_key = next((k for k in bq_data if k.startswith(key_prefix)), None)
                    if actual_key and isinstance(bq_data[actual_key], str):
                        text = bq_data[actual_key].strip()
                        if text and (mode == "force" or not already_translated(text, target_regex)):
                            strings_to_translate[actual_key] = text

        if not strings_to_translate:
            return

        name = os.path.basename(os.path.dirname(file_path)) + "/" + os.path.basename(file_path)
        self.callbacks.on_log(f"⚡ Перевод BQ [{name}] — {len(strings_to_translate)} строк", "yellow")
        
        translated = self.service.translate_dict(
            strings_to_translate,
            target_lang,
            self.callbacks,
            context=name,
        )
        
        if not self.state.should_run() or not translated:
            return

        # Применяем перевод
        bq_data = data[props_key][bq_key]
        for key, value in translated.items():
             bq_data[key] = value
             
        self.state.increment_translated(len(translated))

        # Сохраняем файл обратно
        temp_path = file_path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(temp_path, file_path)
