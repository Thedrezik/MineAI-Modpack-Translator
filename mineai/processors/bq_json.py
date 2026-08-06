import json
import os
import shutil

from mineai.engines.base import EngineCallbacks
from mineai.engines.service import TranslationService
from mineai.io_utils import atomic_write_text
from mineai.runtime.state import JobState
from mineai.text_processing import already_translated

class BQProcessor:
    def __init__(self, service: TranslationService, state: JobState, callbacks: EngineCallbacks) -> None:
        self.service = service
        self.state = state
        self.callbacks = callbacks

    def process(self, file_path: str, *, target_lang: dict, mode: str) -> None:
        backup = file_path + ".bak"
        source_path = backup if (mode == "force" and os.path.exists(backup)) else file_path
        target_regex = target_lang["regex"]

        with open(source_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Ищем строки для перевода...
        strings_to_translate = {}
        props_key = next((k for k in data if k.startswith("properties")), None)
        if props_key and isinstance(data[props_key], dict):
            bq_key = next((k for k in data[props_key] if k.startswith("betterquesting")), None)
            if bq_key and isinstance(data[props_key][bq_key], dict):
                bq_data = data[props_key][bq_key]
                for key_prefix in ["name", "desc"]:
                    actual_key = next((k for k in bq_data if k.startswith(key_prefix)), None)
                    if actual_key and isinstance(bq_data[actual_key], str):
                        text = bq_data[actual_key].strip()
                        if text and (mode == "force" or not already_translated(text, target_regex)):
                            strings_to_translate[actual_key] = text

        if not strings_to_translate:
            return

        # Бэкап создаётся ТОЛЬКО когда перевод действительно нужен
        if not os.path.exists(backup):
            shutil.copy2(file_path, backup)

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
             
        # Сохраняем файл обратно
        payload = json.dumps(data, indent=2, ensure_ascii=False)
        atomic_write_text(file_path, payload)