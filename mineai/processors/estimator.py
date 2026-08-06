import json
import os
import re
import zipfile

from mineai.constants import (
    BOOK_PATH_MARKERS,
    MD_PATH_MARKERS,
    RESEARCH_PATH_MARKERS,
)
from mineai.json_utils import load_lenient_json
from mineai.processors.locale_keys import (
    collect_lang_keys_to_translate,
    count_translatable_lang_entries,
)
from mineai.processors.selection import (
    collect_book_json_selection,
    collect_book_markdown_selection,
    collect_bq_selection,
    collect_snbt_selection,
    skip_threshold_reached,
)
from mineai.processors.snbt import (
    get_snbt_target_path,
    should_ignore_snbt_source,
)
from mineai.runtime.state import JobState


class StringEstimator:
    def __init__(self, state: JobState) -> None:
        self.state = state

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
    ) -> int:
        total = 0
        target_file = f"{target_lang['file']}.json"
        target_regex = target_lang["regex"]

        for path in jar_files:
            if not self.state.should_run():
                return total
            self.state.wait_if_paused()
            total += self._estimate_jar(
                path,
                target_file,
                target_lang,
                mode,
                translate_mods,
                translate_books,
                smart_glue,
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
                )

            for path in bq_files:
                if not self.state.should_run():
                    return total
                self.state.wait_if_paused()
                total += self._estimate_bq(path, mode, target_regex)

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
    ) -> int:
        count = 0
        try:
            with zipfile.ZipFile(path, "r") as archive:
                locale = {
                    item.filename.lower(): item
                    for item in archive.infolist()
                    if target_file in item.filename.lower()
                    or f"/{target_lang['file']}/" in item.filename.lower()
                }
                for item in archive.infolist():
                    file_lower = item.filename.lower()
                    is_book_json = (
                        file_lower.endswith(".json")
                        and "/en_us/" in file_lower
                        and (
                            any(
                                marker in file_lower
                                for marker in BOOK_PATH_MARKERS
                            )
                            or any(
                                marker in file_lower
                                for marker in RESEARCH_PATH_MARKERS
                            )
                        )
                    )
                    is_book_md = (
                        (
                            file_lower.endswith(".md")
                            or file_lower.endswith(".txt")
                        )
                        and "/en_us/" in file_lower
                        and any(
                            marker in file_lower
                            for marker in MD_PATH_MARKERS
                        )
                    )
                    is_lang = (
                        file_lower.endswith("en_us.json")
                        and not is_book_json
                    )

                    if translate_mods and is_lang:
                        count += self._count_lang(
                            archive,
                            item,
                            locale,
                            target_file,
                            mode,
                            target_lang["regex"],
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
    ) -> int:
        try:
            source_text = archive.read(item).decode(
                "utf-8-sig",
                errors="ignore",
            )
        except OSError:
            return 0

        target_path = re.sub(
            r"/en_us/",
            f"/{target_lang['file']}/",
            item.filename,
            flags=re.IGNORECASE,
        )
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

        selection = collect_book_markdown_selection(
            source_text,
            target_text,
            mode,
            smart_glue=smart_glue,
        )
        if mode == "skip" and skip_threshold_reached(
            selection.total_translatable,
            len(selection.pending),
        ):
            return 0
        return len(selection.pending)

    def _estimate_loose(
        self,
        path,
        target_file,
        mode,
        target_regex,
    ) -> int:
        try:
            with open(path, encoding="utf-8") as source_file:
                source_data = load_lenient_json(
                    source_file.read().encode("utf-8")
                )
            target_path = path.replace("en_us.json", target_file)
            target_data = {}
            if os.path.exists(target_path):
                with open(target_path, encoding="utf-8") as target_handle:
                    target_data = load_lenient_json(
                        target_handle.read().encode("utf-8")
                    )
        except (json.JSONDecodeError, OSError):
            return 0

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
    ) -> int:
        if should_ignore_snbt_source(path):
            return 0

        target_path = get_snbt_target_path(path, target_code)
        separate_target = target_path != path
        if (
            separate_target
            and mode in ("append", "skip")
            and os.path.exists(target_path)
        ):
            return 0

        if separate_target:
            original_path = path
            current_path = path
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

        selection = collect_snbt_selection(
            original_content,
            current_content,
            mode,
            target_regex,
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
    ) -> int:
        backup = path + ".bak"
        source_path = (
            backup
            if mode == "force" and os.path.exists(backup)
            else path
        )
        try:
            with open(source_path, "r", encoding="utf-8") as source_file:
                data = json.load(source_file)
        except (OSError, json.JSONDecodeError):
            return 0

        selection = collect_bq_selection(data, mode, target_regex)
        if mode == "skip" and skip_threshold_reached(
            selection.total_translatable,
            len(selection.pending),
        ):
            return 0
        return len(selection.pending)
