import os
import re
import shutil

from mineai.analysis_items import selected_segments_for_target
from mineai.engines.base import EngineCallbacks
from mineai.engines.service import TranslationService
from mineai.io_utils import atomic_write_text
from mineai.language_validation import (
    translation_needs_repair,
    uses_same_latin_script,
)
from mineai.text_processing import is_nontranslatable_value
from mineai.processors.selection import skip_threshold_reached
from mineai.processors.quest_groups import collect_quest_groups
from mineai.processors.snbt_extract import apply_snbt_translations, merge_snbt_target
from mineai.processors.snbt_chapter_lang import (
    is_chapter_or_reward_snbt,
    extract_chapter_lang_entries,
    load_lang_snbt,
    dump_lang_snbt,
    merge_and_write_lang_snbt,
)
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
        # Accumulated lang entries from chapter/reward_table files.
        # Keys are "<HEXID>.<field_suffix>", values are str or list[str].
        self._accumulated_lang: dict[str, str | list[str]] = {}

    def process(
        self,
        file_path: str,
        *,
        target_lang: dict,
        mode: str,
        selected_items: frozenset[str] | None = None,
        selected_units: dict[str, frozenset[str]] | None = None,
        retranslate_selected: bool = False,
    ) -> str | None:
        allowed_unit_ids = _selected_unit_ids(selected_units, file_path)
        if selected_units is not None and allowed_unit_ids is None:
            return
        if is_chapter_or_reward_snbt(file_path):
            return self._process_chapter_to_lang(
                file_path,
                target_lang=target_lang,
                mode=mode,
                allowed_unit_ids=allowed_unit_ids,
                retranslate_selected=retranslate_selected,
            )
        return self._process_lang_catalog(
            file_path,
            target_lang=target_lang,
            mode=mode,
            selected_items=selected_items,
            selected_units=selected_units,
            retranslate_selected=retranslate_selected,
        )

    # ------------------------------------------------------------------
    # Chapter / reward_table → lang accumulation
    # ------------------------------------------------------------------

    def _process_chapter_to_lang(
        self,
        file_path: str,
        *,
        target_lang: dict,
        mode: str,
        allowed_unit_ids: frozenset[str] | None = None,
        retranslate_selected: bool = False,
    ) -> None:
        """Extract translatable text from a chapter SNBT and queue it for
        later writing into lang/<target_code>.snbt.

        The chapter file itself is never modified.
        """
        try:
            with open(file_path, encoding="utf-8") as fh:
                content = fh.read()
        except OSError:
            return None

        entries = extract_chapter_lang_entries(content, file_path)
        allowed_entry_ids = _selected_entry_ids(allowed_unit_ids)
        if allowed_unit_ids is not None:
            if not allowed_entry_ids:
                return None
            entries = {
                key: value
                for key, value in entries.items()
                if key.split(".", 1)[0].casefold() in allowed_entry_ids
            }
        if not entries:
            return None

        target_regex = target_lang.get("regex", "")
        parent = os.path.dirname(file_path)
        quests_dir = (
            os.path.dirname(parent)
            if os.path.basename(parent).casefold()
            in {"chapters", "reward_tables"}
            else parent
        )
        existing = load_lang_snbt(
            os.path.join(quests_dir, "lang", f"{target_lang['file']}.snbt")
        )

        def value_needs_translation(
            source_value: str | list[str],
            existing_value: str | list[str] | None,
        ) -> bool:
            if mode == "force" or existing_value is None:
                return True
            if isinstance(source_value, list):
                if not isinstance(existing_value, list):
                    return True
                if len(source_value) != len(existing_value):
                    return True
                return any(
                    (
                        source_line != target_line
                        if is_nontranslatable_value(source_line)
                        else translation_needs_repair(
                            source_line,
                            target_line,
                            target_lang,
                        )
                    )
                    for source_line, target_line in zip(
                        source_value,
                        existing_value,
                        strict=True,
                    )
                )
            if isinstance(existing_value, list):
                return True
            return translation_needs_repair(source_value, existing_value, target_lang)

        # Determine which entries still need translation
        pending: dict[str, str | list[str]] = {}
        for key, value in entries.items():
            if key in self._accumulated_lang:
                continue
            if retranslate_selected or value_needs_translation(value, existing.get(key)):
                pending[key] = value

        if not pending:
            return None

        # Flatten to individual strings for the translation service
        flat_texts: list[str] = []
        flat_keys: list[tuple[str, int]] = []  # (lang_key, index_in_list_or_-1)
        preserved_flat: dict[int, str] = {}
        flat_index = 0
        for key, value in pending.items():
            existing_value = existing.get(key)
            if isinstance(value, list):
                for idx, line in enumerate(value):
                    flat_texts.append(line)
                    flat_keys.append((key, idx))
                    if (
                        mode != "force"
                        and isinstance(existing_value, list)
                        and len(existing_value) == len(value)
                        and not is_nontranslatable_value(line)
                        and not translation_needs_repair(
                            line,
                            existing_value[idx],
                            target_lang,
                        )
                    ):
                        preserved_flat[flat_index] = existing_value[idx]
                    flat_index += 1
            else:
                flat_texts.append(value)
                flat_keys.append((key, -1))
                flat_index += 1

        # Filter by mode (skip entries that are already in target regex)
        to_translate_indices: list[int] = []
        for i, text in enumerate(flat_texts):
            if i in preserved_flat or is_nontranslatable_value(text):
                continue
            if mode != "force" and target_regex and re.search(target_regex, text):
                continue
            to_translate_indices.append(i)

        if not to_translate_indices:
            # Still accumulate the untranslated originals so they exist in lang
            for key, value in pending.items():
                if key in self._accumulated_lang:
                    continue
                if isinstance(value, list):
                    start = next(
                        index
                        for index, (flat_key, _list_index) in enumerate(flat_keys)
                        if flat_key == key
                    )
                    self._accumulated_lang[key] = [
                        preserved_flat.get(start + index, line)
                        for index, line in enumerate(value)
                    ]
                else:
                    self._accumulated_lang[key] = value
            return None

        name = os.path.basename(file_path)
        self.callbacks.on_log(
            f"⚡ Перевод {name} [Квесты→lang] — {len(to_translate_indices)} строк",
            "yellow",
        )

        chunk = {
            str(i): flat_texts[to_translate_indices[i]]
            for i in range(len(to_translate_indices))
        }
        translated = self.service.translate_dict(
            chunk,
            target_lang,
            self.callbacks,
            context=name,
            prompt_type="quests",
        )

        if not self.state.should_run():
            return None

        # Map translations back to original flat_texts positions
        translated_texts = list(flat_texts)
        for index, value in preserved_flat.items():
            translated_texts[index] = value
        failed_indices: set[int] = set()
        for batch_idx, orig_idx in enumerate(to_translate_indices):
            tx = translated.get(str(batch_idx))
            if tx:
                translated_texts[orig_idx] = tx
            else:
                failed_indices.add(orig_idx)

        # Reconstruct per-key values and accumulate
        for i, (key, list_idx) in enumerate(flat_keys):
            # Keep failed text in its original slot.  Dropping it would
            # compact the list and move page-break/image macros to another
            # FTB Quests page.  The source text remains visible and can be
            # retried safely on the next run.
            tx_value = flat_texts[i] if i in failed_indices else translated_texts[i]
            if list_idx == -1:
                # Single string
                if key not in self._accumulated_lang:
                    self._accumulated_lang[key] = tx_value
            else:
                # List — build incrementally
                existing = self._accumulated_lang.get(key)
                if existing is None:
                    self._accumulated_lang[key] = [tx_value]
                elif isinstance(existing, list):
                    existing.append(tx_value)
                # (if it became a str somehow, leave it)

        return None

    def flush_accumulated_lang(
        self,
        quests_dir: str,
        target_code: str,
    ) -> str | None:
        """Write all accumulated chapter/RT lang entries to lang/<target>.snbt.

        Returns the path to the written file, or None if nothing was written.
        """
        if not self._accumulated_lang:
            return None

        lang_dir = os.path.join(quests_dir, "lang")
        target_path = os.path.join(lang_dir, f"{target_code}.snbt")

        merge_and_write_lang_snbt(
            target_path,
            self._accumulated_lang,
            # The buffer contains only missing or invalid values.  Valid
            # entries were filtered before translation, so repaired values
            # must replace their stale counterparts on disk.
            overwrite_existing=True,
        )
        self._accumulated_lang.clear()
        return target_path

    # ------------------------------------------------------------------
    # Lang-catalog (en_us.snbt / lang/en_us/*.snbt) — unchanged logic
    # ------------------------------------------------------------------

    def _process_lang_catalog(
        self,
        file_path: str,
        *,
        target_lang: dict,
        mode: str,
        selected_items: frozenset[str] | None = None,
        selected_units: dict[str, frozenset[str]] | None = None,
        retranslate_selected: bool = False,
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

        if separate_target and filename == "en_us.snbt":
            self.callbacks.on_log(
                f"📖 Обработка {filename} [lang/] → {os.path.basename(target_file_path)} "
                f"(режим: {mode})",
                "cyan",
            )

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

        allowed_unit_ids = _selected_unit_ids(selected_units, file_path)
        if selected_units is not None and allowed_unit_ids is None:
            return

        selection = collect_snbt_selection_with_baseline(
            original_content,
            current_content,
            mode,
            target_lang["regex"],
            same_latin_script=uses_same_latin_script(target_lang),
            allowed_entry_ids=allowed_entry_ids,
            allowed_unit_ids=allowed_unit_ids,
            target_lang=target_lang,
        )
        if allowed_unit_ids is not None and retranslate_selected:
            selection.pending = list(
                dict.fromkeys(
                    node.source
                    for node in (selection.document.nodes if selection.document else ())
                    if node.translatable and node.key in allowed_unit_ids
                )
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
        ) and not selection.repair_pending:
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
            original_content,
            mapping,
            target_content=current_content,
            allowed_entry_ids=allowed_entry_ids,
            allowed_unit_ids=allowed_unit_ids,
        )
        atomic_write_text(target_file_path, new_content)
        return target_file_path


def _selected_unit_ids(
    selected_units: dict[str, frozenset[str]] | None,
    file_path: str,
) -> frozenset[str] | None:
    if selected_units is None:
        return None
    normalized = file_path.replace("\\", "/")
    for path, unit_ids in selected_units.items():
        candidate = path.replace("\\", "/")
        if (
            candidate.casefold() == normalized.casefold()
            or normalized.casefold().endswith("/" + candidate.casefold().lstrip("/"))
        ):
            return frozenset(unit_ids)
    return frozenset()


def _selected_entry_ids(
    unit_ids: frozenset[str] | None,
) -> frozenset[str]:
    """Extract FTB quest IDs from structured SNBT unit keys."""
    if unit_ids is None:
        return frozenset()
    entry_ids: set[str] = set()
    for unit_id in unit_ids:
        match = re.match(r"(?i)^snbt/([0-9a-f]{16})(?:/|$)", unit_id)
        if match:
            entry_ids.add(match.group(1).casefold())
    return frozenset(entry_ids)
