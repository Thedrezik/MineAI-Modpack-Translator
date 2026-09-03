import copy
from dataclasses import dataclass
from typing import Any

from mineai.formats.document import DocumentPath, StructuredDocument, TextNode
from mineai.formats.markdown import (
    MarkdownSelection,
    collect_book_markdown_selection,
    markdown_structure_mismatch_indices,
    markdown_structure_signature,
    markdown_structures_compatible,
    normalize_markdown_newlines,
    validate_markdown_structure,
)
from mineai.json_utils import (
    apply_translations_by_path,
    iter_translatable_strings,
    key_to_path,
    path_to_key,
)
from mineai.language_validation import translation_needs_repair
from mineai.text_processing import (
    is_technical_term,
    looks_like_source_language,
)


def skip_threshold_reached(total_translatable: int, pending_count: int) -> bool:
    """Return True when at least 90% of translatable entries are complete."""
    if total_translatable <= 0:
        return False
    translated_count = max(0, total_translatable - pending_count)
    return translated_count / total_translatable >= 0.9


def collect_book_json_selection(
    source_data: Any,
    target_data: Any,
    mode: str,
    target_lang: dict | None = None,
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Return filtered source map, preserved target map and pending entries."""
    all_source_map = {
        path_to_key(path): text
        for path, text in iter_translatable_strings(source_data)
        if text.strip()
    }
    target_map = (
        {
            path_to_key(path): text
            for path, text in iter_translatable_strings(target_data)
            if text.strip()
        }
        if target_data
        else {}
    )
    nodes = tuple(
        TextNode(
            key=key,
            path=DocumentPath(key_to_path(key)),
            source=text,
            existing=(
                ""
                if target_lang
                and translation_needs_repair(
                    text,
                    target_map.get(key, ""),
                    target_lang,
                )
                else target_map.get(key, "")
            ),
            translatable=(
                looks_like_source_language(text)
                and not is_technical_term(text)
            ),
            context="json-book",
        )
        for key, text in all_source_map.items()
    )
    document = StructuredDocument(source=source_data, nodes=nodes)
    source_map = {
        node.key: node.source
        for node in document.nodes
        if node.translatable
    }
    preserved = document.preserved(mode)
    pending = document.pending(mode)
    return source_map, preserved, pending


def build_book_json_output(
    source_data: Any,
    preserved: dict[str, str],
    translated: dict[str, str],
) -> Any:
    output = copy.deepcopy(source_data)
    apply_translations_by_path(output, preserved)
    apply_translations_by_path(output, translated)
    return output


@dataclass
class BQSelection:
    properties_key: str | None
    betterquesting_key: str | None
    total_translatable: int
    pending: dict[str, str]
    document: StructuredDocument | None = None


def collect_bq_selection(
    data: dict,
    mode: str,
    target_regex: str,
) -> BQSelection:
    from mineai.text_processing import already_translated

    properties_key = next(
        (key for key in data if key.startswith("properties")),
        None,
    )
    betterquesting_key = None
    fields: dict[str, str] = {}

    if properties_key and isinstance(data.get(properties_key), dict):
        properties = data[properties_key]
        betterquesting_key = next(
            (key for key in properties if key.startswith("betterquesting")),
            None,
        )
        if betterquesting_key and isinstance(
            properties.get(betterquesting_key),
            dict,
        ):
            bq_data = properties[betterquesting_key]
            for prefix in ("name", "desc"):
                actual_key = next(
                    (key for key in bq_data if key.startswith(prefix)),
                    None,
                )
                if actual_key and isinstance(bq_data[actual_key], str):
                    text = bq_data[actual_key].strip()
                    if text:
                        fields[actual_key] = text

    nodes = tuple(
        TextNode(
            key=key,
            path=DocumentPath(
                tuple(
                    part
                    for part in (
                        properties_key,
                        betterquesting_key,
                        key,
                    )
                    if part is not None
                )
            ),
            source=text,
            translatable=not (
                looks_like_source_language(text)
                and is_technical_term(text)
            ),
            context="betterquesting",
        )
        for key, text in fields.items()
    )
    document = StructuredDocument(source=data, nodes=nodes)
    pending = {
        node.key: node.source
        for node in document.nodes
        if node.translatable
        and (
            mode == "force"
            or not already_translated(node.source, target_regex)
        )
    }
    return BQSelection(
        properties_key=properties_key,
        betterquesting_key=betterquesting_key,
        total_translatable=document.total_translatable,
        pending=pending,
        document=document,
    )


@dataclass
class SnbtSelection:
    total_translatable: int
    pending: list[str]
    document: StructuredDocument | None = None
    # Existing non-empty values that failed the structural/content validator.
    # They must be retried even when the ordinary skip threshold is reached.
    repair_pending: tuple[str, ...] = ()


def collect_snbt_selection(
    original_content: str,
    current_content: str,
    mode: str,
    target_regex: str,
    *,
    allowed_entry_ids: set[str] | frozenset[str] | None = None,
    allowed_unit_ids: set[str] | frozenset[str] | None = None,
    target_lang: dict | None = None,
) -> SnbtSelection:
    from mineai.processors.snbt_extract import build_snbt_document

    document = build_snbt_document(
        original_content,
        current_content,
        allowed_entry_ids=allowed_entry_ids,
        allowed_unit_ids=allowed_unit_ids,
    )
    needs_repair = (
        (lambda source, existing: translation_needs_repair(
            source,
            existing,
            target_lang,
        ))
        if target_lang is not None
        else None
    )
    pending = document.pending_source_values(
        mode,
        target_regex,
        same_latin_script=False,
        needs_repair=needs_repair,
    )
    repair_pending = (
        tuple(
            dict.fromkeys(
                node.source
                for node in document.nodes
                if node.translatable
                and node.existing.strip()
                and node.existing.strip() != node.source.strip()
                and needs_repair is not None
                and needs_repair(node.source, node.existing)
            )
        )
        if needs_repair is not None
        else ()
    )
    return SnbtSelection(
        total_translatable=len(document.unique_translatable_sources()),
        pending=list(pending),
        document=document,
        repair_pending=repair_pending,
    )
