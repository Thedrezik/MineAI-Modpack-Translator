import json
import os
import shutil

from mineai.engines.base import EngineCallbacks
from mineai.engines.service import TranslationService
from mineai.io_utils import atomic_write_text
from mineai.language_validation import uses_same_latin_script
from mineai.processors.selection import skip_threshold_reached
from mineai.processors.translation_state import collect_bq_selection_with_baseline
from mineai.runtime.state import JobState


class BQProcessor:
    def __init__(
        self,
        service: TranslationService,
        state: JobState,
        callbacks: EngineCallbacks,
    ) -> None:
        self.service = service
        self.state = state
        self.callbacks = callbacks

    def process(self, file_path: str, *, target_lang: dict, mode: str) -> None:
        backup = file_path + ".bak"
        source_path = (
            backup
            if mode == "force" and os.path.exists(backup)
            else file_path
        )

        with open(source_path, "r", encoding="utf-8") as source_file:
            data = json.load(source_file)

        original_data = None
        if mode != "force" and os.path.exists(backup):
            try:
                with open(backup, "r", encoding="utf-8") as backup_file:
                    original_data = json.load(backup_file)
            except (OSError, json.JSONDecodeError):
                original_data = None

        selection = collect_bq_selection_with_baseline(
            data,
            mode,
            target_lang["regex"],
            original_data=original_data,
            same_latin_script=uses_same_latin_script(target_lang),
        )
        if not selection.pending:
            return
        if mode == "skip" and skip_threshold_reached(
            selection.total_translatable,
            len(selection.pending),
        ):
            self.callbacks.on_log(
                f"⏩ Пропуск {os.path.basename(file_path)}: "
                "готово не менее 90% строк",
                "dim",
            )
            return

        if not os.path.exists(backup):
            shutil.copy2(file_path, backup)

        name = (
            os.path.basename(os.path.dirname(file_path))
            + "/"
            + os.path.basename(file_path)
        )
        self.callbacks.on_log(
            f"⚡ Перевод BQ [{name}] — {len(selection.pending)} строк",
            "yellow",
        )
        translated = self.service.translate_dict(
            selection.pending,
            target_lang,
            self.callbacks,
            context=name,
            prompt_type="quests",
        )

        if not self.state.should_run() or not translated:
            return
        if not selection.properties_key or not selection.betterquesting_key:
            return

        bq_data = data[selection.properties_key][selection.betterquesting_key]
        for key, value in translated.items():
            bq_data[key] = value

        payload = json.dumps(data, indent=2, ensure_ascii=False)
        atomic_write_text(file_path, payload)
