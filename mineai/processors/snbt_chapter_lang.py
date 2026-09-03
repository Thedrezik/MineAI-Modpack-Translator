"""Extract translatable text from FTB Quests chapter/reward_table SNBT files.

FTB Quests loads translations from ``config/ftbquests/quests/lang/<locale>/``
using ``TranslationManager``.  For each quest object it calls::

    getTranslationManager().getStringTranslation(this, locale, TranslationKey)

The ``TranslationKey`` enum contains: TITLE, QUEST_SUBTITLE, QUEST_DESC,
CHAPTER_SUBTITLE.

Keys in the lang SNBT are formatted as ``<hexId>.<field_name>``, e.g.::

    {
        "1234ABCD5678EF90.title": "My Quest Title",
        "1234ABCD5678EF90.quest_desc": ["Line 1", "Line 2"]
    }

This module detects whether a path is a chapter/reward_table/data SNBT file,
extracts translatable fields per hex-ID entry, and provides helpers to read and
write the accumulated ``ru_ru.snbt`` lang file on disk.
"""
from __future__ import annotations

import os
import re
from typing import Iterator

from mineai.io_utils import atomic_write_text
from mineai.text_processing import (
    is_nontranslatable_value,
    is_technical_term,
    is_translation_key,
)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_CHAPTER_RE = re.compile(
    r"[/\\]ftbquests[/\\]quests[/\\](?:chapters|reward_tables)[/\\][^/\\]+\.snbt$",
    re.IGNORECASE,
)
_DATA_SNBT_RE = re.compile(
    r"[/\\]ftbquests[/\\]quests[/\\]data\.snbt$",
    re.IGNORECASE,
)
_LANG_RE = re.compile(
    r"[/\\]ftbquests[/\\]quests[/\\]lang[/\\]",
    re.IGNORECASE,
)


def is_chapter_or_reward_snbt(file_path: str) -> bool:
    """Return True for chapters/*.snbt, reward_tables/*.snbt and data.snbt."""
    p = "/" + file_path.replace("\\", "/").lstrip("/")
    return bool(_CHAPTER_RE.search(p) or _DATA_SNBT_RE.search(p))


def is_lang_snbt(file_path: str) -> bool:
    """Return True for files inside the lang/ directory of FTB Quests."""
    p = "/" + file_path.replace("\\", "/").lstrip("/")
    return bool(_LANG_RE.search(p))


# ---------------------------------------------------------------------------
# SNBT value parsing helpers
# ---------------------------------------------------------------------------

# Regex for a quoted SNBT string value
_SNBT_STR_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')
# Regex for a 16-char hex object ID declaration:  id: "1A2B3C4D5E6F7890"
_ENTRY_ID_RE = re.compile(r'\bid\s*:\s*"([0-9A-Fa-f]{16})"')
# Fields whose string value maps to TranslationKey.TITLE / QUEST_SUBTITLE
_SINGLE_LANG_FIELDS = frozenset({
    "title",
    "subtitle",
    "quest_subtitle",
})
# Fields whose string-list value maps to TranslationKey.QUEST_DESC / CHAPTER_SUBTITLE
_LIST_LANG_FIELDS = frozenset({
    "description",
    "desc",
    "quest_desc",
    "chapter_subtitle",
    "text",
})
# Additional direct fields from FtbQuestsChapterAdapter (written verbatim, not via lang)
_DIRECT_FIELDS = frozenset({
    "feedback_message",
})
# Canonical field name mapping → TranslationKey names used in lang SNBT keys
_FIELD_LANG_SUFFIX: dict[str, str] = {
    "title": "title",
    "subtitle": "quest_subtitle",
    "quest_subtitle": "quest_subtitle",
    "description": "quest_desc",
    "desc": "quest_desc",
    "quest_desc": "quest_desc",
    "chapter_subtitle": "chapter_subtitle",
    "text": "quest_desc",
    "feedback_message": "title",   # stored as plain string, use title slot
}
# Combined set of all translatable field names
_ALL_LANG_FIELDS = _SINGLE_LANG_FIELDS | _LIST_LANG_FIELDS | _DIRECT_FIELDS


def _unescape_snbt(raw: str) -> str:
    """Un-escape a raw SNBT string value (the content between the quotes)."""
    return (
        raw
        .replace('\\"', '"')
        .replace("\\n", "\n")
        .replace("\\r", "\r")
        .replace("\\t", "\t")
        .replace("\\\\", "\\")
    )


def _escape_snbt(value: str) -> str:
    """Escape a plain string for embedding in a SNBT quoted value."""
    return (
        value
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def _looks_translatable(value: str) -> bool:
    """Return True if the string looks like player-visible text to translate."""
    stripped = value.strip()
    if not stripped:
        return False
    if is_nontranslatable_value(stripped):
        return False
    if is_translation_key(stripped):
        return False
    if is_technical_term(stripped):
        return False
    # Must contain at least one letter (not just numbers/symbols)
    return bool(re.search(r"[A-Za-z]", stripped))


# ---------------------------------------------------------------------------
# Core extraction
# ---------------------------------------------------------------------------

def _iter_entries(content: str) -> Iterator[tuple[str, int, int]]:
    """Yield (hex_id, start, end) for every id: "HEXID" occurrence."""
    for m in _ENTRY_ID_RE.finditer(content):
        yield m.group(1).upper(), m.start(), m.end()


def _find_hex_id_for_position(
    id_positions: list[tuple[str, int, int]],
    field_start: int,
) -> str | None:
    """Return the hex ID of the nearest preceding id: declaration."""
    best: str | None = None
    best_pos = -1
    for hex_id, start, end in id_positions:
        if end <= field_start and start > best_pos:
            best_pos = start
            best = hex_id
    return best


def _parse_snbt_string_list(content: str, bracket_start: int) -> list[str] | None:
    """Parse a SNBT array of strings starting at '['.

    Returns list of unescaped string values, or None on parse error.
    """
    if bracket_start >= len(content) or content[bracket_start] != "[":
        return None
    # Find matching close bracket (simple scan, not full nesting)
    depth = 0
    i = bracket_start
    end = bracket_start
    while i < len(content):
        ch = content[i]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
        i += 1
    else:
        return None

    body = content[bracket_start:end]
    return [
        _unescape_snbt(m.group(1))
        for m in _SNBT_STR_RE.finditer(body)
    ]


def extract_chapter_lang_entries(
    content: str,
    file_path: str = "",
) -> dict[str, str | list[str]]:
    """Parse a chapter/reward_table SNBT and extract lang-compatible entries.

    Returns a dict keyed by ``<HEXID>.<field_suffix>`` with string or
    list-of-strings values (matching FTB Quests TranslationKey format).

    Only text that:
      * belongs to a known translatable field
      * is not a ``{translation.key}`` reference
      * is not a technical term
      * contains at least one letter

    is included.

    For list fields with multiple entries the value is a ``list[str]``.
    For single-string fields the value is a ``str``.
    """
    # Pre-compute all id positions for fast lookup
    id_positions = list(_iter_entries(content))

    entries: dict[str, str | list[str]] = {}

    # Build field-matching regex
    field_alts = "|".join(re.escape(f) for f in _ALL_LANG_FIELDS)
    # Match:  fieldname : "value"   or  fieldname : [ ... ]
    field_re = re.compile(
        rf'(?<![A-Za-z_])(?P<field>{field_alts})(?:")?'
        rf'\s*:\s*'
        rf'(?P<rest>"[^"\\]*(?:\\.[^"\\]*)*"|\[)',
        re.IGNORECASE,
    )

    for m in field_re.finditer(content):
        field_name = m.group("field").casefold()
        field_suffix = _FIELD_LANG_SUFFIX.get(field_name, field_name)
        rest = m.group("rest").strip()

        hex_id = _find_hex_id_for_position(id_positions, m.start())
        if hex_id is None:
            continue

        lang_key = f"{hex_id}.{field_suffix}"

        if rest.startswith('"'):
            # Single string value
            str_m = _SNBT_STR_RE.match(rest)
            if not str_m:
                continue
            value = _unescape_snbt(str_m.group(1))
            if not _looks_translatable(value):
                continue
            # Don't overwrite if we already have a longer list for this key
            if lang_key not in entries or isinstance(entries[lang_key], str):
                entries[lang_key] = value
        elif rest.startswith("["):
            # Array value — find '[' in original content
            bracket_pos = content.find("[", m.start("rest"))
            if bracket_pos < 0:
                continue
            items = _parse_snbt_string_list(content, bracket_pos)
            if items is None:
                continue
            if not any(_looks_translatable(v) for v in items):
                continue
            # Keep the complete list, including renderer macros and empty
            # slots.  FTB Quests addresses description lines by index; compacting
            # the list would move a page break/image onto a different line.
            entries[lang_key] = items

    return entries


# ---------------------------------------------------------------------------
# Lang SNBT file I/O
# ---------------------------------------------------------------------------

def _format_snbt_value(value: str | list[str]) -> str:
    """Format a value as a SNBT string or array of strings."""
    if isinstance(value, list):
        items = ", ".join(f'"{_escape_snbt(v)}"' for v in value)
        return f"[{items}]"
    return f'"{_escape_snbt(value)}"'


def load_lang_snbt(path: str) -> dict[str, str | list[str]]:
    """Read an existing lang/ru_ru.snbt and return its key→value mapping.

    Returns an empty dict if the file does not exist or is malformed.
    """
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
    except OSError:
        return {}
    return _parse_lang_snbt(content)


def _parse_lang_snbt(content: str) -> dict[str, str | list[str]]:
    """Parse a simple ``{ "key": value, ... }`` SNBT compound."""
    result: dict[str, str | list[str]] = {}
    # Key pattern: "some.key": value
    entry_re = re.compile(
        r'"(?P<key>[^"]+)"\s*:\s*(?P<rest>"[^"\\]*(?:\\.[^"\\]*)*"|\[[^\]]*\])',
        re.DOTALL,
    )
    for m in entry_re.finditer(content):
        key = m.group("key")
        rest = m.group("rest").strip()
        if rest.startswith('"'):
            sm = _SNBT_STR_RE.match(rest)
            if sm:
                result[key] = _unescape_snbt(sm.group(1))
        elif rest.startswith("["):
            items = [
                _unescape_snbt(sm.group(1))
                for sm in _SNBT_STR_RE.finditer(rest)
            ]
            result[key] = items
    return result


def dump_lang_snbt(entries: dict[str, str | list[str]]) -> str:
    """Serialise a key→value mapping into ``lang/ru_ru.snbt`` format."""
    if not entries:
        return "{\n}\n"
    lines: list[str] = ["{"]
    items = list(entries.items())
    for i, (key, value) in enumerate(items):
        comma = "," if i < len(items) - 1 else ""
        lines.append(f'\t"{key}": {_format_snbt_value(value)}{comma}')
    lines.append("}")
    return "\n".join(lines) + "\n"


def merge_and_write_lang_snbt(
    target_path: str,
    new_entries: dict[str, str | list[str]],
    *,
    overwrite_existing: bool = False,
) -> None:
    """Merge new_entries into the existing lang SNBT file and write it atomically.

    If ``overwrite_existing`` is False (default), existing translated values
    are kept and only new keys are added.
    """
    existing = load_lang_snbt(target_path)
    if overwrite_existing:
        merged = {**existing, **new_entries}
    else:
        merged = {**new_entries, **existing}  # existing takes priority
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    atomic_write_text(target_path, dump_lang_snbt(merged))
