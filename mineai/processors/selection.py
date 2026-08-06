import copy
import re
from dataclasses import dataclass
from typing import Any

from mineai.json_utils import (
    apply_translations_by_path,
    iter_translatable_strings,
    path_to_key,
)
from mineai.text_processing import (
    apply_smart_glue,
    is_technical_term,
    looks_like_source_language,
)


YAML_TITLE_RE = re.compile(
    r'^(\s*title\s*:\s*[\'"]?)(.*?)([\'"]?)$',
    re.IGNORECASE,
)


def skip_threshold_reached(total_translatable: int, pending_count: int) -> bool:
    """Return True when at least 90% of translatable entries are complete."""
    if total_translatable <= 0:
        return False
    translated_count = max(0, total_translatable - pending_count)
    return translated_count / total_translatable >= 0.9


def _needs_translation(source: str, existing: str, mode: str) -> bool:
    if mode == "force":
        return True
    return not existing.strip() or existing == source


def collect_book_json_selection(
    source_data: Any,
    target_data: Any,
    mode: str,
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Return filtered source map, preserved target map and pending entries."""
    all_source_map = {
        path_to_key(path): text
        for path, text in iter_translatable_strings(source_data)
        if text.strip()
    }
    source_map = {
        key: text
        for key, text in all_source_map.items()
        if looks_like_source_language(text)
        and not is_technical_term(text)
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

    preserved = {
        key: existing
        for key, existing in target_map.items()
        if key in all_source_map
        and existing != all_source_map[key]
        and mode != "force"
    }
    pending = {
        key: source
        for key, source in source_map.items()
        if _needs_translation(source, target_map.get(key, ""), mode)
    }
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
class MarkdownSelection:
    source_text: str
    lines_out: list[str]
    pending: dict[str, str]
    title_meta: dict[str, tuple[str, str]]
    total_translatable: int


def _extract_yaml_title(line: str) -> tuple[str, str, str] | None:
    match = YAML_TITLE_RE.match(line)
    if not match:
        return None
    return match.group(1), match.group(2), match.group(3)


def collect_book_markdown_selection(
    source_text: str,
    target_text: str,
    mode: str,
    *,
    smart_glue: bool,
) -> MarkdownSelection:
    """Build one shared Markdown/YAML translation plan for estimator and writer."""
    if smart_glue:
        source_text = apply_smart_glue(source_text)

    source_lines = source_text.split("\n")
    target_lines = target_text.split("\n") if target_text else []
    lines_out = list(source_lines)
    pending: dict[str, str] = {}
    title_meta: dict[str, tuple[str, str]] = {}
    total_translatable = 0
    in_yaml = False

    for index, line in enumerate(source_lines):
        stripped = line.strip()
        if stripped == "---":
            in_yaml = not in_yaml
            continue

        existing_line = target_lines[index] if index < len(target_lines) else ""

        if in_yaml:
            if not stripped.lower().startswith("title:"):
                continue
            source_title = _extract_yaml_title(line)
            if not source_title:
                continue
            prefix, title, suffix = source_title
            if (
                not title.strip()
                or not looks_like_source_language(title)
                or is_technical_term(title)
            ):
                continue

            total_translatable += 1
            title_meta[str(index)] = (prefix, suffix)
            existing_title_parts = _extract_yaml_title(existing_line)
            existing_title = (
                existing_title_parts[1] if existing_title_parts else ""
            )
            if _needs_translation(title, existing_title, mode):
                pending[str(index)] = title
            else:
                lines_out[index] = existing_line
            continue

        if stripped.startswith("<") or stripped.startswith("!["):
            continue
        if (
            not stripped
            or not looks_like_source_language(line)
            or is_technical_term(line)
        ):
            continue

        total_translatable += 1
        if _needs_translation(line, existing_line, mode):
            pending[str(index)] = line
        else:
            lines_out[index] = existing_line

    return MarkdownSelection(
        source_text=source_text,
        lines_out=lines_out,
        pending=pending,
        title_meta=title_meta,
        total_translatable=total_translatable,
    )


@dataclass
class BQSelection:
    properties_key: str | None
    betterquesting_key: str | None
    total_translatable: int
    pending: dict[str, str]


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

    pending = {
        key: text
        for key, text in fields.items()
        if mode == "force" or not already_translated(text, target_regex)
    }
    return BQSelection(
        properties_key=properties_key,
        betterquesting_key=betterquesting_key,
        total_translatable=len(fields),
        pending=pending,
    )


@dataclass
class SnbtSelection:
    total_translatable: int
    pending: list[str]


def collect_snbt_selection(
    original_content: str,
    current_content: str,
    mode: str,
    target_regex: str,
) -> SnbtSelection:
    from mineai.processors.snbt_extract import extract_snbt_strings

    original_strings = extract_snbt_strings(original_content)
    pending = (
        original_strings
        if mode == "force"
        else extract_snbt_strings(
            current_content,
            skip_translated_regex=target_regex,
        )
    )
    return SnbtSelection(
        total_translatable=len(original_strings),
        pending=pending,
    )
