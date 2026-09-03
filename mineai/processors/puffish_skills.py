"""Lossless adapter for Puffish Skills category data in datapacks.

Puffish Skills stores the visible skill names and descriptions together with
the graph itself.  The graph is not a translation document: coordinates,
connections, rewards and resource identifiers must be copied byte-for-byte at
the data level.  This adapter therefore exposes only known display fields as
translation units and renders the result back into the original JSON tree.
"""

from __future__ import annotations

import copy
import json
import os
import re
import zipfile
from collections.abc import Iterator
from typing import Any

from mineai.engines.base import EngineCallbacks
from mineai.engines.service import TranslationService
from mineai.io_utils import atomic_write_bytes
from mineai.json_utils import load_lenient_json, set_at_path
from mineai.language_validation import translation_needs_repair
from mineai.output.pack_writer import PackWriter
from mineai.processors.selection import skip_threshold_reached
from mineai.runtime.state import JobState
from mineai.text_processing import is_nontranslatable_value, is_technical_term
from mineai.formats.document import DocumentPath


# These are the display properties used by Puffish Skills.  Keeping the list
# explicit is important: the same JSON documents also contain IDs, commands,
# attributes, coordinates and connection endpoints.
SKILL_TEXT_KEYS = frozenset(
    {
        "description",
        "display_name",
        "header",
        "heading",
        "hover_text",
        "label",
        "link_text",
        "name",
        "subtitle",
        "text",
        "title",
        "tooltip",
    }
)

_RESOURCE_ID = re.compile(
    r"^[a-z0-9_.-]+:[a-z0-9_./-]+(?:\[[^\r\n]*\])?$",
    re.IGNORECASE,
)
_NUMERIC = re.compile(r"[-+]?\d+(?:[.,]\d+)?(?:\s*[%x×])?$")
_PATH_PREFIXES = (
    "icon",
    "item",
    "texture",
    "frame",
    "reward",
    "rewards",
    "connection",
    "connections",
    "requirement",
    "requirements",
    "attribute",
    "operation",
    "command",
    "definition",
    "parent",
    "id",
    "type",
    "key",
    "path",
    "source",
    "target",
    "metadata",
)
_NEWLINE_TOKEN = re.compile(r"<MINEAI_NL_(\d+)>" )


def _normalise_overlay_member(name: str) -> str | None:
    """Return a safe archive member path or ``None`` for unsafe names."""
    normalized = name.replace("\\", "/").strip("/")
    parts = [part for part in normalized.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        return None
    return "/".join(parts)


def _member_is_target(name: str, target_path: str) -> bool:
    normalized = _normalise_overlay_member(name)
    if not normalized:
        return False
    parts = normalized.split("/")
    folded = [part.casefold() for part in parts]
    try:
        data_index = folded.index("data")
    except ValueError:
        return False
    return "/".join(parts[data_index:]).casefold() == target_path.casefold()


def _source_is_candidate(source_path: str, candidate_path: str) -> bool:
    """Do not read the authoritative datapack source as its own translation."""
    try:
        return os.path.samefile(source_path, candidate_path)
    except (FileNotFoundError, OSError):
        return os.path.realpath(source_path) == os.path.realpath(candidate_path)


def _iter_overlay_directories(mc_dir: str) -> tuple[str, ...]:
    """Return only locations that can contain an installed data overlay."""
    roots = [
        os.path.join(mc_dir, "data"),
        os.path.join(mc_dir, "datapacks"),
        os.path.join(mc_dir, "config", "paxi", "datapacks"),
        os.path.join(mc_dir, "config", "openloader", "data"),
        os.path.join(mc_dir, "kubejs", "data"),
        os.path.join(mc_dir, "MineAI_Datapacks"),
    ]
    saves = os.path.join(mc_dir, "saves")
    try:
        worlds = sorted(os.listdir(saves), key=str.casefold)
    except OSError:
        worlds = []
    for world in worlds:
        roots.append(os.path.join(saves, world, "datapacks"))
    return tuple(dict.fromkeys(roots))


def _load_overlay_json(
    mc_dir: str,
    source_path: str,
    target_path: str,
) -> Any | None:
    """Find the newest valid overlay for one datapack resource.

    Sources are searched without following the source file itself. This is
    deliberately read-only: Paxi/KubeJS/OpenLoader inputs remain untouched,
    while a prior MineAI/world datapack can be reused on an incremental run.
    """
    candidates: list[tuple[float, str, bytes]] = []
    for root in _iter_overlay_directories(mc_dir):
        if not os.path.isdir(root):
            continue
        for current, directories, files in os.walk(root):
            directories[:] = [
                name
                for name in directories
                if name.casefold() not in {"mods", "resourcepacks", "saves"}
            ]
            for name in files:
                path = os.path.join(current, name)
                if name.casefold().endswith(".json"):
                    relative = os.path.relpath(path, root).replace("\\", "/")
                    if os.path.basename(root).casefold() == "data":
                        relative = "data/" + relative
                    if (
                        _member_is_target(relative, target_path)
                        and not _source_is_candidate(source_path, path)
                    ):
                        try:
                            with open(path, "rb") as handle:
                                payload = handle.read()
                            load_lenient_json(payload)
                            candidates.append(
                                (os.path.getmtime(path), path, payload)
                            )
                        except (OSError, ValueError):
                            continue
                elif name.casefold().endswith(".zip"):
                    try:
                        with zipfile.ZipFile(path) as archive:
                            matching = next(
                                (
                                    member
                                    for member in archive.namelist()
                                    if _member_is_target(member, target_path)
                                ),
                                None,
                            )
                            if matching is None:
                                continue
                            payload = archive.read(matching)
                            load_lenient_json(payload)
                        candidates.append((os.path.getmtime(path), path, payload))
                    except (OSError, ValueError, zipfile.BadZipFile, KeyError):
                        continue
    if not candidates:
        return None
    _, _, payload = max(
        candidates,
        key=lambda item: (item[0], item[1].casefold()),
    )
    try:
        return load_lenient_json(payload)
    except ValueError:
        return None


def _path_key(path: tuple[str | int, ...]) -> str:
    return "json:/" + DocumentPath(path).encode()


def _path_from_key(key: str) -> tuple[str | int, ...]:
    if key.startswith("json:"):
        key = key[5:]
    key = key.lstrip("/")
    return DocumentPath.decode(key).parts


def _looks_like_display_text(key: object, value: str, path: tuple[str | int, ...]) -> bool:
    if not isinstance(key, str) or key.casefold() not in SKILL_TEXT_KEYS:
        return False
    if not isinstance(value, str) or not value.strip():
        return False
    parent_keys = {
        str(part).casefold()
        for part in path[:-1]
        if isinstance(part, str)
    }
    if parent_keys.intersection(_PATH_PREFIXES):
        return False
    stripped = value.strip()
    if _NUMERIC.fullmatch(stripped) or _RESOURCE_ID.fullmatch(stripped):
        return False
    # A formatting-only value is a renderer instruction, not user-facing text.
    if is_nontranslatable_value(stripped) or is_technical_term(stripped):
        return False
    return bool(re.search(r"[A-Za-zА-Яа-яЁё]", stripped))


def _iter_skill_units(
    value: Any,
    path: tuple[str | int, ...] = (),
) -> Iterator[tuple[str, str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = path + (key,)
            if _looks_like_display_text(key, child, child_path):
                yield _path_key(child_path), child
            elif isinstance(child, (dict, list)):
                yield from _iter_skill_units(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_path = path + (index,)
            if isinstance(child, (dict, list)):
                yield from _iter_skill_units(child, child_path)


def extract_skill_units(data: Any) -> dict[str, str]:
    """Return deterministic ``json:/...`` IDs for visible skill text only."""
    return dict(_iter_skill_units(data))


def _extract_existing_units(
    data: Any,
    source_units: dict[str, str],
) -> dict[str, str]:
    """Read candidate values at source display paths without language filters."""
    if not isinstance(data, (dict, list)):
        return {}
    result: dict[str, str] = {}
    for key in source_units:
        current = data
        try:
            for part in _path_from_key(key):
                current = current[part]
        except (KeyError, IndexError, TypeError):
            continue
        if isinstance(current, str):
            result[key] = current
    return result


def apply_skill_translations(
    source: Any,
    translations: dict[str, str],
) -> Any:
    """Copy *source* and apply selected display translations by JSON path."""
    output = copy.deepcopy(source)
    for key, translated in translations.items():
        if not isinstance(translated, str):
            continue
        path = _path_from_key(key)
        if not path:
            continue
        # Only replace fields the adapter itself exposes.  This prevents a
        # malformed/stale cache entry from changing a technical graph value.
        parent = path[-1]
        current = output
        try:
            for part in path[:-1]:
                current = current[part]
            original = current[parent]
        except (KeyError, IndexError, TypeError):
            continue
        if _looks_like_display_text(parent, original, path):
            set_at_path(output, path, translated)
    return output


def _protect_line_breaks(text: str) -> tuple[str, dict[str, str]]:
    """Represent newlines as protected tags while the generic service runs."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    replacements: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        token = f"<MINEAI_NL_{len(replacements)}>"
        replacements[token] = match.group(0)
        return token

    return re.sub(r"\n", replace, normalized), replacements


def _restore_line_breaks(text: str, replacements: dict[str, str]) -> str:
    for token, newline in replacements.items():
        text = text.replace(token, newline)
    return text


def skill_datapack_target_path(file_path: str, mc_dir: str) -> str:
    """Map a source JSON below a datapack's ``data/`` directory to its target."""
    relative = os.path.relpath(file_path, mc_dir).replace("\\", "/")
    parts = [part for part in relative.strip("/").split("/") if part]
    folded = [part.casefold() for part in parts]
    try:
        data_index = folded.index("data")
    except ValueError as exc:
        raise ValueError(f"Puffish Skills source is outside data/: {file_path}") from exc
    target = parts[data_index:]
    if len(target) < 4 or target[2].casefold() != "puffish_skills":
        raise ValueError(f"Unsupported Puffish Skills path: {file_path}")
    if any(part in {".", ".."} for part in target) or not target[-1].casefold().endswith(".json"):
        raise ValueError(f"Unsafe Puffish Skills path: {file_path}")
    return "/".join(target)


def count_skill_units(file_path: str) -> int:
    try:
        with open(file_path, "rb") as handle:
            data = load_lenient_json(handle.read())
    except (OSError, ValueError):
        return 0
    return len(extract_skill_units(data))


def count_translated_skill_units(
    file_path: str,
    mc_dir: str,
    target_lang: dict,
) -> int:
    """Count valid translated display fields in an installed overlay."""
    try:
        with open(file_path, "rb") as handle:
            source = load_lenient_json(handle.read())
        target_path = skill_datapack_target_path(file_path, mc_dir)
    except (OSError, ValueError):
        return 0
    source_units = extract_skill_units(source)
    existing = _load_overlay_json(mc_dir, file_path, target_path)
    existing_units = _extract_existing_units(existing, source_units)
    translated = 0
    for key, original in source_units.items():
        candidate = existing_units.get(key)
        if not isinstance(candidate, str):
            continue
        try:
            if not translation_needs_repair(original, candidate, target_lang):
                translated += 1
        except (TypeError, ValueError):
            continue
    return translated


def _selected_unit_ids(
    selected_units: dict[str, frozenset[str]] | None,
    logical_path: str,
) -> frozenset[str] | None:
    if selected_units is None:
        return None
    normalized = logical_path.replace("\\", "/").casefold()
    for path, unit_ids in selected_units.items():
        if path.replace("\\", "/").casefold() == normalized:
            return frozenset(unit_ids)
    return frozenset()


class PuffishSkillsProcessor:
    """Translate Puffish Skills display fields into a datapack overlay."""

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
        mc_dir: str,
        *,
        target_lang: dict,
        mode: str,
        output_mode: str,
        pack_writer: PackWriter | None,
        selected_units: dict[str, frozenset[str]] | None = None,
        retranslate_selected: bool = False,
    ) -> str | None:
        logical_path = os.path.relpath(file_path, mc_dir).replace("\\", "/")
        selected_ids = _selected_unit_ids(selected_units, logical_path)
        if selected_units is not None and selected_ids is None:
            return None
        try:
            with open(file_path, "rb") as handle:
                source = load_lenient_json(handle.read())
            target_path = skill_datapack_target_path(file_path, mc_dir)
        except (OSError, ValueError):
            return None

        source_units = extract_skill_units(source)
        if not source_units:
            return None

        # The source is always the authoritative skeleton.  A previous MineAI
        # or world datapack may provide translated display values, but it is
        # never used to replace graph data.  Invalid/partial values are
        # discarded and sent for translation again.
        existing_data = _load_overlay_json(mc_dir, file_path, target_path)
        existing_units = (
            _extract_existing_units(existing_data, source_units)
            if isinstance(existing_data, (dict, list))
            else {}
        )
        preserved: dict[str, str] = {}
        if mode != "force":
            for key, original in source_units.items():
                candidate = existing_units.get(key)
                if not isinstance(candidate, str):
                    continue
                try:
                    valid = not translation_needs_repair(
                        original,
                        candidate,
                        target_lang,
                    )
                except (TypeError, ValueError):
                    valid = False
                if valid:
                    preserved[key] = candidate

        pending = {
            key: value
            for key, value in source_units.items()
            if key not in preserved
            or (retranslate_selected and selected_ids is not None and key in selected_ids)
        }
        if selected_ids is not None:
            pending = {
                key: value
                for key, value in pending.items()
                if key in selected_ids
            }
        if mode == "skip" and skip_threshold_reached(len(source_units), len(pending)):
            return None

        protected_values: dict[str, dict[str, str]] = {}
        service_input: dict[str, str] = {}
        for key, value in pending.items():
            prepared, replacements = _protect_line_breaks(value)
            service_input[key] = prepared
            if replacements:
                protected_values[key] = replacements

        translated: dict[str, str] = {}
        if service_input:
            self.callbacks.on_log(
                f"⚡ Перевод навыков [{os.path.basename(file_path)}] — "
                f"{len(service_input)} блоков",
                "cyan",
            )
            translated = self.service.translate_dict(
                service_input,
                target_lang,
                self.callbacks,
                context=f"Puffish Skills | {logical_path}",
                prompt_type="quests",
                cache_contexts={
                    key: f"puffish-skills|{logical_path}|{key}"
                    for key in service_input
                },
            )
        if not self.state.should_run():
            return None

        for key, replacements in protected_values.items():
            if key in translated:
                translated[key] = _restore_line_breaks(
                    translated[key],
                    replacements,
                )
        merged = dict(preserved)
        merged.update(translated)
        result = apply_skill_translations(source, merged)
        payload = json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")

        if pack_writer is not None:
            pack_writer.write(target_path, payload)
            return None
        if output_mode == "inplace":
            # Only a real datapack root can be safely written without the
            # PackWriter.  Paxi/KubeJS sources never reach this branch in the
            # normal job because run_translation creates a datapack writer.
            disk_path = os.path.join(mc_dir, *target_path.split("/"))
            atomic_write_bytes(disk_path, payload)
            return disk_path
        return None
