import json
import os
import re
import zipfile

from formatkit import FormatRegistry, FormatValidationError
from formatkit.adapters.modonomicon import is_modonomicon_path
from mineai.analysis_items import selected_segments_for_target, target_is_selected
from mineai.analysis_items import loose_file_scope
from mineai.json_utils import load_lenient_json
from mineai.language_validation import uses_same_latin_script
from mineai.processors.bq_baseline import resolve_bq_force_baseline
from mineai.processors.book_paths import (
    MarkdownBookLocator,
    legacy_lang_target_path,
    localized_json_target_path,
    minecraft_lang_json_target_path,
)
from mineai.processors.locale_keys import (
    collect_lang_keys_to_translate,
    count_translatable_lang_entries,
)
from mineai.processors.selection import (
    collect_book_json_selection,
    skip_threshold_reached,
)
from mineai.processors.loose_paths import loose_target_disk_path
from mineai.processors.quest_groups import collect_quest_groups
from mineai.processors.snbt import (
    get_snbt_target_path,
    should_ignore_snbt_source,
)
from mineai.processors.snbt_extract import merge_snbt_target
from mineai.processors.translation_state import (
    collect_bq_selection_with_baseline,
    collect_snbt_selection_with_baseline,
)
from mineai.runtime.state import JobState


class StringEstimator:
    def __init__(self, state: JobState) -> None:
        self.state = state
        self.format_registry = FormatRegistry.default()

    def estimate(
        self,
        jar_files: list[str],
        loose_files: list[str],
        snbt_files: list[str],
        bq_files: list[str],
        *,
        target_lang: dict,
        mode: str,
        translate_mods: bool,
        translate_books: bool,
        translate_quests: bool,
        smart_glue: bool,
        selected_items: frozenset[str] | None = None,
        heracles_files: list[str] | None = None,
        book_locator: MarkdownBookLocator | None = None,
    ) -> int:
        total = 0
        target_file = f"{target_lang['file']}.json"
        target_regex = target_lang["regex"]

        book_locator = book_locator or MarkdownBookLocator.from_archives(
            jar_files,
            target_lang["file"],
        )
        for path in jar_files:
            if not self.state.should_run():
                return total
            self.state.wait_if_paused()
            selected_mods = translate_mods and target_is_selected(
                selected_items,
                path,
                "mods",
            )
            selected_books = translate_books and target_is_selected(
                selected_items,
                path,
                "books",
            )
            total += self._estimate_jar(
                path,
                target_file,
                target_lang,
                mode,
                selected_mods,
                selected_books,
                smart_glue,
                book_locator,
            )

        for path in loose_files:
            if not self.state.should_run():
                return total
            self.state.wait_if_paused()
            total += self._estimate_loose(
                path,
                target_file,
                mode,
                target_regex,
            )

        if translate_quests:
            for path in snbt_files:
                if not self.state.should_run():
                    return total
                self.state.wait_if_paused()
                total += self._estimate_snbt(
                    path,
                    mode,
                    target_regex,
                    target_lang["file"],
                    selected_items,
                    target_lang,
                )

            for path in bq_files:
                if not self.state.should_run():
                    return total
                self.state.wait_if_paused()
                total += self._estimate_bq(
                    path,
                    mode,
                    target_regex,
                    target_lang,
                )

            for path in heracles_files or []:
                if not self.state.should_run():
                    return total
                self.state.wait_if_paused()
                total += self._estimate_heracles(
                    path,
                    mode,
                    target_lang,
                )

        return total

    def _estimate_jar(
        self,
        path,
        target_file,
        target_lang,
        mode,
        translate_mods,
        translate_books,
        smart_glue,
        book_locator=None,
    ) -> int:
        count = 0
        try:
            with zipfile.ZipFile(path, "r") as archive:
                archive_items = archive.infolist()
                locale = {
                    item.filename.lower(): item
                    for item in archive_items
                }
                book_locator = book_locator or MarkdownBookLocator(
                    [item.filename for item in archive_items],
                    target_lang["file"],
                )
                companion_lang_prefixes = (
                    self.format_registry.companion_lang_prefixes(
                        [item.filename for item in archive_items]
                    )
                    if translate_books
                    else ()
                )
                companion_documents: list[tuple[str, str]] = []
                if translate_books:
                    for document_item in archive_items:
                        if not is_modonomicon_path(document_item.filename):
                            continue
                        try:
                            companion_documents.append((
                                document_item.filename,
                                archive.read(document_item).decode(
                                    "utf-8-sig",
                                    errors="ignore",
                                ),
                            ))
                        except OSError:
                            continue
                companion_lang_keys = self.format_registry.companion_lang_keys(
                    companion_documents
                )
                for item in archive_items:
                    is_book_json = localized_json_target_path(
                        item.filename,
                        target_lang["file"],
                    ) is not None
                    markdown_target = book_locator.target_path(item.filename)
                    is_book_md = markdown_target is not None
                    is_modonomicon = is_modonomicon_path(item.filename)
                    upstream_adapter_id = (
                        self.format_registry.upstream_adapter_id(
                            item.filename
                        )
                    )
                    upstream_target = (
                        self.format_registry.upstream_target_path(
                            item.filename,
                            target_lang["file"],
                        )
                    )
                    legacy_lang_target = legacy_lang_target_path(
                        item.filename,
                        target_lang["file"],
                    )
                    is_lang = (
                        minecraft_lang_json_target_path(
                            item.filename,
                            target_lang["file"],
                        ) is not None
                        and not is_book_json
                    )

                    if translate_mods and legacy_lang_target:
                        count += self._count_book_md(
                            archive,
                            item,
                            locale,
                            target_lang,
                            mode,
                            smart_glue,
                            legacy_lang_target,
                        )
                    elif translate_mods and is_lang:
                        try:
                            count += self._count_book_md(
                                archive,
                                item,
                                locale,
                                target_lang,
                                mode,
                                smart_glue,
                                upstream_target
                                or minecraft_lang_json_target_path(
                                    item.filename,
                                    target_lang["file"],
                                ),
                            )
                        except (ValueError, FormatValidationError):
                            count += self._count_lang(
                                archive,
                                item,
                                locale,
                                target_file,
                                mode,
                                target_lang["regex"],
                            )
                    elif (
                        companion_lang_prefixes or companion_lang_keys
                    ) and is_lang:
                        count += self._count_book_lang_metadata(
                            archive,
                            item,
                            locale,
                            target_file,
                            mode,
                            target_lang["regex"],
                            companion_lang_prefixes,
                            companion_lang_keys,
                        )
                    elif translate_books and is_modonomicon:
                        count += self._count_formatkit_document(
                            archive,
                            item,
                            target_lang,
                            mode,
                        )
                    elif (
                        translate_books
                        and upstream_adapter_id == "patchouli-book-json"
                        and upstream_target
                    ):
                        try:
                            count += self._count_book_md(
                                archive,
                                item,
                                locale,
                                target_lang,
                                mode,
                                smart_glue,
                                upstream_target,
                            )
                        except (ValueError, FormatValidationError):
                            count += self._count_book_json(
                                archive,
                                item,
                                locale,
                                target_lang,
                                mode,
                            )
                    elif (
                        translate_books
                        and upstream_adapter_id
                        in {
                            "oracle-index-mdx",
                            "oracle-index-meta-json",
                        }
                        and upstream_target
                    ):
                        count += self._count_book_md(
                            archive,
                            item,
                            locale,
                            target_lang,
                            mode,
                            smart_glue,
                            upstream_target,
                        )
                    elif translate_books and is_book_json:
                        count += self._count_book_json(
                            archive,
                            item,
                            locale,
                            target_lang,
                            mode,
                        )
                    elif translate_books and is_book_md:
                        count += self._count_book_md(
                            archive,
                            item,
                            locale,
                            target_lang,
                            mode,
                            smart_glue,
                            markdown_target,
                        )
        except (OSError, zipfile.BadZipFile):
            return 0
        return count

    def _count_lang(
        self,
        archive,
        item,
        locale,
        target_file,
        mode,
        target_regex,
    ) -> int:
        try:
            source_data = load_lenient_json(archive.read(item))
        except (json.JSONDecodeError, OSError):
            return 0

        target_path = re.sub(
            r"en_us\.json$",
            target_file,
            item.filename,
            flags=re.IGNORECASE,
        )
        target_data = {}
        target_key = target_path.lower()
        if target_key in locale:
            try:
                target_data = load_lenient_json(
                    archive.read(locale[target_key])
                )
            except (json.JSONDecodeError, OSError):
                target_data = {}

        pending = collect_lang_keys_to_translate(
            source_data,
            target_data,
            mode,
            target_regex,
        )
        total_translatable = count_translatable_lang_entries(source_data)
        if mode == "skip" and skip_threshold_reached(
            total_translatable,
            len(pending),
        ):
            return 0
        return len(pending)

    def _count_book_json(
        self,
        archive,
        item,
        locale,
        target_lang,
        mode,
    ) -> int:
        try:
            source_data = load_lenient_json(archive.read(item))
        except (json.JSONDecodeError, OSError):
            return 0

        target_path = re.sub(
            r"/en_us/",
            f"/{target_lang['file']}/",
            item.filename,
            flags=re.IGNORECASE,
        )
        target_data = {}
        target_key = target_path.lower()
        if mode != "force" and target_key in locale:
            try:
                target_data = load_lenient_json(
                    archive.read(locale[target_key])
                )
            except (json.JSONDecodeError, OSError):
                target_data = {}

        source_map, _preserved, pending = collect_book_json_selection(
            source_data,
            target_data,
            mode,
            target_lang,
        )
        if mode == "skip" and skip_threshold_reached(
            len(source_map),
            len(pending),
        ):
            return 0
        return len(pending)

    def _count_book_md(
        self,
        archive,
        item,
        locale,
        target_lang,
        mode,
        smart_glue,
        target_path,
    ) -> int:
        try:
            source_text = archive.read(item).decode(
                "utf-8-sig",
                errors="ignore",
            )
        except OSError:
            return 0

        target_text = ""
        target_key = target_path.lower()
        if mode != "force" and target_key in locale:
            try:
                target_text = archive.read(locale[target_key]).decode(
                    "utf-8-sig",
                    errors="ignore",
                )
            except OSError:
                target_text = ""

        del smart_glue
        plan = self.format_registry.plan(
            item.filename,
            source_text,
            target_lang["file"],
            target_path_hint=target_path,
        )
        pending = {unit.id for unit in plan.units}
        if target_text and mode != "force":
            try:
                target_plan = self.format_registry.plan(
                    plan.target_path or target_path,
                    target_text,
                    target_lang["file"],
                    target_path_hint=plan.target_path or target_path,
                )
                _merged, pending = plan.merge_existing(
                    target_plan,
                    target_lang["regex"],
                )
            except (ValueError, FormatValidationError):
                pending = {unit.id for unit in plan.units}
        if mode == "skip" and skip_threshold_reached(
            len(plan.units),
            len(pending),
        ):
            return 0
        return len(pending)

    def _count_formatkit_document(
        self,
        archive,
        item,
        target_lang,
        mode,
    ) -> int:
        del mode
        try:
            source_text = archive.read(item).decode(
                "utf-8-sig",
                errors="ignore",
            )
            plan = self.format_registry.plan(
                item.filename,
                source_text,
                target_lang["file"],
                target_path_hint=item.filename,
            )
        except (OSError, ValueError):
            return 0
        return len(plan.units)

    def _count_book_lang_metadata(
        self,
        archive,
        item,
        locale,
        target_file,
        mode,
        target_regex,
        prefixes,
        exact_keys=frozenset(),
    ) -> int:
        try:
            source_data = load_lenient_json(archive.read(item))
        except (json.JSONDecodeError, OSError):
            return 0
        source = {
            key: value
            for key, value in source_data.items()
            if isinstance(key, str)
            and isinstance(value, str)
            and (key.startswith(prefixes) or key in exact_keys)
        }
        if not source:
            return 0
        target_path = re.sub(
            r"en_us\.json$",
            target_file,
            item.filename,
            flags=re.IGNORECASE,
        )
        target = {}
        if target_path.lower() in locale:
            try:
                target = load_lenient_json(
                    archive.read(locale[target_path.lower()])
                )
            except (json.JSONDecodeError, OSError):
                target = {}
        pending = collect_lang_keys_to_translate(
            source,
            {key: value for key, value in target.items() if key in source},
            mode,
            target_regex,
        )
        if mode == "skip" and skip_threshold_reached(len(source), len(pending)):
            return 0
        return len(pending)

    def _estimate_loose(
        self,
        path,
        target_file,
        mode,
        target_regex,
    ) -> int:
        target_code = target_file.removesuffix(".json")
        if not path.casefold().endswith(".json"):
            try:
                with open(path, encoding="utf-8-sig") as source_file:
                    source_text = source_file.read()
                target_path = loose_target_disk_path(path, target_code)
                plan = self.format_registry.plan(
                    path,
                    source_text,
                    target_code,
                    target_path_hint=target_path,
                )
                pending = {unit.id for unit in plan.units}
                if mode != "force" and os.path.exists(target_path):
                    with open(target_path, encoding="utf-8-sig") as target_handle:
                        target_text = target_handle.read()
                    target_plan = self.format_registry.plan(
                        target_path,
                        target_text,
                        target_code,
                        target_path_hint=target_path,
                    )
                    _merged, pending = plan.merge_existing(
                        target_plan,
                        target_regex,
                    )
            except (OSError, ValueError, FormatValidationError):
                return 0
            if mode == "skip" and skip_threshold_reached(
                len(plan.units),
                len(pending),
            ):
                return 0
            return len(pending)

        try:
            with open(path, encoding="utf-8") as source_file:
                source_data = load_lenient_json(
                    source_file.read().encode("utf-8")
                )
            target_path = loose_target_disk_path(path, target_code)
            target_data = {}
            if os.path.exists(target_path):
                with open(target_path, encoding="utf-8") as target_handle:
                    target_data = load_lenient_json(
                        target_handle.read().encode("utf-8")
                    )
        except (json.JSONDecodeError, OSError):
            return 0

        if loose_file_scope(path) == "books":
            source_map, _preserved, pending = collect_book_json_selection(
                source_data,
                target_data,
                mode,
                {
                    "api": target_file.split("_", 1)[0],
                    "file": target_file,
                    "regex": target_regex,
                },
            )
            total_translatable = len(source_map)
        else:
            pending = collect_lang_keys_to_translate(
                source_data,
                target_data,
                mode,
                target_regex,
            )
            total_translatable = count_translatable_lang_entries(source_data)
        if mode == "skip" and skip_threshold_reached(
            total_translatable,
            len(pending),
        ):
            return 0
        return len(pending)

    def _estimate_snbt(
        self,
        path,
        mode,
        target_regex,
        target_code,
        selected_items=None,
        target_lang=None,
    ) -> int:
        if should_ignore_snbt_source(path):
            return 0

        target_path = get_snbt_target_path(path, target_code)
        separate_target = target_path != path
        if separate_target:
            original_path = path
            current_path = (
                target_path
                if os.path.exists(target_path)
                and (mode != "force" or selected_items is not None)
                else path
            )
        else:
            backup_path = path + ".bak"
            original_path = backup_path if os.path.exists(backup_path) else path
            current_path = original_path if mode == "force" else path

        try:
            with open(original_path, encoding="utf-8") as original_file:
                original_content = original_file.read()
            with open(current_path, encoding="utf-8") as current_file:
                current_content = current_file.read()
        except OSError:
            return 0

        allowed_entry_ids = None
        selected_segments = selected_segments_for_target(
            selected_items,
            path,
            "quests",
        )
        if selected_segments is not None:
            allowed_entry_ids = frozenset(
                entry_id
                for group in collect_quest_groups(path, original_content)
                if group.group_id in selected_segments
                for entry_id in group.entry_ids
            )
            if not allowed_entry_ids:
                return 0

        if separate_target and current_path == target_path:
            current_content = merge_snbt_target(
                original_content,
                current_content,
            )
        language = target_lang or {
            "api": str(target_code).split("_", 1)[0],
            "file": target_code,
            "regex": target_regex,
        }
        selection = collect_snbt_selection_with_baseline(
            original_content,
            current_content,
            mode,
            target_regex,
            same_latin_script=uses_same_latin_script(language),
            allowed_entry_ids=allowed_entry_ids,
        )
        if mode == "skip" and skip_threshold_reached(
            selection.total_translatable,
            len(selection.pending),
        ):
            return 0
        return len(selection.pending)

    def _estimate_bq(
        self,
        path: str,
        mode: str,
        target_regex: str,
        target_lang: dict | None = None,
    ) -> int:
        source_path = (
            resolve_bq_force_baseline(path).source_path
            if mode == "force"
            else path
        )
        try:
            with open(source_path, "r", encoding="utf-8") as source_file:
                data = json.load(source_file)
        except (OSError, json.JSONDecodeError):
            return 0

        original_data = None
        backup = path + ".bak"
        if mode != "force" and os.path.exists(backup):
            try:
                with open(backup, "r", encoding="utf-8") as backup_file:
                    original_data = json.load(backup_file)
            except (OSError, json.JSONDecodeError):
                original_data = None
        language = target_lang or {
            "api": "ru",
            "regex": target_regex,
        }
        selection = collect_bq_selection_with_baseline(
            data,
            mode,
            target_regex,
            original_data=original_data,
            same_latin_script=uses_same_latin_script(language),
        )
        if mode == "skip" and skip_threshold_reached(
            selection.total_translatable,
            len(selection.pending),
        ):
            return 0
        return len(selection.pending)

    def _estimate_heracles(
        self,
        path: str,
        mode: str,
        target_lang: dict,
    ) -> int:
        try:
            with open(path, encoding="utf-8-sig") as handle:
                current_text = handle.read()
            current = self.format_registry.plan(
                path,
                current_text,
                target_lang["file"],
                target_path_hint=path,
            )
            source = current
            backup = path + ".bak"
            if os.path.exists(backup):
                with open(backup, encoding="utf-8-sig") as handle:
                    source_text = handle.read()
                source = self.format_registry.plan(
                    path,
                    source_text,
                    target_lang["file"],
                    target_path_hint=path,
                )
            if mode == "force":
                pending = {unit.id for unit in source.units}
            else:
                _active, pending = source.merge_existing(
                    current,
                    target_lang["regex"],
                )
        except (OSError, ValueError, FormatValidationError):
            return 0
        if mode == "skip" and skip_threshold_reached(
            len(source.units),
            len(pending),
        ):
            return 0
        return len(pending)
