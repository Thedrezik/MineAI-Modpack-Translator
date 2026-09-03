"""Logical groups for large monolithic FTB Quests language files."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from mineai.processors.snbt_extract import extract_snbt_strings_by_entry


ENTRY_RE = re.compile(
    r"^\s*[a-z_]+\.([0-9a-f]{16})\.[^:\s]+\s*:",
    re.IGNORECASE | re.MULTILINE,
)
HEX_ID_RE = re.compile(r'\bid\s*:\s*"([0-9a-f]{16})"', re.IGNORECASE)
CHAPTER_TITLE_RE = re.compile(
    r'^\s*chapter\.([0-9a-f]{16})\.title\s*:\s*"((?:[^"\\]|\\.)*)"',
    re.IGNORECASE | re.MULTILINE,
)
MAX_GROUP_STRINGS = 500


@dataclass(frozen=True)
class QuestGroup:
    group_id: str
    name: str
    entry_ids: frozenset[str]
    total: int
    strings: tuple[str, ...]


def _normalized_ids(values) -> frozenset[str]:
    return frozenset(value.upper() for value in values)


def _chapter_titles(content: str) -> dict[str, str]:
    return {
        match.group(1).upper(): match.group(2).replace(r'\"', '"')
        for match in CHAPTER_TITLE_RE.finditer(content)
    }


def _split_group(
    group_id: str,
    name: str,
    entry_ids: frozenset[str],
    ordered_all_ids: list[str],
    strings_by_id: dict[str, tuple[str, ...]],
) -> list[QuestGroup]:
    ordered_ids = [
        entry_id
        for entry_id in ordered_all_ids
        if entry_id in entry_ids
    ]
    parts: list[QuestGroup] = []
    part_ids: list[str] = []
    part_strings: list[str] = []

    def flush() -> None:
        nonlocal part_ids, part_strings
        if not part_ids:
            return
        part_number = len(parts) + 1
        parts.append(
            QuestGroup(
                group_id=(group_id if not parts and len(ordered_ids) == len(part_ids) else f"{group_id}:part:{part_number}"),
                name=name,
                entry_ids=frozenset(part_ids),
                total=len(part_strings),
                strings=tuple(part_strings),
            )
        )
        part_ids = []
        part_strings = []

    for entry_id in ordered_ids:
        values = strings_by_id.get(entry_id, ())
        new_values = [value for value in values if value not in part_strings]
        if part_ids and len(part_strings) + len(new_values) > MAX_GROUP_STRINGS:
            flush()
            new_values = list(values)
        part_ids.append(entry_id)
        part_strings.extend(value for value in new_values if value not in part_strings)
    flush()

    if len(parts) <= 1:
        return parts
    return [
        QuestGroup(
            group_id=part.group_id,
            name=f"{name} — part {index}/{len(parts)}",
            entry_ids=part.entry_ids,
            total=part.total,
            strings=part.strings,
        )
        for index, part in enumerate(parts, 1)
    ]


def collect_quest_groups(file_path: str, content: str) -> list[QuestGroup]:
    """Group a monolithic ``lang/en_us.snbt`` by real chapter membership."""
    path = Path(file_path)
    if path.name.casefold() != "en_us.snbt" or path.parent.name.casefold() != "lang":
        return []

    all_ids = _normalized_ids(match.group(1) for match in ENTRY_RE.finditer(content))
    if not all_ids:
        return []

    chapter_dir = path.parent.parent / "chapters"
    titles = _chapter_titles(content)
    ordered_all_ids = list(
        dict.fromkeys(match.group(1).upper() for match in ENTRY_RE.finditer(content))
    )
    strings_by_id = extract_snbt_strings_by_entry(content)
    assigned: set[str] = set()
    groups: list[QuestGroup] = []

    if chapter_dir.is_dir():
        for chapter_path in sorted(chapter_dir.glob("*.snbt")):
            try:
                chapter_content = chapter_path.read_text(encoding="utf-8-sig")
            except OSError:
                continue
            member_ids = (
                _normalized_ids(HEX_ID_RE.findall(chapter_content))
                & all_ids
                - assigned
            )
            if not member_ids:
                continue
            chapter_id = next(
                (entry_id for entry_id in member_ids if entry_id in titles),
                None,
            )
            label = titles.get(chapter_id or "", chapter_path.stem.replace("_", " ").title())
            stable_id = f"chapter:{chapter_id or chapter_path.stem.casefold()}"
            groups.extend(
                _split_group(
                    stable_id,
                    label,
                    member_ids,
                    ordered_all_ids,
                    strings_by_id,
                )
            )
            assigned.update(member_ids)

    remaining = all_ids - assigned
    if remaining:
        groups.extend(
            _split_group(
                "other",
                "Other entries",
                remaining,
                ordered_all_ids,
                strings_by_id,
            )
        )
    return groups
