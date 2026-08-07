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
from mineai.processors.snbt_extract import extract_snbt_strings


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
    )


def collect_snbt_selection_with_baseline(
    original_content: str,
    current_content: str,
    mode: str,
    target_regex: str,
    *,
    same_latin_script: bool,
) -> SnbtSelection:
    if mode == "force" or not same_latin_script:
        return collect_snbt_selection(
            original_content,
            current_content,
            mode,
            target_regex,
        )

    original_strings = extract_snbt_strings(original_content)
    current_strings = extract_snbt_strings(current_content)

    # The backup and current file keep the same field order during MineAI's own
    # in-place edits. An unchanged value is still source text; a changed value is
    # a previous translation. Values appended after the backup was created are
    # new source entries and must also be translated.
    pending = [
        text
        for index, text in enumerate(current_strings)
        if index >= len(original_strings) or text == original_strings[index]
    ]
    return SnbtSelection(
        total_translatable=len(current_strings),
        pending=list(dict.fromkeys(pending)),
    )
