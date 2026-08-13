"""Shared completion detection for in-place quest formats.

Russian/CJK targets can still use their distinct-script regex safely. Latin
languages share the source script with English, so their accented-character
regex cannot prove whether a value is translated. For those targets we compare
against MineAI's existing ``.bak`` source baseline instead.
"""

from mineai.processors.selection import (
    BQSelection,
    SnbtSelection,
    collect_bq_selection,
    collect_snbt_selection,
)
from mineai.processors.snbt_extract import build_snbt_baseline_document


def collect_bq_selection_with_baseline(
    data: dict,
    mode: str,
    target_regex: str,
    *,
    original_data: dict | None,
    same_latin_script: bool,
) -> BQSelection:
    if mode == "force" or not same_latin_script or original_data is None:
        return collect_bq_selection(data, mode, target_regex)

    current = collect_bq_selection(data, "force", target_regex)
    original = collect_bq_selection(original_data, "force", target_regex)
    original_fields = original.pending

    pending = {
        key: text
        for key, text in current.pending.items()
        if key not in original_fields or text == original_fields[key]
    }
    return BQSelection(
        properties_key=current.properties_key,
        betterquesting_key=current.betterquesting_key,
        total_translatable=current.total_translatable,
        pending=pending,
        document=current.document,
    )


def collect_snbt_selection_with_baseline(
    original_content: str,
    current_content: str,
    mode: str,
    target_regex: str,
    *,
    same_latin_script: bool,
    allowed_entry_ids: set[str] | frozenset[str] | None = None,
) -> SnbtSelection:
    if mode == "force" or not same_latin_script:
        return collect_snbt_selection(
            original_content,
            current_content,
            mode,
            target_regex,
            allowed_entry_ids=allowed_entry_ids,
        )

    document = build_snbt_baseline_document(
        original_content,
        current_content,
        allowed_entry_ids=allowed_entry_ids,
    )
    pending = document.pending_source_values(
        mode,
        target_regex,
        same_latin_script=True,
    )
    return SnbtSelection(
        total_translatable=len(document.unique_translatable_sources()),
        pending=list(pending),
        document=document,
    )
