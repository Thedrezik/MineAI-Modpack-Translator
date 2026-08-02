"""Harvest conservative Russian terminology from existing locale pairs."""

from __future__ import annotations

import json
import os
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from mineai.glossary import SUPPORTED_LANGUAGE, SUPPORTED_SCHEMA_VERSION, normalize_scope
from mineai.json_utils import load_lenient_json


TECHNICAL_PATTERN = re.compile(
    r"(%[0-9.,]*\$?[a-zA-Z%]|<[^>]+>|\$\([^)]+\)|\[[^]]+\]\([^)]+\)|[{}])"
)
RUSSIAN_PATTERN = re.compile(r"[А-Яа-яЁё]")
SOURCE_WORD_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’+&/-]*")


@dataclass(frozen=True)
class HarvestStats:
    entries: int
    conflicts: int
    pairs_seen: int
    files_scanned: int
    errors: int


class GlossaryHarvester:
    """Collect pairs before translation so the current run cannot self-learn."""

    def __init__(self, language: str = SUPPORTED_LANGUAGE) -> None:
        self.language = language
        self._candidates: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)
        self.pairs_seen = 0
        self.files_scanned = 0
        self.errors = 0

    def collect_from_jars(self, jar_paths: list[str]) -> None:
        target_name = f"{self.language}.json"
        for jar_path in jar_paths:
            try:
                with zipfile.ZipFile(jar_path) as archive:
                    files = {item.filename.lower(): item for item in archive.infolist()}
                    for lower_path, item in files.items():
                        if not re.search(
                            r"(?:^|/)assets/[^/]+/lang/en_us\.json$",
                            lower_path,
                        ):
                            continue
                        target_path = re.sub(r"en_us\.json$", target_name, lower_path)
                        target_item = files.get(target_path)
                        if target_item is None:
                            continue
                        source_data = load_lenient_json(archive.read(item))
                        target_data = load_lenient_json(archive.read(target_item))
                        scope = scope_from_locale_path(item.filename)
                        self._collect_mapping(
                            source_data,
                            target_data,
                            scope,
                            f"{Path(jar_path).name}:{item.filename}",
                        )
                        self.files_scanned += 1
            except (OSError, zipfile.BadZipFile, json.JSONDecodeError):
                self.errors += 1

    def collect_from_loose_json(self, paths: list[str], mc_dir: str) -> None:
        for source_path in paths:
            target_path = re.sub(
                r"en_us\.json$",
                f"{self.language}.json",
                source_path,
                flags=re.IGNORECASE,
            )
            if target_path == source_path or not os.path.exists(target_path):
                continue
            try:
                source_data = _load_file(source_path)
                target_data = _load_file(target_path)
                relative = os.path.relpath(source_path, mc_dir).replace("\\", "/")
                scope = scope_from_locale_path(relative)
                self._collect_mapping(source_data, target_data, scope, relative)
                self.files_scanned += 1
            except (OSError, json.JSONDecodeError):
                self.errors += 1

    def save_generated(self, path: str | Path) -> HarvestStats:
        entries: list[dict] = []
        conflicts: list[dict] = []

        for (_normalized_source, scope), variants in sorted(
            self._candidates.items(),
            key=lambda item: (item[0][1], item[0][0].casefold()),
        ):
            source = _canonical_source(variants)
            if len(variants) == 1:
                target, metadata = next(iter(variants.items()))
                entries.append(
                    {
                        "source": source,
                        "target": target,
                        "scope": [scope],
                        "apply": "exact",
                        "case_sensitive": False,
                        "priority": 50,
                        "occurrences": metadata["count"],
                        "sources": sorted(metadata["sources"]),
                    }
                )
            else:
                conflicts.append(
                    {
                        "source": source,
                        "scope": scope,
                        "variants": [
                            {
                                "target": target,
                                "count": metadata["count"],
                                "sources": sorted(metadata["sources"]),
                            }
                            for target, metadata in sorted(
                                variants.items(),
                                key=lambda item: (-item[1]["count"], item[0].casefold()),
                            )
                        ],
                    }
                )

        payload = {
            "schema_version": SUPPORTED_SCHEMA_VERSION,
            "language": self.language,
            "generated_by": "MineAI Translator smart glossary",
            "entries": entries,
            "conflicts": conflicts,
        }
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = destination.with_name(f".{destination.name}.tmp")
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temp, destination)
        return HarvestStats(
            entries=len(entries),
            conflicts=len(conflicts),
            pairs_seen=self.pairs_seen,
            files_scanned=self.files_scanned,
            errors=self.errors,
        )

    def _collect_mapping(
        self,
        source_data: dict,
        target_data: dict,
        scope: str,
        provenance: str,
    ) -> None:
        if not isinstance(source_data, dict) or not isinstance(target_data, dict):
            return
        for key in source_data.keys() & target_data.keys():
            source = source_data[key]
            target = target_data[key]
            if not isinstance(source, str) or not isinstance(target, str):
                continue
            source = source.strip()
            target = target.strip()
            if not is_safe_candidate(source, target):
                continue
            self.pairs_seen += 1
            candidate_key = (source.casefold(), normalize_scope(scope))
            metadata = self._candidates[candidate_key].setdefault(
                target,
                {"count": 0, "sources": set(), "source_forms": defaultdict(int)},
            )
            metadata["count"] += 1
            metadata["sources"].add(provenance)
            metadata["source_forms"][source] += 1


def is_safe_candidate(source: str, target: str) -> bool:
    if not source or not target or source == target:
        return False
    if len(source) > 80 or "\n" in source or "\r" in source:
        return False
    if not RUSSIAN_PATTERN.search(target):
        return False
    if TECHNICAL_PATTERN.search(source) or TECHNICAL_PATTERN.search(target):
        return False
    words = SOURCE_WORD_PATTERN.findall(source)
    if not 1 <= len(words) <= 5:
        return False
    if not re.search(r"[A-Za-z]", source):
        return False
    if source.rstrip().endswith((".", "!", "?", ":", ";")):
        return False
    return True


def scope_from_locale_path(path: str) -> str:
    normalized = path.replace("\\", "/").lower()
    match = re.search(r"(?:^|/)assets/([^/]+)/", normalized)
    if match:
        return normalize_scope(match.group(1))
    if "ftbquests" in normalized:
        return "ftbquests"
    if "kubejs" in normalized:
        return "kubejs"
    return "unknown"


def _load_file(path: str) -> dict:
    with open(path, "rb") as file_handle:
        return load_lenient_json(file_handle.read())


def _canonical_source(variants: dict[str, dict]) -> str:
    counts: dict[str, int] = defaultdict(int)
    for metadata in variants.values():
        for source, count in metadata["source_forms"].items():
            counts[source] += count
    return min(
        counts,
        key=lambda source: (-counts[source], source.casefold(), source),
    )
