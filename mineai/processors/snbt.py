import os
import re
import shutil

from mineai.analysis_items import selected_segments_for_target
from mineai.engines.base import EngineCallbacks
from mineai.engines.service import TranslationService
from mineai.io_utils import atomic_write_text
from mineai.language_validation import uses_same_latin_script
from mineai.processors.selection import skip_threshold_reached
from mineai.processors.quest_groups import collect_quest_groups
from mineai.processors.snbt_extract import apply_snbt_translations, merge_snbt_target
from mineai.processors.translation_state import collect_snbt_selection_with_baseline
from mineai.runtime.state import JobState


def should_ignore_snbt_source(file_path: str) -> bool:
    filename = os.path.basename(file_path)
    if re.match(r"^[a-z]{2}_[a-z]{2}\.snbt$", filename):
        if filename != "en_us.snbt":
            return True
        en_us_folder = os.path.join(os.path.dirname(file_path), "en_us")
        if os.path.isdir(en_us_folder):
            return True
    return False


def get_snbt_target_path(file_path: str, target_code: str) -> str:
    if os.path.basename(file_path) == "en_us.snbt":
        return os.path.join(
            os.path.dirname(file_path),
            f"{target_code}.snbt",
        )
    return file_path.replace(
        "\\en_us\\",
        f"\\{target_code}\\",
    ).replace(
        "/en_us/",
        f"/{target_code}/",
    )


class SnbtProcessor:
    def __init__(
        self,
        service: TranslationService,
        state: JobState,
        callbacks: EngineCallbacks,
    ) -> None:
        self.service = service
        self.state = state
        self.callbacks = callbacks

    def process(
        self,
        file_path: str,
        *,
        target_lang: dict,
        mode: str,
        selected_items: frozenset[str] | None = None,
    ) -> str | None:
        filename = os.path.basename(file_path)
        if should_ignore_snbt_source(file_path):
            if filename == "en_us.snbt":
                self.callbacks.on_log(
                    f"⏩ Пропуск {filename} (найдена папка с квестами)",
                    "dim",
                )
            return

        target_file_path = get_snbt_target_path(
            file_path,
            target_lang["file"],
        )
        separate_target = target_file_path != file_path

        if separate_target:
            os.makedirs(os.path.dirname(target_file_path), exist_ok=True)
            original_path = file_path
            current_path = (
                target_file_path
                if os.path.exists(target_file_path)
                and (mode != "force" or selected_items is not None)
                else file_path
            )
        else:
            backup_path = file_path + ".bak"
            if not os.path.exists(backup_path):
                shutil.copy2(file_path, backup_path)
            original_path = backup_path
            current_path = backup_path if mode == "force" else file_path

        with open(original_path, encoding="utf-8") as source_file:
            original_content = source_file.read()
        with open(current_path, encoding="utf-8") as current_file:
            current_content = current_file.read()
        target_needs_merge = False
        if separate_target and current_path == target_file_path:
            merged_content = merge_snbt_target(
                original_content,
                current_content,
            )
            target_needs_merge = merged_content != current_content
            current_content = merged_content

        allowed_entry_ids = None
        selected_segments = selected_segments_for_target(
            selected_items,
            file_path,
            "quests",
        )
        if selected_segments is not None:
            allowed_entry_ids = frozenset(
                entry_id
                for group in collect_quest_groups(file_path, original_content)
                if group.group_id in selected_segments
                for entry_id in group.entry_ids
            )
            if not allowed_entry_ids:
                return

        selection = collect_snbt_selection_with_baseline(
            original_content,
            current_content,
            mode,
            target_lang["regex"],
            same_latin_script=uses_same_latin_script(target_lang),
            allowed_entry_ids=allowed_entry_ids,
        )
        if not selection.pending:
            if target_needs_merge:
                atomic_write_text(target_file_path, current_content)
                return target_file_path
            if separate_target and os.path.exists(target_file_path):
                self.callbacks.on_log(
                    f"⏩ Пропуск {os.path.basename(target_file_path)}: "
                    "нет новых строк, существующий SNBT сохранён без перезаписи",
                    "dim",
                )
            return
        if mode == "skip" and skip_threshold_reached(
            selection.total_translatable,
            len(selection.pending),
        ):
            self.callbacks.on_log(
                f"⏩ Пропуск {os.path.basename(target_file_path)}: "
                "готово не менее 90% строк",
                "dim",
            )
            return

        name = os.path.basename(target_file_path)
        self.callbacks.on_log(
            f"⚡ Перевод {name} [Квесты] — {len(selection.pending)} строк",
            "yellow",
        )
        chunk = {
            str(index): text
            for index, text in enumerate(selection.pending)
        }
        translated = self.service.translate_dict(
            chunk,
            target_lang,
            self.callbacks,
            context=name,
            prompt_type="quests",
        )

        if not self.state.should_run():
            return

        mapping = {
            text: translated.get(str(index), text)
            for index, text in enumerate(selection.pending)
        }
        new_content = apply_snbt_translations(
            current_content,
            mapping,
            allowed_entry_ids=allowed_entry_ids,
        )
        atomic_write_text(target_file_path, new_content)
        return target_file_path
