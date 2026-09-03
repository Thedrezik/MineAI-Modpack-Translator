"""Resolve FTB Quest translation keys to their real locale dictionaries."""

from __future__ import annotations

import json
import os
import re
import zipfile
from collections import Counter
from dataclasses import dataclass

from mineai.analysis_items import selected_segments_for_target, target_is_selected
from mineai.engines.base import EngineCallbacks
from mineai.engines.service import TranslationService
from mineai.json_utils import load_lenient_json
from mineai.output.pack_writer import PackWriter
from mineai.processors.discovery import discover_loose_lang_files
from mineai.processors.locale_keys import collect_lang_keys_to_translate
from mineai.processors.loose_paths import (
    loose_pack_target_path,
    loose_target_disk_path,
)
from mineai.processors.quest_groups import collect_quest_groups
from mineai.processors.selection import skip_threshold_reached
from mineai.processors.snbt_extract import build_snbt_document
from mineai.runtime.state import JobState


_REFERENCE_PATTERN = re.compile(r"\{([A-Za-z0-9_-]+[.:][A-Za-z0-9_.-]+)\}")


@dataclass(frozen=True)
class QuestLocaleDependency:
    source_path: str
    target_path: str
    source_entries: dict[str, str]
    existing_entries: dict[str, str]


@dataclass(frozen=True)
class QuestLocalePlan:
    referenced_keys: frozenset[str]
    resolved_keys: frozenset[str]
    missing_keys: frozenset[str]
    dependencies: tuple[QuestLocaleDependency, ...]


class QuestLocaleProcessor:
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
        dependency: QuestLocaleDependency,
        *,
        target_lang: dict,
        mode: str,
        pack_writer: PackWriter,
    ) -> None:
        pending = collect_lang_keys_to_translate(
            dependency.source_entries,
            dependency.existing_entries,
            mode,
            target_lang["regex"],
        )
        if mode == "skip" and skip_threshold_reached(
            len(dependency.source_entries),
            len(pending),
        ):
            pending = {}

        translated: dict[str, str] = {}
        if pending:
            self.callbacks.on_log(
                "⚡ Перевод словаря квестов "
                f"{os.path.basename(dependency.source_path)} — "
                f"{len(pending)} строк",
                "cyan",
            )
            translated = self.service.translate_dict(
                pending,
                target_lang,
                self.callbacks,
                context="Связанный словарь FTB Quests",
                prompt_type="quests",
            )
        if not self.state.should_run():
            return

        merged = dict(dependency.existing_entries)
        merged.update(translated)
        if merged:
            pack_writer.write(
                dependency.target_path,
                json.dumps(
                    merged,
                    ensure_ascii=False,
                    indent=2,
                ).encode("utf-8"),
            )


def translation_reference_key(text: str) -> str | None:
    match = _REFERENCE_PATTERN.fullmatch(text.strip())
    return match.group(1) if match else None


def collect_quest_reference_keys(
    snbt_files: list[str],
    selected_items: frozenset[str] | None = None,
) -> frozenset[str]:
    keys: set[str] = set()
    for path in snbt_files:
        if not target_is_selected(selected_items, path, "quests"):
            continue
        try:
            with open(path, encoding="utf-8-sig") as source_handle:
                content = source_handle.read()
        except OSError:
            continue

        allowed_entry_ids = None
        selected_segments = selected_segments_for_target(
            selected_items,
            path,
            "quests",
        )
        if selected_segments is not None:
            allowed_entry_ids = frozenset(
                entry_id
                for group in collect_quest_groups(path, content)
                if group.group_id in selected_segments
                for entry_id in group.entry_ids
            )

        for node in build_snbt_document(content).nodes:
            if (
                allowed_entry_ids is not None
                and str(node.metadata.get("entry_id")) not in allowed_entry_ids
            ):
                continue
            key = translation_reference_key(node.source)
            if key:
                keys.add(key)
    return frozenset(keys)


def build_quest_locale_plan(
    mc_dir: str,
    snbt_files: list[str],
    target_code: str,
    selected_items: frozenset[str] | None = None,
) -> QuestLocalePlan:
    referenced = collect_quest_reference_keys(snbt_files, selected_items)
    unresolved = set(referenced)
    resolved: set[str] = set()
    dependencies: list[QuestLocaleDependency] = []

    loose_sources = discover_loose_lang_files(mc_dir)
    for source_path in loose_sources:
        if not unresolved or not source_path.casefold().endswith(".json"):
            continue
        target_path = loose_pack_target_path(source_path, mc_dir, target_code)
        if not target_path:
            continue
        try:
            with open(source_path, "rb") as source_handle:
                source_data = load_lenient_json(source_handle.read())
        except (OSError, ValueError):
            continue
        matched = {
            key: value
            for key, value in source_data.items()
            if key in unresolved and isinstance(value, str)
        }
        if not matched:
            continue

        existing: dict[str, str] = {}
        target_disk = loose_target_disk_path(source_path, target_code)
        if os.path.isfile(target_disk):
            try:
                with open(target_disk, "rb") as target_handle:
                    target_data = load_lenient_json(target_handle.read())
                existing = {
                    key: value
                    for key, value in target_data.items()
                    if key in matched and isinstance(value, str)
                }
            except (OSError, ValueError):
                existing = {}

        dependencies.append(
            QuestLocaleDependency(
                source_path=source_path,
                target_path=target_path,
                source_entries=matched,
                existing_entries=existing,
            )
        )
        claimed = set(matched)
        resolved.update(claimed)
        unresolved.difference_update(claimed)

    for source_path in loose_sources:
        if not unresolved or os.path.basename(source_path).casefold() != "en_us.json":
            continue
        matched = _consensus_entries_from_sibling_locales(
            source_path,
            unresolved,
            target_code,
        )
        if not matched:
            continue
        target_path = loose_pack_target_path(source_path, mc_dir, target_code)
        if not target_path:
            continue
        target_disk = loose_target_disk_path(source_path, target_code)
        existing: dict[str, str] = {}
        if os.path.isfile(target_disk):
            try:
                with open(target_disk, "rb") as target_handle:
                    target_data = load_lenient_json(target_handle.read())
                existing = {
                    key: value
                    for key, value in target_data.items()
                    if key in matched and isinstance(value, str)
                }
            except (OSError, ValueError):
                existing = {}
        _merge_dependency(
            dependencies,
            QuestLocaleDependency(
                source_path=source_path,
                target_path=target_path,
                source_entries=matched,
                existing_entries=existing,
            ),
        )
        claimed = set(matched)
        resolved.update(claimed)
        unresolved.difference_update(claimed)

    for archive_path in _quest_locale_archives(mc_dir):
        if not unresolved:
            break
        try:
            with zipfile.ZipFile(archive_path) as archive:
                names = {
                    name.casefold(): name
                    for name in archive.namelist()
                }
                source_names = [
                    actual
                    for folded, actual in names.items()
                    if re.fullmatch(
                        r"assets/[a-z0-9_.-]+/lang/en_us\.json",
                        folded,
                    )
                ]
                for source_name in sorted(source_names, key=str.casefold):
                    try:
                        source_data = load_lenient_json(
                            archive.read(source_name)
                        )
                    except (KeyError, ValueError):
                        continue
                    matched = {
                        key: value
                        for key, value in source_data.items()
                        if key in unresolved and isinstance(value, str)
                    }
                    if not matched:
                        continue
                    target_path = re.sub(
                        r"(?i)en_us\.json$",
                        f"{target_code.casefold()}.json",
                        source_name,
                    )
                    existing: dict[str, str] = {}
                    existing_name = names.get(target_path.casefold())
                    if existing_name:
                        try:
                            target_data = load_lenient_json(
                                archive.read(existing_name)
                            )
                            existing = {
                                key: value
                                for key, value in target_data.items()
                                if key in matched and isinstance(value, str)
                            }
                        except (KeyError, ValueError):
                            existing = {}
                    dependencies.append(
                        QuestLocaleDependency(
                            source_path=f"{archive_path}!{source_name}",
                            target_path=target_path,
                            source_entries=matched,
                            existing_entries=existing,
                        )
                    )
                    claimed = set(matched)
                    resolved.update(claimed)
                    unresolved.difference_update(claimed)
        except (OSError, zipfile.BadZipFile):
            continue

    return QuestLocalePlan(
        referenced_keys=referenced,
        resolved_keys=frozenset(resolved),
        missing_keys=frozenset(unresolved),
        dependencies=tuple(dependencies),
    )


def _quest_locale_archives(mc_dir: str) -> list[str]:
    result: list[str] = []
    for relative_dir, extensions in (
        ("resourcepacks", (".zip",)),
        ("mods", (".jar", ".zip")),
    ):
        directory = os.path.join(mc_dir, relative_dir)
        if not os.path.isdir(directory):
            continue
        try:
            names = sorted(os.listdir(directory), key=str.casefold)
        except OSError:
            continue
        result.extend(
            os.path.join(directory, name)
            for name in names
            if name.casefold().endswith(extensions)
        )
    return result


def _consensus_entries_from_sibling_locales(
    source_path: str,
    unresolved: set[str],
    target_code: str,
) -> dict[str, str]:
    """Recover omissions in en_us only when other locale files agree."""
    candidates: dict[str, list[str]] = {key: [] for key in unresolved}
    directory = os.path.dirname(source_path)
    try:
        names = sorted(os.listdir(directory), key=str.casefold)
    except OSError:
        return {}
    for name in names:
        folded = name.casefold()
        if (
            not re.fullmatch(r"[a-z]{2}_[a-z]{2}\.json", folded)
            or folded in {"en_us.json", f"{target_code.casefold()}.json"}
        ):
            continue
        try:
            with open(os.path.join(directory, name), "rb") as locale_handle:
                locale_data = load_lenient_json(locale_handle.read())
        except (OSError, ValueError):
            continue
        for key in unresolved:
            value = locale_data.get(key)
            if isinstance(value, str) and value.strip():
                candidates[key].append(value)

    recovered: dict[str, str] = {}
    for key, values in candidates.items():
        if not values:
            continue
        value, count = Counter(values).most_common(1)[0]
        if count >= 2:
            recovered[key] = value
    return recovered


def _merge_dependency(
    dependencies: list[QuestLocaleDependency],
    addition: QuestLocaleDependency,
) -> None:
    for index, dependency in enumerate(dependencies):
        if dependency.target_path.casefold() != addition.target_path.casefold():
            continue
        source_entries = dict(dependency.source_entries)
        source_entries.update(addition.source_entries)
        existing_entries = dict(dependency.existing_entries)
        existing_entries.update(addition.existing_entries)
        dependencies[index] = QuestLocaleDependency(
            source_path=dependency.source_path,
            target_path=dependency.target_path,
            source_entries=source_entries,
            existing_entries=existing_entries,
        )
        return
    dependencies.append(addition)
