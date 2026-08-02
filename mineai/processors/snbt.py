import os
import shutil

from mineai.engines.base import EngineCallbacks
from mineai.engines.service import TranslationService
from mineai.processors.snbt_extract import apply_snbt_translations, extract_snbt_strings
from mineai.runtime.state import JobState
from mineai.text_processing import already_translated


class SnbtProcessor:
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
            with open(source_path, encoding="utf-8") as f:
                content = f.read()
        except OSError as exc:
            self.callbacks.on_log(f"❌ Ошибка чтения {file_path}: {exc}", "red")
            return

        skip_regex = target_regex if mode == "append" else None
        strings = extract_snbt_strings(content, skip_translated_regex=skip_regex)
        if not strings:
            return

        if mode == "skip":
            with open(file_path, encoding="utf-8") as f:
                if already_translated(f.read(), target_regex):
                    return

        name = os.path.basename(file_path)
        self.callbacks.on_log(f"⚡ Перевод {name} [Квесты] — {len(strings)} строк", "yellow")
        chunk = {str(i): s for i, s in enumerate(strings)}
        translated = self.service.translate_dict(chunk, target_lang, self.callbacks, context=name)
        if not self.state.should_run():
            return
        mapping = {strings[i]: translated.get(str(i), strings[i]) for i in range(len(strings))}
        self.state.increment_translated(len(mapping))

        new_content = apply_snbt_translations(content, mapping)
        temp_path = file_path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        os.replace(temp_path, file_path)
