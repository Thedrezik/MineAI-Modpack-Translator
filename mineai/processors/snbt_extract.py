import re

from bisect import bisect_right

from mineai.formats.document import DocumentPath, StructuredDocument, TextNode
from mineai.text_processing import (
    is_nontranslatable_value,
    is_technical_term,
    is_translation_key,
    looks_like_source_language,
)


SNBT_VALUE_RE = r'(?:[^"\\]|\\.)*'
SNBT_STRING_RE = rf'"({SNBT_VALUE_RE})"'
SINGLE_FIELDS = (
    "title",
    "subtitle",
    "text",
    "desc",
    "description",
    "quest_subtitle",
    "quest_desc",
    "chapter_subtitle",
    "item_name",
    "flavor_text",
    "name",
)
ARRAY_FIELDS = (
    "description",
    "text",
    "desc",
    "quest_desc",
    "chapter_subtitle",
)
KEY_START = r'(?<![\w])(?:"|)'
_ENTRY_ID_PATTERN = re.compile(
    r'(?:\bid\s*:\s*"([0-9a-f]{16})"|[a-z_]+\.([0-9a-f]{16})\.)',
    re.IGNORECASE,
)


def _entry_id_index(content: str) -> tuple[tuple[int, str], ...]:
    return tuple(
        (
            match.start(),
            (match.group(1) or match.group(2)).upper(),
        )
        for match in _ENTRY_ID_PATTERN.finditer(content)
    )


def _entry_id_before(
    content: str,
    position: int,
    id_positions: tuple[tuple[int, str], ...] | None = None,
) -> str | None:
    """Return the nearest quest/object ID before a text slot.

    FTB Quests chapter files use ``id: \"<hex>\"`` on a line of its own,
    while language catalogs use ``quest.<hex>.<field>``.  Looking only at the
    current line (the old behaviour) assigned multiline chapter fields to
    ``@root`` and made unit-level preview selection impossible.
    """
    id_positions = id_positions or _entry_id_index(content)
    if not id_positions:
        return None
    index = bisect_right(id_positions, (position, "\uffff")) - 1
    return id_positions[index][1] if index >= 0 else None


def _escape_snbt_value(value: str) -> str:
    """Escape new quotes without double-escaping existing SNBT sequences."""
    escaped: list[str] = []
    i = 0
    while i < len(value):
        char = value[i]
        if char == "\\":
            if i + 1 < len(value):
                escaped.append(value[i : i + 2])
                i += 2
                continue
            escaped.append("\\\\")
        elif char == '"':
            escaped.append('\\"')
        elif char == "\n":
            escaped.append("\\n")
        elif char == "\r":
            escaped.append("\\r")
        elif char == "\t":
            escaped.append("\\t")
        else:
            escaped.append(char)
        i += 1
    return "".join(escaped)


def _is_selected_text(value: str, skip_translated_regex: str | None) -> bool:
    return bool(
        value.strip()
        and not is_nontranslatable_value(value)
        and not is_translation_key(value)
        and looks_like_source_language(value)
        and not (
            skip_translated_regex
            and re.search(skip_translated_regex, value)
        )
    )


def _iter_all_snbt_slots(content: str):
    """Yield stable source spans for every supported textual SNBT field."""
    id_positions = _entry_id_index(content)
    ordinals: dict[tuple[str, str, str], int] = {}
    field_pattern = "|".join(SINGLE_FIELDS)
    single_pattern = re.compile(
        rf'(?P<prefix>{KEY_START}(?P<field>{field_pattern})(?:"|)\s*:\s*")'
        rf'(?P<value>{SNBT_VALUE_RE})(?P<suffix>")',
        re.IGNORECASE,
    )
    for match in single_pattern.finditer(content):
        entry_id = _entry_id_before(content, match.start(), id_positions) or "@root"
        field = match.group("field").casefold()
        ordinal_key = (entry_id, "single", field)
        ordinal = ordinals.get(ordinal_key, 0)
        ordinals[ordinal_key] = ordinal + 1
        path = DocumentPath(("snbt", entry_id, "single", field, ordinal))
        yield path, match.group("value"), match.start("value"), match.end("value"), entry_id

    array_fields = "|".join(ARRAY_FIELDS)
    array_pattern = re.compile(
        rf'(?P<prefix>{KEY_START}(?P<field>{array_fields})(?:"|)\s*:\s*)'
        rf'(?P<body>\[\s*(?:"(?:[^"\\]|\\.)*"\s*,?\s*)*\])',
        re.IGNORECASE,
    )
    for block in array_pattern.finditer(content):
        entry_id = _entry_id_before(content, block.start(), id_positions) or "@root"
        field = block.group("field").casefold()
        ordinal_key = (entry_id, "array", field)
        for string_match in re.finditer(SNBT_STRING_RE, block.group("body")):
            ordinal = ordinals.get(ordinal_key, 0)
            ordinals[ordinal_key] = ordinal + 1
            start = block.start("body") + string_match.start(1)
            end = block.start("body") + string_match.end(1)
            path = DocumentPath(("snbt", entry_id, "array", field, ordinal))
            yield path, string_match.group(1), start, end, entry_id


def build_snbt_document(
    source_content: str,
    target_content: str = "",
    *,
    allowed_entry_ids: set[str] | frozenset[str] | None = None,
    allowed_unit_ids: set[str] | frozenset[str] | None = None,
) -> StructuredDocument:
    """Parse SNBT into the common AST and retain an exact source skeleton."""
    target_by_path = {
        path: value
        for path, value, _start, _end, _entry_id in _iter_all_snbt_slots(
            target_content
        )
    }
    nodes: list[TextNode] = []
    for path, value, start, end, entry_id in _iter_all_snbt_slots(source_content):
        selected = (
            (allowed_entry_ids is None or entry_id in allowed_entry_ids)
            and (allowed_unit_ids is None or path.encode() in allowed_unit_ids)
        )
        nodes.append(
            TextNode(
                key=path.encode(),
                path=path,
                source=value,
                existing=target_by_path.get(path, ""),
                translatable=(
                    selected
                    and _is_selected_text(value, None)
                    and not is_technical_term(value)
                ),
                context="snbt",
                metadata={"start": start, "end": end, "entry_id": entry_id},
            )
        )

    def render(translations: dict[str, str]) -> str:
        replacements: list[tuple[int, int, str]] = []
        for node in nodes:
            value = translations.get(node.key)
            if value is None:
                value = node.existing if node.existing else node.source
            replacements.append(
                (
                    int(node.metadata["start"]),
                    int(node.metadata["end"]),
                    _escape_snbt_value(value),
                )
            )
        chunks: list[str] = []
        cursor = 0
        for start, end, value in sorted(replacements):
            chunks.append(source_content[cursor:start])
            chunks.append(value)
            cursor = end
        chunks.append(source_content[cursor:])
        return "".join(chunks)

    return StructuredDocument(
        source=source_content,
        nodes=tuple(nodes),
        renderer=render,
    )


def merge_snbt_target(source_content: str, target_content: str) -> str:
    """Overlay translations on the current source structure without data loss."""
    return build_snbt_document(source_content, target_content).render({})


def build_snbt_baseline_document(
    baseline_content: str,
    current_content: str,
    *,
    allowed_entry_ids: set[str] | frozenset[str] | None = None,
    allowed_unit_ids: set[str] | frozenset[str] | None = None,
) -> StructuredDocument:
    """Use current structure while retaining old source values as a baseline."""
    baseline_by_path = {
        path: value
        for path, value, _start, _end, _entry_id in _iter_all_snbt_slots(
            baseline_content
        )
    }
    nodes: list[TextNode] = []
    for path, current, start, end, entry_id in _iter_all_snbt_slots(current_content):
        source = baseline_by_path.get(path, current)
        selected = (
            (allowed_entry_ids is None or entry_id in allowed_entry_ids)
            and (allowed_unit_ids is None or path.encode() in allowed_unit_ids)
        )
        nodes.append(
            TextNode(
                key=path.encode(),
                path=path,
                source=source,
                existing=current if path in baseline_by_path else "",
                translatable=(
                    selected
                    and _is_selected_text(source, None)
                    and not is_technical_term(source)
                ),
                context="snbt-baseline",
                metadata={"start": start, "end": end, "entry_id": entry_id},
            )
        )
    return StructuredDocument(source=current_content, nodes=tuple(nodes))


def extract_snbt_strings_by_entry(content: str) -> dict[str, tuple[str, ...]]:
    """Build a single-pass index used to split large FTB language files."""
    values: dict[str, list[str]] = {}
    document = build_snbt_document(content)
    for node in document.nodes:
        if not node.translatable:
            continue
        entry_id = str(node.metadata["entry_id"])
        if entry_id == "@root":
            continue
        value = node.source
        bucket = values.setdefault(entry_id, [])
        if value not in bucket:
            bucket.append(value)
    return {entry_id: tuple(bucket) for entry_id, bucket in values.items()}


def extract_snbt_strings(
    content: str,
    *,
    skip_translated_regex: str | None = None,
    allowed_entry_ids: set[str] | frozenset[str] | None = None,
) -> list[str]:
    document = build_snbt_document(
        content,
        allowed_entry_ids=allowed_entry_ids,
    )
    strings = [
        node.source
        for node in document.nodes
        if node.translatable
        and not (
            skip_translated_regex
            and re.search(skip_translated_regex, node.source)
        )
    ]
    return list(dict.fromkeys(strings))


def apply_snbt_translations(
    content: str,
    mapping: dict[str, str],
    *,
    target_content: str = "",
    allowed_entry_ids: set[str] | frozenset[str] | None = None,
    allowed_unit_ids: set[str] | frozenset[str] | None = None,
) -> str:
    document = build_snbt_document(
        content,
        target_content,
        allowed_entry_ids=allowed_entry_ids,
        allowed_unit_ids=allowed_unit_ids,
    )
    translations = {
        node.key: mapping.get(
            node.source,
            node.existing if node.existing else node.source,
        )
        for node in document.nodes
        if node.translatable
    }
    return document.render(translations)
