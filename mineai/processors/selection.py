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
    PLACEHOLDER_PATTERN,
    is_technical_term,
    looks_like_source_language,
    mask_protected_fragments,
)


YAML_TITLE_RE = re.compile(
    r'^(\s*title\s*:\s*[\'\"]?)(.*?)([\'\"]?)$',
    re.IGNORECASE,
)
_MARKDOWN_BLOCK_MARKER_RE = re.compile(
    r"^(?:#{1,6}[ \t]+|[-+*][ \t]+|\d+[.)][ \t]+|>[ \t]*)"
)
_MARKDOWN_LIST_MARKER_RE = re.compile(r"^(?:[-+*]|\d+[.)])[ \t]+$")
_MARKDOWN_TASK_MARKER_RE = re.compile(r"^\[[ xX]\][ \t]+")
_FENCED_CODE_RE = re.compile(r"^[ \t]{0,3}(?P<fence>`{3,}|~{3,})")


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
    # Exact structural affixes reconstructed by JarProcessor after translation.
    # The historical field name is kept so the writer does not need a parallel
    # Markdown-only translation path.
    title_meta: dict[str, tuple[str, str]]
    total_translatable: int


def _extract_yaml_title(line: str) -> tuple[str, str, str] | None:
    match = YAML_TITLE_RE.match(line)
    if not match:
        return None
    return match.group(1), match.group(2), match.group(3)


def split_markdown_block_structure(line: str) -> tuple[str, str, str]:
    """Return exact ``(prefix, payload, suffix)`` for a Markdown prose line.

    Block-level syntax is kept out of TranslationService. This is deliberately
    lightweight rather than an AST: preserve leading indentation, consume
    heading/list/quote/task markers, and preserve trailing whitespace/hard-break
    syntax. The remaining payload can safely use the shared inline masker.
    """
    leading = re.match(r"^[ \t]*", line)
    prefix = leading.group(0) if leading else ""
    payload = line[len(prefix) :]

    for _ in range(8):
        marker_match = _MARKDOWN_BLOCK_MARKER_RE.match(payload)
        if not marker_match:
            break
        marker = marker_match.group(0)
        prefix += marker
        payload = payload[len(marker) :]

        if _MARKDOWN_LIST_MARKER_RE.fullmatch(marker):
            task_match = _MARKDOWN_TASK_MARKER_RE.match(payload)
            if task_match:
                task_marker = task_match.group(0)
                prefix += task_marker
                payload = payload[len(task_marker) :]

    suffix = ""

    # Closing ATX heading markers are structural as well, e.g. ``## Title ##``.
    if re.match(r"^[ \t]*#{1,6}[ \t]+", prefix):
        closing = re.match(r"^(.*?)([ \t]+#+[ \t]*)$", payload)
        if closing:
            payload, suffix = closing.group(1), closing.group(2)

    # Preserve trailing spaces/tabs exactly. In Markdown, two trailing spaces are
    # a hard line break and must never be normalized by the translation service.
    if not suffix:
        trailing = re.match(r"^(.*?)([ \t]+)$", payload)
        if trailing:
            payload, suffix = trailing.group(1), trailing.group(2)

    return prefix, payload, suffix


def _markdown_payload_has_translatable_prose(payload: str) -> bool:
    if not payload.strip():
        return False

    # At this point block-level Markdown structure has already been removed.
    # Titanium Shield therefore only needs to hide inline technical fragments.
    masked, _mapping = mask_protected_fragments(payload)
    residual = PLACEHOLDER_PATTERN.sub("", masked).strip()
    return bool(
        residual
        and looks_like_source_language(residual)
        and not is_technical_term(residual)
    )


def markdown_line_has_translatable_prose(line: str) -> bool:
    """Return True when a Markdown line contains human-readable source prose."""
    _prefix, payload, _suffix = split_markdown_block_structure(line)
    return _markdown_payload_has_translatable_prose(payload)


def collect_book_markdown_selection(
    source_text: str,
    target_text: str,
    mode: str,
    *,
    smart_glue: bool,
) -> MarkdownSelection:
    """Build one shared Markdown/YAML translation plan for estimator and writer."""
    # Do not apply smart glue to the complete Markdown document. TranslationService
    # applies it to selected payloads, while gluing a whole document can join YAML
    # fences, images, tables, lists or component lines and change structure.
    source_lines = source_text.split("\n")
    target_lines = target_text.split("\n") if target_text else []
    lines_out = list(source_lines)
    pending: dict[str, str] = {}
    structural_meta: dict[str, tuple[str, str]] = {}
    total_translatable = 0

    # YAML front matter is structural only when it starts the document. A later
    # ``---`` is a horizontal rule and must not accidentally reopen YAML mode.
    has_frontmatter = bool(
        source_lines
        and source_lines[0].lstrip("\ufeff").strip() == "---"
    )
    in_yaml = has_frontmatter
    in_fenced_code = False
    fence_char = ""
    fence_len = 0

    for index, line in enumerate(source_lines):
        stripped = line.strip()

        if has_frontmatter and index == 0 and stripped == "---":
            continue

        if in_yaml:
            if stripped == "---":
                in_yaml = False
                continue
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
            structural_meta[str(index)] = (prefix, suffix)
            existing_line = target_lines[index] if index < len(target_lines) else ""
            existing_title_parts = _extract_yaml_title(existing_line)
            existing_title = (
                existing_title_parts[1] if existing_title_parts else ""
            )
            if _needs_translation(title, existing_title, mode):
                pending[str(index)] = title
            else:
                lines_out[index] = existing_line
            continue

        fence_match = _FENCED_CODE_RE.match(line)
        if fence_match:
            fence = fence_match.group("fence")
            if not in_fenced_code:
                in_fenced_code = True
                fence_char = fence[0]
                fence_len = len(fence)
            elif fence[0] == fence_char and len(fence) >= fence_len:
                in_fenced_code = False
                fence_char = ""
                fence_len = 0
            continue
        if in_fenced_code:
            continue

        block_prefix, payload, block_suffix = split_markdown_block_structure(line)
        if not _markdown_payload_has_translatable_prose(payload):
            continue

        total_translatable += 1
        existing_line = target_lines[index] if index < len(target_lines) else ""
        if _needs_translation(line, existing_line, mode):
            pending[str(index)] = payload
            if block_prefix or block_suffix:
                structural_meta[str(index)] = (block_prefix, block_suffix)
        else:
            lines_out[index] = existing_line

    return MarkdownSelection(
        source_text=source_text,
        lines_out=lines_out,
        pending=pending,
        title_meta=structural_meta,
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
