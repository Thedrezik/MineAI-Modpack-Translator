import os
import shutil
import re

from mineai.engines.base import EngineCallbacks
from mineai.engines.service import TranslationService
from mineai.io_utils import atomic_write_text
from mineai.processors.snbt_extract import apply_snbt_translations, extract_snbt_strings
from mineai.runtime.state import JobState
from mineai.text_processing import already_translated


class SnbtProcessor:
    def __init__(self, service: TranslationService, state: JobState, callbacks: EngineCallbacks) -> None:
        self.service = service
        self.state = state
        self.callbacks = callbacks

    def process(self, file_path: str, *, target_lang: dict, mode: str) -> None:
        filename = os.path.basename(file_path)
        
        # Если файл называется как локализация (например, es_es.snbt), но это не английский - полностью игнорируем его
        if re.match(r"^[a-z]{2}_[a-z]{2}\.snbt$", filename) and filename != "en_us.snbt":
            return
            
        # --- НОВАЯ ЛОГИКА: ИГНОРИРОВАНИЕ ОГРОМНОГО ФАЙЛА ПРИ НАЛИЧИИ ПАПКИ ---
        if filename == "en_us.snbt":
            dir_name = os.path.dirname(file_path)
            en_us_folder = os.path.join(dir_name, "en_us")
            # Если рядом есть папка en_us (с разбитыми квестами), игнорируем этот гигантский резервный файл
            if os.path.isdir(en_us_folder):
                self.callbacks.on_log(f"⏩ Пропуск {filename} (найдена папка с квестами)", "dim")
                return
        # ----------------------------------------------------------------------
        
        is_lang_file = filename == "en_us.snbt"
        mc_code = target_lang["file"]
        
        # Шаг 1. Определяем, куда сохранять и откуда читать
        if is_lang_file:
            target_file_path = os.path.join(os.path.dirname(file_path), f"{mc_code}.snbt")
            
            # Для en_us мы всегда берем оригинал как шаблон, он не должен меняться
            source_path = file_path
        else:
            # Для разбитых квестов (например, внутри папки en_us/chapters/)
            # Заменяем en_us в пути на целевой язык (например, ru_ru), чтобы не сломать английские исходники
            target_file_path = file_path.replace("\\en_us\\", f"\\{mc_code}\\").replace("/en_us/", f"/{mc_code}/")
            
            if target_file_path != file_path:
                # Если путь успешно изменен на ru_ru, бэкап не нужен, берем оригинал
                os.makedirs(os.path.dirname(target_file_path), exist_ok=True)
                source_path = file_path
            else:
                # Старое поведение для файлов, лежащих в корне (перевод на месте с бэкапом)
                backup = file_path + ".bak"
                if not os.path.exists(backup):
                    shutil.copy2(file_path, backup)
                source_path = file_path if mode == "append" else backup

        target_regex = target_lang["regex"]

        with open(source_path, encoding="utf-8") as f:
            content = f.read()
            
        

        # Шаг 2. Достаем строки и переводим
        skip_regex = target_regex if mode == "append" else None
        strings = extract_snbt_strings(content, skip_translated_regex=skip_regex)
        if not strings:
            return

        if mode == "skip" and os.path.exists(target_file_path):
            try:
                with open(target_file_path, encoding="utf-8") as f:
                    if already_translated(f.read(), target_regex):
                        return
            except OSError:
                pass

        name = os.path.basename(target_file_path)
        self.callbacks.on_log(f"⚡ Перевод {name} [Квесты] — {len(strings)} строк", "yellow")
        
        chunk = {str(i): s for i, s in enumerate(strings)}
        # ДОБАВЛЕНО: prompt_type="quests"
        translated = self.service.translate_dict(chunk, target_lang, self.callbacks, context=name, prompt_type="quests")
        
        # --- НОВАЯ ПРОВЕРКА ОТМЕНЫ ---
        if not self.state.should_run():
            return
        # -----------------------------
        
        mapping = {strings[i]: translated.get(str(i), strings[i]) for i in range(len(strings))}
        
        new_content = apply_snbt_translations(content, mapping)
        
        # Шаг 3. Сохраняем в целевой файл (заменяя авторский ru_ru.snbt)
        atomic_write_text(target_file_path, new_content)