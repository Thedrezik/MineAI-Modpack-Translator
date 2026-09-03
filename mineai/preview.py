"""Read-only preview and integrity audit for translated Minecraft documents.

The preview deliberately works on decoded source/target text.  It reuses the
same FormatKit plans as the translation pipeline, so a preview cannot invent a
different Markdown or quest representation.  No file is written by this
module.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import html
import json
import os
from pathlib import Path
import re
from typing import Iterable, Mapping
import zipfile

from formatkit.contracts import ANCHOR_PATTERN, TranslationPlan
from formatkit.registry import FormatRegistry
from mineai.processors.snbt_extract import (
    build_snbt_document,
)
from mineai.text_processing import (
    is_nontranslatable_value,
    is_technical_term,
    looks_like_source_language,
)
from mineai.language_validation import (
    delimiter_counts_need_repair,
    formatting_boundaries_need_repair,
    has_untranslated_source_words,
)
from mineai.processors.book_paths import MarkdownBookLocator
from mineai.processors.loose_paths import loose_target_disk_path


_WORD_RE = re.compile(r"[A-Za-z]{3,}")
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")
_QUEST_ID_RE = re.compile(r"(?i)\bid\s*:\s*\"([0-9a-f]{16})\"")
_DEPENDENCIES_RE = re.compile(
    r"(?is)\bdependencies\s*:\s*\[(?P<body>.*?)\]"
)
_QUOTED_ID_RE = re.compile(r'"([0-9a-f]{16})"', re.IGNORECASE)
_FTB_DISPLAY_RE = re.compile(
    r'(?im)^\s*(?P<kind>chapter_group|chapter|quest|file)\.'
    r'(?P<id>[0-9a-f]{16})\.(?P<field>'
    r'title|name|subtitle|quest_subtitle|chapter_subtitle'
    r')\s*:\s*"(?P<value>(?:\\.|[^"\\])*)"'
)
_LOCALE_DIRECTORY_RE = re.compile(r"(?:^|/)_?([a-z]{2}_[a-z]{2})(?:/|$)", re.IGNORECASE)
_SERVICE_DOCUMENT_NAMES = frozenset(
    {
        "readme.md",
        "readme.markdown",
        "changelog.md",
        "changelog.markdown",
        "license.md",
        "license.markdown",
        "credits.md",
        "credits.markdown",
        "notice.md",
        "notice.markdown",
        "license.txt",
        "credits.txt",
        "notice.txt",
    }
)
_BOOK_PATH_MARKERS = (
    "/patchouli_books/",
    "/modonomicon/books/",
    "/oracle_index/books/",
    "/guide/",
    "/guides/",
    "/guidebook/",
    "/guidebooks/",
    "/lexicon/",
    "/handbook/",
    "/codex/",
    "/wiki/",
    "/mi_guidebook/",
    "/fieldguide/",
    "/manual/",
    "/manuals/",
    "/book/",
    "/books/",
    "/ae2guide/",
)
_BOOK_DIRECTORY_SUFFIXES = ("_book", "_books")
_BOOK_TEXT_EXTENSIONS = (
    ".md",
    ".markdown",
    ".mdx",
    ".txt",
    ".json",
    ".xml",
    ".lang",
    ".properties",
    ".snbt",
)
_BOOK_ADAPTER_IDS = frozenset(
    {
        "markdown-v2",
        "guideme-v2",
        "ie-manual-v1",
        "modonomicon-json-v1",
        "patchouli-book-json",
        "guideme-markdown",
        "guideme-data-driven-markdown",
        "immersive-engineering-manual",
        "oracle-index-mdx",
        "oracle-index-meta-json",
        "properties-v1",
        "xml-text-v1",
    }
)


@dataclass(frozen=True)
class PreviewInput:
    """One decoded source/target pair supplied by the calling application."""

    logical_path: str
    source_text: str
    target_text: str
    kind: str = "book"
    target_path: str | None = None
    skipped: bool = False


@dataclass(frozen=True)
class PreviewIssue:
    kind: str
    severity: str
    logical_path: str
    message: str
    unit_id: str = ""
    source: str = ""
    target: str = ""

    @property
    def selection_key(self) -> str:
        """Stable key used by the preview to retry one text unit."""
        return preview_selection_key(self.logical_path, self.unit_id)


@dataclass(frozen=True)
class PreviewPage:
    index: int
    title: str
    source: str
    target: str
    unit_ids: tuple[str, ...] = ()
    source_title: str = ""
    target_title: str = ""


@dataclass(frozen=True)
class QuestGraphNode:
    node_id: str
    title: str
    dependencies: tuple[str, ...] = ()
    dependency_titles: tuple[str, ...] = ()


@dataclass(frozen=True)
class QuestGraphLayoutNode:
    node_id: str
    title: str
    level: int
    column: int


@dataclass(frozen=True)
class QuestGraphLayout:
    nodes: tuple[QuestGraphLayoutNode, ...]
    edges: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class PreviewDocument:
    logical_path: str
    kind: str
    format: str
    pages: tuple[PreviewPage, ...] = ()
    graph_nodes: tuple[QuestGraphNode, ...] = ()
    issues: tuple[PreviewIssue, ...] = ()


@dataclass(frozen=True)
class PreviewReport:
    documents: tuple[PreviewDocument, ...]
    issues: tuple[PreviewIssue, ...]
    output_path: str = ""
    skipped: int = 0

    @property
    def untranslated(self) -> int:
        return sum(issue.kind == "untranslated" for issue in self.issues)

    @property
    def structure_errors(self) -> int:
        return sum(issue.kind == "structure" for issue in self.issues)

    @property
    def missing(self) -> int:
        return sum(issue.kind == "missing" for issue in self.issues)

    @property
    def translated(self) -> int:
        total = sum(
            1
            for document in self.documents
            for issue in document.issues
            if issue.kind == "translated"
        )
        return total

    @property
    def book_count(self) -> int:
        return sum(document.kind == "book" for document in self.documents)

    @property
    def quest_count(self) -> int:
        return sum(document.kind == "quest" for document in self.documents)

    def to_text(self) -> str:
        lines = [
            "MineAI Beta45 — предпросмотр перевода",
            f"Документы: {len(self.documents)} (книги: {self.book_count}, квесты: {self.quest_count})",
            f"Проверено узлов: {self.translated}",
            f"Не переведено: {self.untranslated}",
            f"Ошибок структуры: {self.structure_errors}",
            f"Отсутствует/пропущено: {self.missing + self.skipped}",
        ]
        if self.output_path:
            lines.append(f"Архив результата: {self.output_path}")
        for document in self.documents:
            lines.append("")
            label = "КНИГА" if document.kind == "book" else "КВЕСТЫ"
            lines.append(
                f"[{label}] {_preview_document_name(document.logical_path)} — "
                f"{_preview_format_label(document.format)}"
            )
            if document.kind == "book":
                lines.append(f"  Страниц: {len(document.pages)}")
                for page in document.pages[:20]:
                    preview = _plain_preview(page.target or page.source, limit=180)
                    title = _plain_preview(page.title, limit=80) or f"Страница {page.index + 1}"
                    lines.append(f"  {page.index + 1}. {title}: {preview}")
                if len(document.pages) > 20:
                    lines.append(f"  … ещё страниц: {len(document.pages) - 20}")
            else:
                lines.append(f"  Квестов на графе: {len(document.graph_nodes)}")
                for index, node in enumerate(document.graph_nodes[:40], 1):
                    title = _quest_display_title(node, index)
                    if node.dependencies:
                        names = node.dependency_titles or tuple(
                            "Название не найдено" for _ in node.dependencies
                        )
                        relation = (
                            f"Зависимостей: {len(node.dependencies)}; "
                            f"Зависит от: {', '.join(names)}"
                        )
                    else:
                        relation = "Зависимостей: 0; Без зависимостей"
                    lines.append(f"  {index}. {title} — {relation}")
                if len(document.graph_nodes) > 40:
                    lines.append(f"  … ещё квестов: {len(document.graph_nodes) - 40}")
            problems = [
                issue
                for issue in document.issues
                if issue.kind in {"untranslated", "structure", "missing", "skipped"}
            ]
            for issue in problems[:20]:
                lines.append(f"  ⚠ {_preview_issue_label(issue.kind)}: {issue.message}")
            if len(problems) > 20:
                lines.append(f"  … ещё проблем: {len(problems) - 20}")
        if not self.documents:
            lines.append("\nПодходящие книги или квесты не найдены.")
        return "\n".join(lines)

    def to_json(self) -> str:
        payload = {
            "version": "Beta45",
            "summary": {
                "documents": len(self.documents),
                "books": self.book_count,
                "quests": self.quest_count,
                "translated": self.translated,
                "untranslated": self.untranslated,
                "structure_errors": self.structure_errors,
                "missing": self.missing,
                "skipped": self.skipped,
            },
            "output_path": self.output_path,
            "documents": [
                {
                    "logical_path": document.logical_path,
                    "kind": document.kind,
                    "format": document.format,
                    "pages": [asdict(page) for page in document.pages],
                    "graph_nodes": [asdict(node) for node in document.graph_nodes],
                    "issues": [asdict(issue) for issue in document.issues],
                }
                for document in self.documents
            ],
            "issues": [asdict(issue) for issue in self.issues],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def to_html(self, *, kind: str | None = None) -> str:
        return render_preview_html(self, kind=kind)

    def units_for_selection(
        self,
        selection_keys: Iterable[str],
    ) -> dict[str, frozenset[str]]:
        """Map checked preview rows to format-unit IDs for the translator."""
        wanted = set(selection_keys)
        selected: dict[str, set[str]] = {}
        for document in self.documents:
            candidates: set[str] = set()
            for page in document.pages:
                candidates.update(page.unit_ids)
            candidates.update(
                issue.unit_id
                for issue in document.issues
                if issue.unit_id
            )
            for unit_id in candidates:
                if preview_selection_key(document.logical_path, unit_id) in wanted:
                    selected.setdefault(document.logical_path, set()).add(unit_id)
        return {
            logical_path: frozenset(unit_ids)
            for logical_path, unit_ids in selected.items()
        }


def _preview_document_name(logical_path: str) -> str:
    """Return a readable file name while keeping its useful relative path."""
    normalized = logical_path.replace("\\", "/")
    return normalized.rsplit("/", 1)[-1] or normalized


def preview_selection_key(logical_path: str, unit_id: str) -> str:
    """Build a portable, deterministic selection key for preview controls."""
    normalized = logical_path.replace("\\", "/")
    return f"{normalized}::{unit_id or '@document'}"


def build_quest_graph_layout(document: PreviewDocument) -> QuestGraphLayout:
    """Return stable levels and dependency edges for a quest graph widget."""
    nodes = tuple(document.graph_nodes)
    known = {node.node_id for node in nodes}
    levels: dict[str, int] = {}

    def level(node_id: str, visiting: set[str]) -> int:
        if node_id in levels:
            return levels[node_id]
        if node_id in visiting:
            return 0
        visiting.add(node_id)
        node = next((item for item in nodes if item.node_id == node_id), None)
        dependencies = node.dependencies if node else ()
        value = 0
        for dependency in dependencies:
            if dependency in known:
                value = max(value, level(dependency, visiting) + 1)
        visiting.discard(node_id)
        levels[node_id] = value
        return value

    for node in nodes:
        level(node.node_id, set())
    columns: dict[int, int] = {}
    layout_nodes: list[QuestGraphLayoutNode] = []
    for node in nodes:
        node_level = levels.get(node.node_id, 0)
        column = columns.get(node_level, 0)
        columns[node_level] = column + 1
        layout_nodes.append(
            QuestGraphLayoutNode(
                node_id=node.node_id,
                title=node.title,
                level=node_level,
                column=column,
            )
        )
    edges = tuple(
        (dependency, node.node_id)
        for node in nodes
        for dependency in node.dependencies
        if dependency in known
    )
    return QuestGraphLayout(tuple(layout_nodes), edges)


def _preview_format_label(format_id: str) -> str:
    labels = {
        "markdown-v2": "Markdown-книга",
        "guideme-v2": "Книга GuideME",
        "guideme-markdown": "Книга GuideME",
        "guideme-data-driven-markdown": "Книга GuideME",
        "ie-manual-v1": "Руководство Immersive Engineering",
        "immersive-engineering-manual": "Руководство Immersive Engineering",
        "modonomicon-json-v1": "Книга Modonomicon",
        "patchouli-book-json": "Книга Patchouli",
        "oracle-index-mdx": "Книга Oracle Index",
        "oracle-index-meta-json": "Книга Oracle Index",
        "ftbquests-snbt": "FTB Quests / SNBT",
        "skipped": "пропущено",
        "unknown": "неизвестный формат",
    }
    return labels.get(format_id, format_id)


def _preview_issue_label(kind: str) -> str:
    return {
        "untranslated": "не переведено",
        "structure": "структура",
        "missing": "нет результата",
        "skipped": "пропущено",
    }.get(kind, kind)


def _plain_preview(value: str, *, limit: int = 180) -> str:
    text = re.sub(r"[&§][0-9a-fk-orlmn]", "", value or "", flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        return text[: max(1, limit - 1)].rstrip() + "…"
    return text


def _quest_display_title(node: QuestGraphNode, index: int) -> str:
    title = _plain_preview(node.title, limit=120)
    if not title or title.casefold() == node.node_id.casefold():
        return f"Квест {index}"
    return title


def _quest_html_title(node: QuestGraphNode, index: int) -> str:
    if _quest_display_title(node, index) == f"Квест {index}":
        return html.escape(f"Квест {index}")
    return _render_game_text(node.title)


def _visible_payload(payload: str) -> str:
    return ANCHOR_PATTERN.sub("", payload)


def _residual_source_words(source: str, target: str) -> tuple[str, ...]:
    """Find meaningful English words that survived in the target payload."""
    target_folded = target.casefold()
    residual: list[str] = []
    for word in _WORD_RE.findall(source):
        if word.casefold() in target_folded and not is_technical_term(word):
            residual.append(word)
    return tuple(dict.fromkeys(residual))


def _looks_untranslated(source: str, target: str, target_regex: str) -> bool:
    source = _visible_payload(source).strip()
    target = _visible_payload(target).strip()
    source_for_classification = re.sub(
        r"[&§][0-9a-fk-orlmn]",
        " ",
        source,
        flags=re.IGNORECASE,
    ).strip()
    if (
        not source
        or is_nontranslatable_value(source)
        or is_technical_term(source_for_classification)
    ):
        return False
    if not target or target == source:
        return True
    if has_untranslated_source_words(
        source,
        target,
        {"api": "ru", "regex": target_regex},
    ):
        return True
    return bool(
        looks_like_source_language(source)
        and target_regex
        and not re.search(target_regex, target)
    )


def _unit_signature(plan: TranslationPlan) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            unit.kind,
            unit.encoding,
            tuple(anchor.source for anchor in unit.anchors),
        )
        for unit in plan.units
    )


def _markdown_pages(
    source: str,
    target: str,
    source_units=(),
) -> tuple[PreviewPage, ...]:
    source_lines = source.splitlines()
    target_lines = target.splitlines()
    pages: list[PreviewPage] = []
    current_title = ""
    current_target_title = ""
    current_source: list[str] = []
    current_target: list[str] = []
    current_start: int | None = None
    source_offset = 0
    page_index = 0

    def flush() -> None:
        nonlocal page_index, current_title, current_target_title, current_source, current_target, current_start
        if not current_source and not current_target and not current_title:
            return
        page_start = current_start if current_start is not None else 0
        page_end = source_offset
        page_unit_ids = tuple(
            unit.id
            for unit in source_units
            if unit.start < page_end and unit.end > page_start
        )
        pages.append(
            PreviewPage(
                index=page_index,
                title=current_title or f"Страница {page_index + 1}",
                source="\n".join(current_source).strip(),
                target="\n".join(current_target).strip(),
                unit_ids=page_unit_ids,
                source_title=current_title,
                target_title=current_target_title or current_title,
            )
        )
        page_index += 1
        current_title = ""
        current_target_title = ""
        current_source = []
        current_target = []
        current_start = None

    target_cursor = 0
    for line in source_lines:
        line_start = source_offset
        source_offset += len(line) + 1
        while target_cursor < len(target_lines) and not target_lines[target_cursor].strip():
            if current_source:
                current_target.append(target_lines[target_cursor])
            target_cursor += 1
        target_line = target_lines[target_cursor] if target_cursor < len(target_lines) else ""
        target_cursor += 1
        heading = _HEADING_RE.match(line)
        if heading:
            flush()
            current_start = line_start
            current_title = heading.group(2)
            target_heading = _HEADING_RE.match(target_line)
            if target_heading:
                current_target_title = target_heading.group(2)
                current_target.append(target_heading.group(2))
            current_source.append(line)
            continue
        if current_start is None:
            current_start = line_start
        current_source.append(line)
        current_target.append(target_line)
    while target_cursor < len(target_lines):
        current_target.append(target_lines[target_cursor])
        target_cursor += 1
    flush()
    return tuple(pages)


def _generic_pages(source_plan: TranslationPlan, target_plan: TranslationPlan) -> tuple[PreviewPage, ...]:
    pages: list[PreviewPage] = []
    for index, source_unit in enumerate(source_plan.units):
        target_unit = target_plan.units[index] if index < len(target_plan.units) else None
        pages.append(
            PreviewPage(
                index=index,
                title=source_unit.kind or f"Фрагмент {index + 1}",
                source=_visible_payload(source_unit.payload),
                target=_visible_payload(target_unit.payload if target_unit else ""),
                unit_ids=(source_unit.id,),
            )
        )
    return tuple(pages)


def _snbt_masked_skeleton(
    content: str,
    document,
    paths: set[str],
    *,
    require_translatable: bool = True,
) -> str:
    replacements: list[tuple[int, int, str]] = []
    for node in document.nodes:
        if node.key not in paths or (require_translatable and not node.translatable):
            continue
        replacements.append(
            (
                int(node.metadata["start"]),
                int(node.metadata["end"]),
                "<mineai-translatable>",
            )
        )
    chunks: list[str] = []
    cursor = 0
    for start, end, value in sorted(replacements):
        chunks.append(content[cursor:start])
        chunks.append(value)
        cursor = end
    chunks.append(content[cursor:])
    return "".join(chunks)


def _is_ftb_language_catalog(path: str) -> bool:
    normalized = "/" + path.casefold().replace("\\", "/")
    return "/ftbquests/quests/lang/" in normalized


def _decode_snbt_string(value: str) -> str:
    """Decode the JSON-compatible escapes used by FTB Quests SNBT strings."""
    try:
        return json.loads(f'"{value}"')
    except (TypeError, ValueError, json.JSONDecodeError):
        return (
            value.replace(r"\\", "\\")
            .replace(r'\"', '"')
            .replace(r"\n", "\n")
            .replace(r"\t", "\t")
        )


def _ftb_quest_catalog_titles(
    items: Iterable[PreviewInput],
) -> tuple[dict[tuple[str, str], str], dict[tuple[str, str], str]]:
    source_titles: dict[tuple[str, str], str] = {}
    target_titles: dict[tuple[str, str], str] = {}
    for item in items:
        is_catalog = (
            _is_ftb_language_catalog(item.logical_path)
            or item.kind.casefold() in {"quest_catalog", "catalog"}
        )
        if is_catalog:
            for match in _FTB_DISPLAY_RE.finditer(item.source_text):
                key = (match.group("kind").casefold(), match.group("id").upper())
                if match.group("field").casefold() == "title" or key not in source_titles:
                    source_titles[key] = _decode_snbt_string(match.group("value"))
            for match in _FTB_DISPLAY_RE.finditer(item.target_text):
                key = (match.group("kind").casefold(), match.group("id").upper())
                if match.group("field").casefold() == "title" or key not in target_titles:
                    target_titles[key] = _decode_snbt_string(match.group("value"))
            if item.logical_path.casefold().endswith(".json"):
                for key, value in _json_catalog_strings(item.source_text).items():
                    source_titles[("key", key.casefold())] = value
                for key, value in _json_catalog_strings(item.target_text).items():
                    target_titles[("key", key.casefold())] = value

        # Some packs keep literal titles in a chapter instead of the shared
        # lang catalog.  Index every quest document up front so a dependency
        # in another file can still be rendered with its translated name.
        if item.kind.casefold() == "quest" and item.logical_path.casefold().endswith(".snbt"):
            source_kind, source_objects = _snbt_graph_objects(item.source_text)
            target_kind, target_objects = _snbt_graph_objects(item.target_text)
            for node_id, body in source_objects:
                title = _snbt_display_title(body)
                if title:
                    source_titles[(source_kind, node_id)] = title
            for node_id, body in target_objects:
                title = _snbt_display_title(body)
                if title:
                    target_titles[(target_kind, node_id)] = title
    return source_titles, target_titles


def _json_catalog_strings(content: str) -> dict[str, str]:
    try:
        payload = json.loads(content)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    result: dict[str, str] = {}

    def visit(value) -> None:
        if not isinstance(value, dict):
            return
        for key, child in value.items():
            if isinstance(child, str):
                result[str(key)] = child
            elif isinstance(child, dict):
                visit(child)

    visit(payload)
    return result


def _looks_like_quest_catalog(content: str) -> bool:
    return any(
        key.casefold().startswith(("quest.", "chapter.", "chapter_group."))
        or ".quest." in key.casefold()
        or ".chapters." in key.casefold()
        for key in _json_catalog_strings(content)
    )


def _snbt_list_objects(content: str, field: str) -> tuple[str, ...]:
    """Extract only top-level object entries from an SNBT list.

    FTB Quests stores quest objects in ``quests: [...]``.  Searching every
    ``id:`` in the file also finds task and reward IDs, which are not quests.
    This small scanner understands quoted strings and nested braces without
    attempting to parse or rewrite SNBT.
    """
    field_match = re.search(
        rf"(?im)\b{re.escape(field)}\s*:\s*\[",
        content,
    )
    if field_match is None:
        return ()
    list_start = content.find("[", field_match.start(), field_match.end())
    if list_start < 0:
        return ()

    objects: list[str] = []
    quote = False
    escaped = False
    brace_depth = 0
    object_start: int | None = None
    for index in range(list_start + 1, len(content)):
        char = content[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quote = False
            continue
        if char == '"':
            quote = True
        elif char == "{":
            if brace_depth == 0:
                object_start = index
            brace_depth += 1
        elif char == "}" and brace_depth:
            brace_depth -= 1
            if brace_depth == 0 and object_start is not None:
                objects.append(content[object_start : index + 1])
                object_start = None
        elif char == "]" and brace_depth == 0:
            break
    return tuple(objects)


def _snbt_object_id(body: str) -> str:
    match = re.search(r'(?im)(?:^|[\n{])\s*id\s*:\s*"([0-9a-f]{16})"', body)
    return match.group(1).upper() if match else ""


def _snbt_literal_title(body: str) -> str:
    match = re.search(r'(?im)(?:^|[,\n{])\s*title\s*:\s*"((?:[^"\\]|\\.)*)"', body)
    return _decode_snbt_string(match.group(1)) if match else ""


def _snbt_display_title(body: str) -> str:
    """Return the best human-facing title available in a quest object."""
    for field in ("title", "name", "subtitle", "quest_subtitle", "chapter_subtitle"):
        match = re.search(
            rf"(?is)\b{re.escape(field)}\s*:\s*\"((?:[^\"\\]|\\.)*)\"",
            body,
        )
        if match:
            value = _decode_snbt_string(match.group(1)).strip()
            if value:
                return value
    return ""


def _snbt_first_text(body: str, field: str) -> str:
    match = re.search(
        rf"(?is)\b{re.escape(field)}\s*:\s*\[(?P<body>.*?)\]",
        body,
    )
    if match is None:
        return ""
    for value in re.finditer(r'"((?:[^"\\]|\\.)*)"', match.group("body")):
        text = _decode_snbt_string(value.group(1)).strip()
        if text:
            return text
    return ""


def _snbt_dependency_ids(body: str) -> tuple[str, ...]:
    match = _DEPENDENCIES_RE.search(body)
    if match is None:
        return ()
    return tuple(dict.fromkeys(item.upper() for item in _QUOTED_ID_RE.findall(match.group("body"))))


def _snbt_graph_objects(content: str) -> tuple[str, tuple[tuple[str, str], ...]]:
    for field, kind in (
        ("quests", "quest"),
        ("chapters", "chapter"),
        ("chapter_groups", "chapter_group"),
    ):
        objects = _snbt_list_objects(content, field)
        if objects:
            return kind, tuple(
                (node_id, body)
                for body in objects
                if (node_id := _snbt_object_id(body))
            )
    return "", ()


def _catalog_title(
    node_id: str,
    kind: str,
    target_titles: Mapping[tuple[str, str], str],
    source_titles: Mapping[tuple[str, str], str],
) -> str:
    keys = (
        (kind, node_id),
        ("quest", node_id),
        ("chapter", node_id),
        ("chapter_group", node_id),
        ("file", node_id),
    )
    for key in keys:
        title = target_titles.get(key) or source_titles.get(key)
        if title:
            return title
    return ""


def _localized_title(
    title: str,
    target_titles: Mapping[tuple[str, str], str],
    source_titles: Mapping[tuple[str, str], str],
) -> str:
    match = re.fullmatch(r"\{([^{}]+)\}", title.strip())
    if match is None:
        return title
    key = match.group(1).casefold()
    return target_titles.get(("key", key)) or source_titles.get(("key", key)) or title


def _humanize_localization_key(title: str) -> str:
    match = re.fullmatch(r"\{([^{}]+)\}", title.strip())
    if match is None:
        return title
    tail = match.group(1).rsplit(".", 1)[-1].replace("_", " ")
    tail = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", tail)
    return tail[:1].upper() + tail[1:] if tail else ""


def _quest_fallback_title(node_id: str, *, dependency: bool = False) -> str:
    """Give the user a stable, readable label when no title exists."""
    if dependency and node_id:
        return f"Квест {node_id[:8]}"
    return "Квест без названия"


def _snbt_graph(
    source: str,
    target: str,
    *,
    source_titles: Mapping[tuple[str, str], str] | None = None,
    target_titles: Mapping[tuple[str, str], str] | None = None,
) -> tuple[QuestGraphNode, ...]:
    source_titles = source_titles or {}
    target_titles = target_titles or {}
    source_kind, source_objects = _snbt_graph_objects(source)
    target_kind, target_objects = _snbt_graph_objects(target)

    if source_objects:
        target_by_id = {node_id: body for node_id, body in target_objects}
        result: list[QuestGraphNode] = []
        for node_id, body in source_objects:
            target_body = target_by_id.get(node_id, "")
            title = _snbt_display_title(target_body) or _snbt_display_title(body)
            title = title or _snbt_first_text(target_body, "description") or _snbt_first_text(body, "description")
            title = _localized_title(title, target_titles, source_titles)
            title = title or _catalog_title(node_id, source_kind, target_titles, source_titles)
            title = _humanize_localization_key(title) or _quest_fallback_title(node_id)
            dependencies = _snbt_dependency_ids(body)
            dependency_titles = tuple(
                _catalog_title(dependency, "quest", target_titles, source_titles)
                or _quest_fallback_title(dependency, dependency=True)
                for dependency in dependencies
            )
            result.append(
                QuestGraphNode(
                    node_id=node_id,
                    title=title or node_id,
                    dependencies=dependencies,
                    dependency_titles=dependency_titles,
                )
            )
        return tuple(result)

    # Compatibility fallback for the compact SNBT snippets used by older
    # packs and by the preview API.  It still pairs target bodies by ID.
    source_ids = list(_QUEST_ID_RE.finditer(source))
    target_ids = list(_QUEST_ID_RE.finditer(target))
    target_by_id = {
        match.group(1).upper(): (index, match)
        for index, match in enumerate(target_ids)
    }
    result = []
    for index, match in enumerate(source_ids):
        node_id = match.group(1).upper()
        end = source_ids[index + 1].start() if index + 1 < len(source_ids) else len(source)
        body = source[match.end() : end]
        target_match_info = target_by_id.get(node_id)
        if target_match_info is None and index < len(target_ids):
            target_match_info = (index, target_ids[index])
        if target_match_info is None:
            target_body = ""
        else:
            target_index, target_match = target_match_info
            target_end = (
                target_ids[target_index + 1].start()
                if target_index + 1 < len(target_ids)
                else len(target)
            )
            target_body = target[target_match.end() : target_end]
        title = _snbt_display_title(target_body) or _snbt_display_title(body)
        title = title or _snbt_first_text(target_body, "description") or _snbt_first_text(body, "description")
        title = _localized_title(title, target_titles, source_titles)
        title = title or _catalog_title(node_id, target_kind or "quest", target_titles, source_titles)
        title = _humanize_localization_key(title) or _quest_fallback_title(node_id)
        dependencies = _snbt_dependency_ids(body)
        dependency_titles = tuple(
            _catalog_title(dependency, "quest", target_titles, source_titles)
            or _quest_fallback_title(dependency, dependency=True)
            for dependency in dependencies
        )
        result.append(
            QuestGraphNode(
                node_id=node_id,
                title=title or node_id,
                dependencies=dependencies,
                dependency_titles=dependency_titles,
            )
        )
    return tuple(result)


class PreviewBuilder:
    """Build a pure, in-memory audit report from decoded documents."""

    def __init__(
        self,
        *,
        target_locale: str = "ru_ru",
        target_regex: str = r"[А-Яа-яЁё]",
        registry: FormatRegistry | None = None,
    ) -> None:
        self.target_locale = target_locale
        self.target_regex = target_regex
        self.registry = registry or FormatRegistry.default()

    def build(self, items: Iterable[PreviewInput]) -> PreviewReport:
        items = tuple(items)
        source_titles, target_titles = _ftb_quest_catalog_titles(items)
        documents: list[PreviewDocument] = []
        report_issues: list[PreviewIssue] = []
        skipped = 0
        for item in items:
            if item.kind.casefold() in {"quest_catalog", "catalog"}:
                continue
            if item.skipped:
                skipped += 1
                issue = PreviewIssue(
                    "skipped",
                    "info",
                    item.logical_path,
                    "Элемент исключён выбором пользователя",
                )
                document = PreviewDocument(
                    item.logical_path,
                    item.kind,
                    "skipped",
                    issues=(issue,),
                )
                documents.append(document)
                report_issues.append(issue)
                continue
            if not item.target_text:
                issue = PreviewIssue(
                    "missing",
                    "error",
                    item.logical_path,
                    "Целевой текст отсутствует",
                )
                document = PreviewDocument(
                    item.logical_path,
                    item.kind,
                    "unknown",
                    issues=(issue,),
                )
                documents.append(document)
                report_issues.append(issue)
                continue
            if item.kind.casefold() == "quest" and item.logical_path.casefold().endswith(".snbt"):
                document = self._build_quest(
                    item,
                    source_titles=source_titles,
                    target_titles=target_titles,
                )
            else:
                document = self._build_book(item)
            documents.append(document)
            report_issues.extend(document.issues)
        return PreviewReport(tuple(documents), tuple(report_issues), skipped=skipped)

    def _build_book(self, item: PreviewInput) -> PreviewDocument:
        issues: list[PreviewIssue] = []
        try:
            source_plan = self.registry.plan(
                item.logical_path,
                item.source_text,
                self.target_locale,
                target_path_hint=item.target_path,
            )
            target_plan = self.registry.plan(
                item.target_path or item.logical_path,
                item.target_text,
                self.target_locale,
                target_path_hint=item.target_path,
            )
        except Exception as exc:
            issue = PreviewIssue("structure", "error", item.logical_path, f"Не удалось разобрать формат: {exc}")
            return PreviewDocument(item.logical_path, item.kind, "unknown", issues=(issue,))

        validator = getattr(source_plan, "validator", None)
        if callable(validator):
            try:
                validation = validator(item.source_text, item.target_text)
            except Exception as exc:
                validation = None
                issues.append(PreviewIssue("structure", "error", item.logical_path, f"Ошибка проверки структуры: {exc}"))
            if validation is not None and not validation.ok:
                for error in validation.errors:
                    issues.append(PreviewIssue("structure", "error", item.logical_path, error))
        else:
            try:
                source_plan.merge_existing(target_plan, self.target_regex)
            except Exception as exc:
                issues.append(PreviewIssue("structure", "error", item.logical_path, str(exc)))
        if _unit_signature(source_plan) != _unit_signature(target_plan) or len(source_plan.units) != len(target_plan.units):
            issues.append(PreviewIssue("structure", "error", item.logical_path, "Набор переводимых узлов изменён"))

        for index, source_unit in enumerate(source_plan.units):
            target_unit = target_plan.units[index] if index < len(target_plan.units) else None
            if target_unit is None:
                issues.append(
                    PreviewIssue(
                        "missing",
                        "error",
                        item.logical_path,
                        "Целевой узел отсутствует",
                        unit_id=source_unit.id,
                        source=_visible_payload(source_unit.payload),
                    )
                )
                continue
            source_payload = source_unit.payload
            target_payload = target_unit.payload
            source_visible = _visible_payload(source_payload)
            target_visible = _visible_payload(target_payload)
            if delimiter_counts_need_repair(source_visible, target_visible):
                issues.append(
                    PreviewIssue(
                        "structure",
                        "error",
                        item.logical_path,
                        "Изменено количество структурных разделителей",
                        unit_id=source_unit.id,
                        source=source_visible,
                        target=target_visible,
                    )
                )
                continue
            if formatting_boundaries_need_repair(source_payload, target_payload):
                issues.append(
                    PreviewIssue(
                        "structure",
                        "error",
                        item.logical_path,
                        "Изменены пробелы вокруг цветовых/стилевых кодов",
                        unit_id=source_unit.id,
                        source=source_visible,
                        target=target_visible,
                    )
                )
                continue
            if _looks_untranslated(source_visible, target_visible, self.target_regex):
                residual = _residual_source_words(source_visible, target_visible)
                message = "Текст не переведён"
                if residual:
                    message += ": " + ", ".join(residual[:4])
                issues.append(
                    PreviewIssue(
                        "untranslated",
                        "warning",
                        item.logical_path,
                        message,
                        unit_id=source_unit.id,
                        source=source_visible,
                        target=target_visible,
                    )
                )
            else:
                issues.append(PreviewIssue("translated", "info", item.logical_path, "Узел проверен", unit_id=source_unit.id))

        pages = (
            _markdown_pages(item.source_text, item.target_text, source_plan.units)
            if source_plan.adapter_id.startswith("markdown")
            else _generic_pages(source_plan, target_plan)
        )
        return PreviewDocument(item.logical_path, item.kind, source_plan.adapter_id, pages=pages, issues=tuple(issues))

    def _build_quest(
        self,
        item: PreviewInput,
        *,
        source_titles: Mapping[tuple[str, str], str] | None = None,
        target_titles: Mapping[tuple[str, str], str] | None = None,
    ) -> PreviewDocument:
        issues: list[PreviewIssue] = []
        is_language_catalog = "/ftbquests/quests/lang/" in (
            "/" + item.logical_path.casefold().replace("\\", "/")
        )
        try:
            source_doc = build_snbt_document(item.source_text)
            target_doc = build_snbt_document(item.target_text)
        except Exception as exc:
            issue = PreviewIssue("structure", "error", item.logical_path, f"Не удалось разобрать SNBT: {exc}")
            return PreviewDocument(item.logical_path, item.kind, "ftbquests-snbt", issues=(issue,))

        source_paths = tuple(node.key for node in source_doc.nodes)
        target_paths = tuple(node.key for node in target_doc.nodes)
        if source_paths != target_paths and not is_language_catalog:
            issues.append(PreviewIssue("structure", "error", item.logical_path, "Пути и количество узлов квеста изменены"))

        target_by_path = {node.key: node for node in target_doc.nodes}
        source_translatable = {node.key for node in source_doc.nodes if node.translatable}
        target_paths_by_source = set(target_by_path)
        if source_translatable - target_paths_by_source:
            issues.append(PreviewIssue("missing", "error", item.logical_path, "Часть текстовых узлов квеста отсутствует"))

        if not is_language_catalog:
            source_skeleton = _snbt_masked_skeleton(item.source_text, source_doc, source_translatable)
            target_skeleton = _snbt_masked_skeleton(
                item.target_text,
                target_doc,
                source_translatable,
                require_translatable=False,
            )
            if source_skeleton != target_skeleton:
                issues.append(
                    PreviewIssue(
                        "structure",
                        "error",
                        item.logical_path,
                        "Изменены ID, зависимости, требования, награды или другие защищённые данные квеста",
                    )
                )
        if "/ftbquests/quests/chapters/" in f"/{item.logical_path.casefold()}" or "/ftbquests/quests/reward_tables/" in f"/{item.logical_path.casefold()}":
            try:
                from mineai_formatkit.ftb_quests import FtbQuestsChapterAdapter

                chapter_adapter = FtbQuestsChapterAdapter()
                source_fingerprint = chapter_adapter.fingerprint(item.source_text)
                target_fingerprint = chapter_adapter.fingerprint(item.target_text)
                source_plan = chapter_adapter.prepare(item.logical_path, item.source_text)
                target_plan = chapter_adapter.prepare(item.logical_path, item.target_text)
                source_protected = tuple(
                    (unit.kind, tuple((item.placeholder, item.value) for item in unit.protected))
                    for unit in source_plan.units
                )
                target_protected = tuple(
                    (unit.kind, tuple((item.placeholder, item.value) for item in unit.protected))
                    for unit in target_plan.units
                )
                if (
                    (source_fingerprint.fields or source_fingerprint.component_skeletons)
                    and source_fingerprint != target_fingerprint
                ):
                    issues.append(
                        PreviewIssue(
                            "structure",
                            "error",
                            item.logical_path,
                            "Изменены защищённые ссылки или JSON-компоненты текста FTB Quests",
                        )
                    )
                elif source_protected != target_protected:
                    issues.append(
                        PreviewIssue(
                            "structure",
                            "error",
                            item.logical_path,
                            "Изменены защищённые ссылки или JSON-компоненты текста FTB Quests",
                        )
                    )
            except Exception as exc:
                issues.append(PreviewIssue("structure", "error", item.logical_path, f"Проверка FTB Quests не выполнена: {exc}"))

        for node in source_doc.nodes:
            if not node.translatable:
                continue
            target_node = target_by_path.get(node.key)
            if target_node is None:
                continue
            if delimiter_counts_need_repair(node.source, target_node.source):
                issues.append(
                    PreviewIssue(
                        "structure",
                        "error",
                        item.logical_path,
                        "Изменено количество структурных разделителей",
                        unit_id=node.key,
                        source=node.source,
                        target=target_node.source,
                    )
                )
                continue
            if formatting_boundaries_need_repair(node.source, target_node.source):
                issues.append(
                    PreviewIssue(
                        "structure",
                        "error",
                        item.logical_path,
                        "Изменены пробелы вокруг цветовых/стилевых кодов",
                        unit_id=node.key,
                        source=node.source,
                        target=target_node.source,
                    )
                )
                continue
            if _looks_untranslated(node.source, target_node.source, self.target_regex):
                issues.append(
                    PreviewIssue(
                        "untranslated",
                        "warning",
                        item.logical_path,
                        "Текст квеста не переведён",
                        unit_id=node.key,
                        source=node.source,
                        target=target_node.source,
                    )
                )
            else:
                issues.append(PreviewIssue("translated", "info", item.logical_path, "Узел проверен", unit_id=node.key))

        graph = _snbt_graph(
            item.source_text,
            item.target_text,
            source_titles=source_titles,
            target_titles=target_titles,
        )
        page_unit_ids = {
            node.node_id: tuple(
                source_node.key
                for source_node in source_doc.nodes
                if source_node.metadata.get("entry_id") == node.node_id
            )
            for node in graph
        }
        pages = tuple(
            PreviewPage(
                index=index,
                title=node.title,
                source=node.node_id,
                target=node.title,
                unit_ids=page_unit_ids.get(node.node_id, ()),
            )
            for index, node in enumerate(graph)
        )
        return PreviewDocument(item.logical_path, item.kind, "ftbquests-snbt", pages=pages, graph_nodes=graph, issues=tuple(issues))


def build_preview_report(
    items: Iterable[PreviewInput],
    *,
    target_locale: str = "ru_ru",
    target_regex: str = r"[А-Яа-яЁё]",
) -> PreviewReport:
    return PreviewBuilder(target_locale=target_locale, target_regex=target_regex).build(items)


def _read_archive_text(archive: zipfile.ZipFile, name: str) -> str | None:
    try:
        raw = archive.read(name)
    except KeyError:
        return None
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def _preview_archives(mc_dir: str) -> list[Path]:
    roots = (
        Path(mc_dir) / "resourcepacks",
        Path(mc_dir) / "MineAI_Datapacks",
        Path(mc_dir) / "config" / "openloader" / "data",
        Path(mc_dir) / "world" / "datapacks",
    )
    candidates = [
        path
        for root in roots
        if root.is_dir()
        for path in root.glob("*.zip")
        if path.is_file()
    ]
    return sorted(
        candidates,
        key=lambda path: (
            0 if any(token in path.name.casefold() for token in ("mineai", "beta")) else 1,
            -path.stat().st_mtime,
        ),
    )


def _archive_group_key(path: Path) -> str:
    name = path.stem.casefold()
    return re.sub(r"(?:_datapack|\.datapack)$", "", name)


def _candidate_book_path(path: str) -> bool:
    lower = path.casefold()
    locale = _LOCALE_DIRECTORY_RE.search(lower)
    if locale is not None and locale.group(1).casefold() != "en_us":
        return False
    name = lower.rsplit("/", 1)[-1]
    if name in _SERVICE_DOCUMENT_NAMES or name.startswith("license"):
        return False
    if "meta-inf/" in lower or "/thirdparty/" in lower:
        return False
    if not lower.endswith(_BOOK_TEXT_EXTENSIONS):
        return False
    # Do not infer a book from a JSON extension alone.  The marker is shared
    # with the analyzer and covers every registered guide format (Markdown,
    # Patchouli, Modonomicon, GuideME, IE, Oracle Index, XML and legacy text).
    if any(marker in lower for marker in _BOOK_PATH_MARKERS):
        return True
    parts = lower.strip("/").split("/")
    return any(
        index >= 2
        and index < len(parts) - 1
        and parts[index].endswith(_BOOK_DIRECTORY_SUFFIXES)
        for index in range(len(parts))
    )


def _book_plan_for_path(
    registry: FormatRegistry,
    logical_path: str,
    source_text: str,
    target_locale: str,
    *,
    target_path_hint: str | None = None,
):
    """Return a registered book plan, never a generic mod-locale plan.

    Discovery is intentionally adapter-driven.  A path marker narrows the
    candidate set, while the registry decides whether the concrete document
    is a supported book format and supplies its lossless target path.
    """
    if not _candidate_book_path(logical_path):
        return None
    try:
        plan = registry.plan(
            logical_path,
            source_text,
            target_locale,
            target_path_hint=target_path_hint,
        )
    except Exception:
        return None
    adapter_id = str(getattr(plan, "adapter_id", ""))
    if adapter_id in _BOOK_ADAPTER_IDS:
        return plan
    # A future FormatKit book adapter may use a new stable name.  Keep it
    # discoverable when it is attached to an explicit book tree, but never
    # classify ordinary assets/lang JSON as a book.
    return plan if plan.units else None


def _logical_loose_book_path(raw_path: str, mc_dir: str) -> str:
    """Map a loose KubeJS/datapack file to the logical archive path."""
    relative = os.path.relpath(raw_path, mc_dir).replace(os.sep, "/").strip("/")
    parts = relative.split("/")
    lowered = [part.casefold() for part in parts]
    for root_name in ("assets", "data"):
        if root_name in lowered:
            return "/".join(parts[lowered.index(root_name) :])
    return relative


def _discover_loose_book_sources(mc_dir: str) -> list[tuple[str, str]]:
    """Find data-driven books which have no ``en_us`` directory.

    Modonomicon books are the important example: their source JSON lives at
    ``data/<namespace>/modonomicon/books/...`` and the translated copy is
    supplied by a data-pack.  Only explicit book trees are walked; normal
    configs, saves and mod files are never treated as preview documents.
    """
    root = Path(mc_dir)
    search_roots = (
        root / "kubejs",
        root / "config",
        root / "defaultconfigs",
        root / "world" / "datapacks",
        root / "data",
    )
    found: dict[str, str] = {}
    for search_root in search_roots:
        if not search_root.is_dir():
            continue
        for current, _directories, files in os.walk(search_root):
            for filename in files:
                raw_path = os.path.join(current, filename)
                logical_path = _logical_loose_book_path(raw_path, mc_dir)
                if not _candidate_book_path(logical_path):
                    continue
                key = os.path.abspath(raw_path).casefold()
                found.setdefault(key, raw_path)
    return [
        (raw_path, _logical_loose_book_path(raw_path, mc_dir))
        for raw_path in sorted(found.values(), key=str.casefold)
    ]


def _loose_target_candidates(
    raw_path: str,
    logical_path: str,
    plan_target_path: str,
    mc_dir: str,
    target_locale: str,
) -> tuple[str, ...]:
    candidates: list[str] = []

    def add(value: str | None) -> None:
        if not value:
            return
        normalized = value.replace("\\", "/").strip("/")
        if normalized and normalized.casefold() not in {item.casefold() for item in candidates}:
            candidates.append(normalized)

    add(plan_target_path)
    add(logical_path)
    target_disk = loose_target_disk_path(raw_path, target_locale)
    if os.path.abspath(target_disk) != os.path.abspath(raw_path):
        add(os.path.relpath(target_disk, mc_dir))
    for value in tuple(candidates):
        parts = value.split("/")
        lowered = [part.casefold() for part in parts]
        for root_name in ("assets", "data"):
            if root_name in lowered:
                add("/".join(parts[lowered.index(root_name) :]))
    return tuple(candidates)


def _target_archive_entry(
    archives: tuple[zipfile.ZipFile, ...],
    path: str,
) -> tuple[str, str] | None:
    folded = path.casefold().lstrip("/")
    for archive in archives:
        names = getattr(archive, "_mineai_name_index", None)
        if names is None:
            names = {
                name.casefold().lstrip("/"): name
                for name in archive.namelist()
            }
            setattr(archive, "_mineai_name_index", names)
        actual = names.get(folded)
        if actual is None:
            continue
        text = _read_archive_text(archive, actual)
        if text is not None:
            return text, actual
    return None


def _archive_language_catalog(
    archives: tuple[zipfile.ZipFile, ...],
    target_locale: str,
) -> dict[str, str]:
    """Collect translated Minecraft locale keys from generated archives."""
    suffix = f"/{target_locale.casefold()}.json"
    catalog: dict[str, str] = {}
    for archive in archives:
        for name in archive.namelist():
            lower = name.casefold().lstrip("/")
            if "/lang/" not in lower or not lower.endswith(suffix):
                continue
            text = _read_archive_text(archive, name)
            if not text:
                continue
            try:
                payload = json.loads(text)
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            for key, value in payload.items():
                if isinstance(key, str) and isinstance(value, str):
                    catalog[key] = value
    return catalog


def _resolve_companion_book_text(
    source_text: str,
    target_text: str,
    catalog: Mapping[str, str],
) -> str:
    """Expand Modonomicon localization keys for a faithful read-only view.

    The translation pipeline deliberately stores Modonomicon prose in a
    resource-pack locale and leaves the data-pack JSON structure untouched.
    Preview must therefore join both artifacts before planning units.  Missing
    keys fall back to the source value so they are reported as untranslated,
    not as a false structural mismatch.
    """
    localization_key = re.compile(r"^mineai\.book\.[A-Za-z0-9_.-]+$")
    try:
        # Modonomicon permits literal control characters in legacy JSON.  Its
        # lossless walker already handles those strings, while json.loads does
        # not, so use token spans instead of reserializing the whole document.
        from formatkit.adapters.modonomicon import _JsonWalker

        source_tokens = {
            token.path: token
            for token in _JsonWalker(source_text).walk()
            if not token.is_key
        }
        target_tokens = [
            token
            for token in _JsonWalker(target_text).walk()
            if not token.is_key
        ]
    except (TypeError, ValueError):
        return target_text

    replacements: list[tuple[int, int, str]] = []
    for token in target_tokens:
        if not localization_key.fullmatch(token.value):
            continue
        replacement = catalog.get(token.value)
        if replacement is None:
            source_token = source_tokens.get(token.path)
            replacement = (
                source_token.value
                if source_token is not None and isinstance(source_token.value, str)
                else None
            )
        if replacement is None:
            continue
        replacements.append(
            (token.start, token.end, json.dumps(replacement, ensure_ascii=False))
        )
    for start, end, replacement in reversed(replacements):
        target_text = target_text[:start] + replacement + target_text[end:]
    return target_text


def _quest_target_path(path: Path, target_locale: str) -> Path:
    parts = list(path.parts)
    for index, part in enumerate(parts):
        if part.casefold() == "en_us":
            parts[index] = target_locale.casefold()
            return Path(*parts)
    if path.name.casefold() == "en_us.snbt":
        return path.with_name(f"{target_locale.casefold()}.snbt")
    return path


def discover_preview_items(
    mc_dir: str,
    *,
    target_locale: str = "ru_ru",
) -> tuple[tuple[PreviewInput, ...], str]:
    """Discover decoded books and quests plus the latest MineAI output.

    The caller owns the input directory.  This function only reads archives and
    files; it never creates a target or a backup.  A missing target is still
    returned as an item so the report can explicitly show it as untranslated.
    """
    root = Path(mc_dir)
    output_archives = _preview_archives(mc_dir)
    if output_archives:
        latest_key = _archive_group_key(output_archives[0])
        output_archives = [
            path
            for path in output_archives
            if _archive_group_key(path) == latest_key
        ]
    opened: list[zipfile.ZipFile] = []
    try:
        for path in output_archives:
            try:
                opened.append(zipfile.ZipFile(path, "r"))
            except (OSError, zipfile.BadZipFile):
                continue

        items: list[PreviewInput] = []
        seen: set[tuple[str, str]] = set()
        registry = FormatRegistry.default()
        companion_catalog = _archive_language_catalog(tuple(opened), target_locale)
        mods_dir = root / "mods"
        mod_paths = (
            sorted(
                mods_dir.glob("*.jar"),
                key=lambda p: p.name.casefold(),
            )
            if mods_dir.is_dir()
            else []
        )
        book_locator = MarkdownBookLocator.from_archives(
            [str(path) for path in mod_paths]
            + [str(path) for path in output_archives],
            target_locale,
        )
        if mods_dir.is_dir():
            for mod_path in mod_paths:
                try:
                    source_archive = zipfile.ZipFile(mod_path, "r")
                except (OSError, zipfile.BadZipFile):
                    continue
                with source_archive:
                    for name in source_archive.namelist():
                        if name.casefold().endswith("/lang/en_us.json"):
                            source_text = _read_archive_text(source_archive, name)
                            if source_text is None or not _looks_like_quest_catalog(source_text):
                                continue
                            try:
                                plan = registry.plan(name, source_text, target_locale)
                                target_path = plan.target_path or name
                            except Exception:
                                target_path = re.sub(
                                    r"(?i)(/|^)en_us\.json$",
                                    r"\1ru_ru.json",
                                    name,
                                )
                            target_result = _target_archive_entry(tuple(opened), target_path)
                            target_text = target_result[0] if target_result else ""
                            key = (name.casefold(), target_path.casefold())
                            if key not in seen:
                                seen.add(key)
                                items.append(
                                    PreviewInput(
                                        logical_path=name,
                                        source_text=source_text,
                                        target_text=target_text,
                                        kind="quest_catalog",
                                        target_path=target_path,
                                    )
                                )
                            continue
                        source_text = _read_archive_text(source_archive, name)
                        if source_text is None:
                            continue
                        plan = _book_plan_for_path(
                            registry,
                            name,
                            source_text,
                            target_locale,
                            target_path_hint=book_locator.target_path(name),
                        )
                        if plan is None:
                            continue
                        target_path = plan.target_path or name
                        target_result = _target_archive_entry(tuple(opened), target_path)
                        if target_result is None and target_path != name:
                            target_result = _target_archive_entry(tuple(opened), name)
                        target_text = target_result[0] if target_result else ""
                        if (
                            target_text
                            and plan.adapter_id == "modonomicon-json-v1"
                            and companion_catalog
                        ):
                            target_text = _resolve_companion_book_text(
                                source_text,
                                target_text,
                                companion_catalog,
                            )
                        key = (name.casefold(), target_path.casefold())
                        if key in seen:
                            continue
                        seen.add(key)
                        items.append(
                            PreviewInput(
                                logical_path=name,
                                source_text=source_text,
                                target_text=target_text,
                                kind="book",
                                target_path=target_path,
                            )
                        )

        # Loose Patchouli/guide files are not necessarily inside a mod JAR.
        try:
            from mineai.processors.discovery import discover_loose_lang_files
            from mineai.processors.loose_paths import (
                loose_pack_target_path,
            )

            loose_candidates: list[tuple[str, str]] = []
            loose_seen: set[str] = set()
            for raw_path in discover_loose_lang_files(mc_dir):
                key = os.path.abspath(raw_path).casefold()
                if key in loose_seen:
                    continue
                loose_seen.add(key)
                loose_candidates.append(
                    (
                        raw_path,
                        os.path.relpath(raw_path, mc_dir).replace(os.sep, "/"),
                    )
                )
            for raw_path, logical_path in _discover_loose_book_sources(mc_dir):
                key = os.path.abspath(raw_path).casefold()
                if key in loose_seen:
                    continue
                loose_seen.add(key)
                loose_candidates.append((raw_path, logical_path))

            for raw_path, logical_path in loose_candidates:
                source_path = Path(raw_path)
                try:
                    source_text = source_path.read_text(encoding="utf-8-sig")
                except (OSError, UnicodeDecodeError):
                    continue
                if (
                    source_path.name.casefold() == "en_us.json"
                    and "/lang/" in source_path.as_posix().casefold()
                    and _looks_like_quest_catalog(source_text)
                ):
                    target_path = Path(loose_target_disk_path(raw_path, target_locale))
                    target_text = ""
                    if target_path.is_file():
                        try:
                            target_text = target_path.read_text(encoding="utf-8-sig")
                        except (OSError, UnicodeDecodeError):
                            target_text = ""
                    target_pack_path = loose_pack_target_path(raw_path, mc_dir, target_locale)
                    target_result = (
                        _target_archive_entry(tuple(opened), target_pack_path)
                        if target_pack_path
                        else None
                    )
                    if target_result is not None:
                        target_text = target_result[0]
                    key = (logical_path.casefold(), (target_pack_path or str(target_path)).casefold())
                    if key not in seen:
                        seen.add(key)
                        items.append(
                            PreviewInput(
                                logical_path=logical_path,
                                source_text=source_text,
                                target_text=target_text,
                                kind="quest_catalog",
                                target_path=target_pack_path or logical_path,
                            )
                        )
                    continue
                plan = _book_plan_for_path(
                    registry,
                    logical_path,
                    source_text,
                    target_locale,
                    target_path_hint=book_locator.target_path(logical_path),
                )
                if plan is None:
                    continue
                target_text = ""
                target_path_name = plan.target_path or logical_path
                target_result = None
                for target_candidate in _loose_target_candidates(
                    raw_path,
                    logical_path,
                    target_path_name,
                    mc_dir,
                    target_locale,
                ):
                    target_result = _target_archive_entry(
                        tuple(opened),
                        target_candidate,
                    )
                    if target_result is not None:
                        target_path_name = target_result[1]
                        break
                if target_result is not None:
                    target_text = target_result[0]
                else:
                    target_disk = Path(loose_target_disk_path(raw_path, target_locale))
                    if target_disk != source_path and target_disk.is_file():
                        try:
                            target_text = target_disk.read_text(encoding="utf-8-sig")
                        except (OSError, UnicodeDecodeError):
                            target_text = ""
                if (
                    target_text
                    and plan.adapter_id == "modonomicon-json-v1"
                    and companion_catalog
                ):
                    target_text = _resolve_companion_book_text(
                        source_text,
                        target_text,
                        companion_catalog,
                    )
                key = (logical_path.casefold(), target_path_name.casefold())
                if key in seen:
                    continue
                seen.add(key)
                items.append(
                    PreviewInput(
                        logical_path,
                        source_text,
                        target_text,
                        "book",
                        target_path_name,
                    )
                )
        except (ImportError, OSError):
            pass

        try:
            from mineai.processors.discovery import discover_snbt_files

            for raw_path in discover_snbt_files(mc_dir):
                current_path = Path(raw_path)
                baseline_path = current_path.with_suffix(current_path.suffix + ".bak")
                target_path = _quest_target_path(current_path, target_locale)
                source_path = baseline_path if baseline_path.is_file() else current_path
                if target_path != current_path and target_path.is_file():
                    target_path = target_path
                else:
                    target_path = current_path
                try:
                    source_text = source_path.read_text(encoding="utf-8-sig")
                    target_text = target_path.read_text(encoding="utf-8-sig")
                except (OSError, UnicodeDecodeError):
                    continue
                logical_path = os.path.relpath(source_path, mc_dir).replace(os.sep, "/")
                key = (logical_path.casefold(), str(target_path).casefold())
                if key in seen:
                    continue
                seen.add(key)
                items.append(
                    PreviewInput(
                        logical_path=logical_path,
                        source_text=source_text,
                        target_text=target_text,
                        kind="quest",
                        target_path=os.path.relpath(target_path, mc_dir).replace(os.sep, "/"),
                    )
                )
        except (ImportError, OSError):
            pass
        return tuple(items), (str(output_archives[0]) if output_archives else "")
    finally:
        for archive in opened:
            archive.close()


def build_preview_from_directory(
    mc_dir: str,
    *,
    target_locale: str = "ru_ru",
    target_regex: str = r"[А-Яа-яЁё]",
) -> PreviewReport:
    items, output_path = discover_preview_items(mc_dir, target_locale=target_locale)
    report = PreviewBuilder(
        target_locale=target_locale,
        target_regex=target_regex,
    ).build(items)
    return replace(report, output_path=output_path)


_MC_COLOR_CODES = frozenset("0123456789abcdef")
_MC_STYLE_CODES = frozenset("klmno")
_MC_CODE_CLASSES = _MC_COLOR_CODES | _MC_STYLE_CODES | {"r"}


def _render_game_text(value: str) -> str:
    """Render Minecraft formatting codes without exposing the codes themselves."""
    if not value:
        return ""
    color_names = {
        "0": "black", "1": "dark-blue", "2": "dark-green", "3": "dark-aqua",
        "4": "dark-red", "5": "dark-purple", "6": "gold", "7": "gray",
        "8": "dark-gray", "9": "blue", "a": "green", "b": "aqua",
        "c": "red", "d": "light-purple", "e": "yellow", "f": "white",
    }
    output: list[str] = []
    buffer: list[str] = []
    open_spans = 0

    def flush() -> None:
        if buffer:
            output.append(html.escape("".join(buffer)).replace("\n", "<br>"))
            buffer.clear()

    def close_spans() -> None:
        nonlocal open_spans
        if open_spans:
            output.extend("</span>" for _ in range(open_spans))
            open_spans = 0

    index = 0
    while index < len(value):
        char = value[index]
        if char in "&§" and index + 1 < len(value):
            code = value[index + 1].casefold()
            if code in _MC_CODE_CLASSES:
                flush()
                if code == "r":
                    close_spans()
                elif code in _MC_COLOR_CODES:
                    output.append(f'<span class="mc-code mc-{code}">')
                    open_spans += 1
                else:
                    output.append(f'<span class="mc-code mc-{code}">')
                    open_spans += 1
                index += 2
                continue
        buffer.append(char)
        index += 1
    flush()
    close_spans()
    return "".join(output)


def _render_issue(issue: PreviewIssue) -> str:
    css_kind = html.escape(issue.kind)
    text = html.escape(issue.message)
    return f'<li class="issue {css_kind}"><b>{html.escape(_preview_issue_label(issue.kind))}</b>: {text}</li>'


def _render_book_page(
    page: PreviewPage,
    side: str,
    *,
    book_original: bool = False,
) -> str:
    page_title_value = (
        page.source_title if book_original else page.target_title
    ) or page.title
    page_title = _render_game_text(page_title_value) or f"Страница {page.index + 1}"
    target = (
        page.source if book_original else page.target
    ) or ("Оригинал отсутствует" if book_original else "Перевод отсутствует")
    target_label = "Оригинал как в игре" if book_original else "Как будет в игре"
    return (
        f'<td class="page minecraft-book minecraft-page book-page book-page-{side}" '
        'width="48%" valign="top" height="320">'
        '<div class="book-page-header">'
        '<span class="minecraft-book-ribbon">Книга · просмотр</span>'
        f'<span class="book-page-number">{page.index + 1}</span>'
        '</div>'
        f'<h4 class="book-page-title">{page_title}</h4>'
        '<div class="book-target">'
        f'<div class="book-target-label">{target_label}</div>'
        f'<div class="game-text">{_render_game_text(target)}</div>'
        '</div>'
        f'<div class="book-page-footer">{page.index + 1}</div>'
        '</td>'
    )


def _render_document(
    document: PreviewDocument,
    *,
    book_original: bool = False,
) -> str:
    title = html.escape(_preview_document_name(document.logical_path))
    path = html.escape(document.logical_path)
    body: list[str] = [
        f'<article class="document {html.escape(document.kind)}">',
        f"<h3>{'Книга' if document.kind == 'book' else 'Квесты'}: {title}</h3>",
        f'<p class="meta">{html.escape(_preview_format_label(document.format))} · '
        f'<details class="path"><summary>Показать путь</summary><code>{path}</code></details></p>',
    ]
    if document.kind == "book":
        body.append('<div class="book-pages book-volume">')
        body.append('<div class="book-cover-label">Открытая книга · перевод</div>')
        for offset in range(0, len(document.pages), 2):
            left = document.pages[offset]
            right = document.pages[offset + 1] if offset + 1 < len(document.pages) else None
            body.append('<table class="book-spread" width="100%" cellspacing="0" cellpadding="0"><tr>')
            body.append(_render_book_page(left, "left", book_original=book_original))
            body.append('<td class="page-seam" width="4%" aria-hidden="true"></td>')
            if right is not None:
                body.append(_render_book_page(right, "right", book_original=book_original))
            else:
                body.append(
                    '<td class="page minecraft-book minecraft-page book-page blank-page" '
                    'width="48%" valign="top" height="320"><div class="blank-page-mark">—</div></td>'
                )
            body.append('</tr></table>')
        if not document.pages:
            body.append('<p class="empty">Страницы книги не найдены.</p>')
        body.append("</div>")
    else:
        body.append('<div class="quest-graph quest-board"><h4>Квестовый граф</h4>')
        body.append('<p class="quest-legend">Связь читается как «квест зависит от…».</p>')
        for index, node in enumerate(document.graph_nodes, 1):
            if node.dependencies:
                names = node.dependency_titles or tuple(
                    "Название не найдено" for _ in node.dependencies
                )
                dependency_label = "Зависит от: " + ", ".join(names)
            else:
                dependency_label = "Без зависимостей"
            body.append(
                '<div class="quest-node quest-card">'
                f'<div class="quest-card-title">{_quest_html_title(node, index)}</div>'
                f'<div class="quest-card-relation">{_render_game_text(dependency_label)}</div>'
                "</div>"
            )
        if not document.graph_nodes:
            body.append("<p>Квестовые узлы не найдены.</p>")
        body.append("</div>")
    if document.issues:
        body.append("<ul class=\"issues\">" + "".join(_render_issue(issue) for issue in document.issues if issue.kind != "translated") + "</ul>")
    body.append("</article>")
    return "".join(body)


def render_preview_html(
    report: PreviewReport,
    *,
    kind: str | None = None,
    book_original: bool = False,
) -> str:
    documents = [document for document in report.documents if kind is None or document.kind == kind]
    document_kinds = {document.logical_path: document.kind for document in report.documents}
    issues = [
        issue
        for issue in report.issues
        if kind is None or document_kinds.get(issue.logical_path) == kind
    ]
    title = "MineAI Beta45 — предпросмотр как в Minecraft"
    body = [
        "<!doctype html><html><head><meta charset=\"utf-8\"><style>",
        "body{font-family:Segoe UI,Arial,sans-serif;background:#171925;color:#e7e8f2;margin:16px;}"
        "h1,h2,h3,h4{color:#f1f2ff}.meta{color:#9da1bd}.summary{display:flex;gap:10px;flex-wrap:wrap}.card{background:#24263a;border:1px solid #3b3f5b;border-radius:8px;padding:10px}.book-pages{display:grid;gap:14px}.page{border-radius:8px;padding:16px}.minecraft-book{background:#ead9ad;color:#292116;border:4px solid #6e4427;box-shadow:inset 0 0 0 2px #b58c58,0 5px 14px #0008}.minecraft-page{min-height:150px}.minecraft-book h4{color:#4b2b17;font-size:1.18em;margin:6px 0 12px}.minecraft-book-ribbon{font-size:.82em;color:#795234;text-transform:uppercase;letter-spacing:.06em}.minecraft-text{line-height:1.55}.game-text{white-space:pre-wrap;word-break:break-word;font-family:Consolas,monospace;font-size:1.04em}.source-details{margin-top:14px;color:#6d5136}.source-details summary{cursor:pointer}.source{background:#dfc995;padding:8px;border:1px dashed #9b774a}.quest-graph{display:flex;flex-wrap:wrap;gap:10px}.quest-board{background:#20212b;border:1px solid #544b86;border-radius:10px;padding:14px}.quest-board h4{width:100%;margin-top:0}.quest-legend{width:100%;color:#b8addd;margin:0 0 8px}.quest-node{display:block;min-width:220px;max-width:320px}.quest-card{background:#4a3b79;border:2px solid #9b82ea;border-radius:8px;padding:12px;box-shadow:0 3px 8px #0007}.quest-card-title,.quest-card-relation{display:block;overflow-wrap:anywhere;word-break:break-word}.quest-card-title{color:#fff;font-weight:700;margin-bottom:7px}.quest-card-relation{color:#d8d0ff;font-size:.88em;line-height:1.4}.mc-code{font-weight:normal}.mc-0{color:#000}.mc-1{color:#0000aa}.mc-2{color:#00aa00}.mc-3{color:#00aaaa}.mc-4{color:#aa0000}.mc-5{color:#aa00aa}.mc-6{color:#d27d00}.mc-7{color:#aaa}.mc-8{color:#555}.mc-9{color:#5555ff}.mc-a{color:#55ff55}.mc-b{color:#55ffff}.mc-c{color:#ff5555}.mc-d{color:#ff55ff}.mc-e{color:#ffff55}.mc-f{color:#fff}.mc-k{text-shadow:2px 0 #666}.mc-l{font-weight:700}.mc-m{text-decoration:line-through}.mc-n{text-decoration:underline}.mc-o{font-style:italic}.issue{margin:4px 0}.issue.untranslated{color:#ffd580}.issue.structure{color:#ff8e8e}.issue.missing{color:#ffb08e}.issue.skipped{color:#aeb1cc}.issue.translated{color:#72e6c2}.issues{padding-left:22px}.document{border-top:1px solid #3b3f5b;margin-top:18px;padding-top:10px}.path{display:inline}.path summary{cursor:pointer;color:#aeb1cc}.path code{color:#aeb1cc}.empty{color:#795234}",
        ".book-volume{margin:12px 0 20px;background:#6e4427;border:5px solid #4f2d19;border-radius:10px;padding:14px;box-shadow:0 8px 22px #0009}.book-cover-label{color:#f7e4bd;font-weight:700;letter-spacing:.06em;text-transform:uppercase;margin:0 0 10px 4px}.book-spread{width:100%;table-layout:fixed;border-collapse:separate;border-spacing:0}.book-page{width:48%;padding:22px 24px;color:#24170c!important;background:#f2e4c2;border:2px solid #9a6b38;border-radius:4px;box-shadow:inset 0 0 0 2px #f8edcf, inset 0 0 18px #b78a4a55}.book-page-left{border-radius:4px 0 0 4px}.book-page-right{border-radius:0 4px 4px 0}.page-seam{width:4%;background:#5e371e;box-shadow:inset 3px 0 5px #2b160c88,inset -3px 0 5px #2b160c88}.book-page-header{color:#67451f;font-size:.82em;min-height:22px}.book-page-number{float:right;color:#67451f;font-weight:700}.book-page-title{color:#3c2411!important;font-family:Georgia,serif;font-size:1.35em;margin:8px 0 16px}.book-target{background:#fff3d4;border:1px solid #d0ae72;border-radius:4px;padding:12px 14px}.book-target-label{color:#65401b;font-size:.78em;font-weight:700;letter-spacing:.04em;text-transform:uppercase;margin-bottom:7px}.book-page .game-text{color:#24170c!important;font-family:Georgia,'Times New Roman',serif;font-size:1.08em;line-height:1.65;white-space:pre-wrap;word-break:normal}.book-page .source-details{color:#65401b!important}.book-page .source-details summary{color:#65401b!important;cursor:pointer;font-weight:600}.book-page .source{color:#24170c!important;background:#ead9b7;border:1px dashed #a67d45;padding:10px}.book-page-footer{text-align:center;color:#79552d;font-size:.85em;margin-top:16px}.blank-page{color:#9f7a49!important;text-align:center}.blank-page-mark{font-size:2em;margin-top:130px}.minecraft-book .mc-0{color:#17120d}.minecraft-book .mc-2{color:#20762a}.minecraft-book .mc-3{color:#176c70}.minecraft-book .mc-4{color:#9b1d1d}.minecraft-book .mc-5{color:#81238b}.minecraft-book .mc-6{color:#885400}.minecraft-book .mc-7{color:#5c554b}.minecraft-book .mc-8{color:#3e3933}.minecraft-book .mc-9{color:#254c9c}.minecraft-book .mc-a{color:#237b2c}.minecraft-book .mc-b{color:#14767a}.minecraft-book .mc-c{color:#a32121}.minecraft-book .mc-d{color:#8d2d9d}.minecraft-book .mc-e{color:#805300}.minecraft-book .mc-f{color:#24170c}",
        "</style></head><body>",
        f"<h1>{html.escape(title)}</h1>",
        '<p class="meta">Текстовые узлы проверены отдельно; коды цвета, ссылки, ID и структура восстановляются из оригинала.</p>',
        '<div class="summary">',
        f'<div class="card">Документы: <b>{len(documents)}</b></div>',
        f'<div class="card">Проверено: <b>{sum(i.kind == "translated" for i in issues)}</b></div>',
        f'<div class="card">Не переведено: <b>{sum(i.kind == "untranslated" for i in issues)}</b></div>',
        f'<div class="card">Ошибок структуры: <b>{sum(i.kind == "structure" for i in issues)}</b></div>',
        f'<div class="card">Пропущено: <b>{sum(i.kind in {"missing", "skipped"} for i in issues)}</b></div>',
        "</div>",
        "<h2>Проблемы</h2>",
        '<ul class="issues">' + ("".join(_render_issue(issue) for issue in issues if issue.kind != "translated") or "<li>Проблем не найдено.</li>") + "</ul>",
    ]
    body.extend(
        _render_document(
            document,
            book_original=book_original,
        )
        for document in documents
    )
    body.append("</body></html>")
    return "".join(body)


__all__ = [
    "PreviewBuilder",
    "PreviewDocument",
    "PreviewInput",
    "PreviewIssue",
    "PreviewPage",
    "PreviewReport",
    "QuestGraphNode",
    "QuestGraphLayout",
    "QuestGraphLayoutNode",
    "build_quest_graph_layout",
    "build_preview_report",
    "build_preview_from_directory",
    "discover_preview_items",
    "preview_selection_key",
    "render_preview_html",
]
