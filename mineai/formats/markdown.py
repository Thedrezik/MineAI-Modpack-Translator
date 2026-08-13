"""Lossless document planning and structure checks for Markdown books."""

from dataclasses import dataclass
import re

from mineai.formats.document import DocumentPath, StructuredDocument, TextNode
from mineai.formats.rich_text import contains_unsafe_formatting
from mineai.text_processing import (
    is_technical_term,
    looks_like_source_language,
)


YAML_TITLE_RE = re.compile(
    r'^(\s*title\s*:\s*[\'\"]?)(.*?)([\'\"]?)$',
    re.IGNORECASE,
)
MARKDOWN_HEADING_RE = re.compile(r"^(\s*#{1,6}\s+)(.*)$")
MARKDOWN_LIST_RE = re.compile(
    r"^(\s*(?:(?:[-+*]|\d+[.)])\s+)(?:\[[ xX]\]\s+)?)(.*)$"
)
MARKDOWN_QUOTE_RE = re.compile(r"^(\s*(?:>\s*)+)(.*)$")
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[[^\]\n]*\]\(([^)\n]+)\)")
MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]\n]*\]\(([^)\n]+)\)")
MARKDOWN_TAG_RE = re.compile(r"<[^>\n]+>")


@dataclass
class MarkdownSelection:
    document: StructuredDocument
    skeleton: "MarkdownSkeleton"
    source_text: str
    lines_out: list[str]
    pending: dict[str, str]
    title_meta: dict[str, tuple[str, str]]
    line_meta: dict[str, tuple[str, str]]
    unit_lines: dict[str, tuple[int, ...]]
    unit_fragments: dict[str, tuple[str, ...]]
    total_translatable: int

    def render(self) -> str:
        return self.skeleton.render(tuple(self.lines_out))

    def restore_line(self, index: int) -> None:
        self.lines_out[index] = self.skeleton.original_lines[index]

    def restore_unit(self, key: str) -> None:
        for index in self.unit_lines[key]:
            self.restore_line(index)

    def unit_for_line(self, index: int) -> str | None:
        return next(
            (
                key
                for key, indices in self.unit_lines.items()
                if index in indices
            ),
            None,
        )

    def apply_translation(self, key: str, value: str) -> bool:
        indices = self.unit_lines[key]
        if len(indices) == 1:
            prefix, suffix = self.line_meta.get(key, ("", ""))
            self.lines_out[indices[0]] = prefix + value + suffix
            return True

        wrapped = _split_translation_for_lines(
            value,
            self.unit_fragments[key],
        )
        if wrapped is None:
            return False
        for index, translated_line in zip(indices, wrapped):
            self.lines_out[index] = translated_line
        return True


@dataclass(frozen=True)
class MarkdownSkeleton:
    """Exact physical lines and separators of one Markdown document."""

    original_lines: tuple[str, ...]
    line_endings: tuple[str, ...]

    @classmethod
    def from_text(cls, text: str) -> "MarkdownSkeleton":
        pieces = re.split(r"(\r\n|\r|\n)", text)
        lines = tuple(pieces[0::2])
        endings = tuple(pieces[1::2]) + ("",)
        return cls(original_lines=lines, line_endings=endings)

    def render(self, lines: tuple[str, ...]) -> str:
        if len(lines) != len(self.original_lines):
            raise ValueError(
                "Markdown skeleton line count changed: "
                f"{len(self.original_lines)} -> {len(lines)}"
            )
        return "".join(
            line + ending
            for line, ending in zip(lines, self.line_endings)
        )


def normalize_markdown_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _needs_translation(source: str, existing: str, mode: str) -> bool:
    if mode == "force":
        return True
    return not existing.strip() or existing == source


def _extract_yaml_title(line: str) -> tuple[str, str, str] | None:
    match = YAML_TITLE_RE.match(line)
    if not match:
        return None
    return match.group(1), match.group(2), match.group(3)


def _translation_span(line: str) -> tuple[str, str, str]:
    for pattern in (
        MARKDOWN_HEADING_RE,
        MARKDOWN_LIST_RE,
        MARKDOWN_QUOTE_RE,
    ):
        match = pattern.match(line)
        if match:
            return match.group(1), match.group(2), ""
    leading = line[: len(line) - len(line.lstrip())]
    return leading, line[len(leading) :], ""


def _plain_paragraph_fragment(line: str) -> str | None:
    prefix, fragment, _suffix = _translation_span(line)
    if prefix or not fragment.strip():
        return None
    if fragment.endswith(("  ", "\\")):
        return None
    if "|" in fragment and fragment.count("|") >= 2:
        return None
    if contains_unsafe_formatting(fragment):
        return None
    if not looks_like_source_language(fragment) or is_technical_term(fragment):
        return None
    return fragment.strip()


def _split_translation_for_lines(
    translated: str,
    source_fragments: tuple[str, ...],
) -> tuple[str, ...] | None:
    words = re.sub(r"\s+", " ", translated).strip().split(" ")
    line_count = len(source_fragments)
    if line_count <= 1:
        return (translated,)
    if len(words) < line_count:
        return None

    source_lengths = [max(1, len(fragment.strip())) for fragment in source_fragments]
    translated_length = max(1, sum(len(word) for word in words) + len(words) - 1)
    total_source_length = sum(source_lengths)
    result: list[str] = []
    word_cursor = 0
    cumulative_source = 0

    for line_index in range(line_count - 1):
        cumulative_source += source_lengths[line_index]
        target_length = round(
            translated_length * cumulative_source / total_source_length
        )
        max_end = len(words) - (line_count - line_index - 1)
        candidates = range(word_cursor + 1, max_end + 1)
        end = min(
            candidates,
            key=lambda value: abs(
                len(" ".join(words[:value])) - target_length
            ),
        )
        result.append(" ".join(words[word_cursor:end]))
        word_cursor = end
    result.append(" ".join(words[word_cursor:]))
    return tuple(result)


def _markdown_line_signature(line: str, *, in_front_matter: bool) -> tuple:
    stripped = line.strip()
    if not stripped:
        return ("blank",)
    if stripped == "---":
        return ("delimiter",)
    if in_front_matter:
        title = _extract_yaml_title(line)
        if title:
            return ("yaml-title", title[0], title[2])
        return ("yaml", line)

    prefix, _text, suffix = _translation_span(line)
    return (
        "content",
        prefix,
        suffix,
        tuple(MARKDOWN_LINK_RE.findall(line)),
        tuple(MARKDOWN_IMAGE_RE.findall(line)),
        tuple(MARKDOWN_TAG_RE.findall(line)),
        line.count("|"),
        line.count("`"),
        line.count("**"),
        line.count("*"),
        line.count("_"),
        line.count("~~"),
    )


def markdown_structure_signature(text: str) -> tuple[tuple, ...]:
    lines = normalize_markdown_newlines(text).split("\n")
    signature: list[tuple] = []
    in_front_matter = False
    for index, line in enumerate(lines):
        line_is_delimiter = line.strip() == "---"
        signature.append(
            _markdown_line_signature(line, in_front_matter=in_front_matter)
        )
        if index == 0 and line_is_delimiter:
            in_front_matter = True
        elif in_front_matter and line_is_delimiter:
            in_front_matter = False
    return tuple(signature)


def markdown_structure_mismatch_indices(
    source_text: str,
    output_text: str,
) -> list[int]:
    source_text = normalize_markdown_newlines(source_text)
    output_text = normalize_markdown_newlines(output_text)
    if len(source_text.split("\n")) != len(output_text.split("\n")):
        return []
    return [
        index
        for index, (source_line, output_line) in enumerate(
            zip(
                markdown_structure_signature(source_text),
                markdown_structure_signature(output_text),
            )
        )
        if source_line != output_line
    ]


def validate_markdown_structure(
    source_text: str,
    output_text: str,
) -> str | None:
    source_text = normalize_markdown_newlines(source_text)
    output_text = normalize_markdown_newlines(output_text)
    source_lines = source_text.split("\n")
    output_lines = output_text.split("\n")
    if len(source_lines) != len(output_lines):
        return (
            "Markdown structure changed: line count "
            f"{len(source_lines)} -> {len(output_lines)}"
        )
    for index, (source_line, output_line) in enumerate(
        zip(
            markdown_structure_signature(source_text),
            markdown_structure_signature(output_text),
        ),
        start=1,
    ):
        if source_line != output_line:
            return f"Markdown structure changed at line {index}"
    return None


def markdown_structures_compatible(source_text: str, target_text: str) -> bool:
    if not target_text:
        return True
    return validate_markdown_structure(source_text, target_text) is None


def collect_book_markdown_selection(
    source_text: str,
    target_text: str,
    mode: str,
    *,
    smart_glue: bool,
) -> MarkdownSelection:
    del smart_glue
    source_skeleton = MarkdownSkeleton.from_text(source_text)
    target_skeleton = MarkdownSkeleton.from_text(target_text)
    if target_text and not markdown_structures_compatible(source_text, target_text):
        target_text = ""
        target_skeleton = MarkdownSkeleton.from_text("")

    source_lines = list(source_skeleton.original_lines)
    target_lines = list(target_skeleton.original_lines) if target_text else []
    lines_out = list(source_lines)
    pending: dict[str, str] = {}
    title_meta: dict[str, tuple[str, str]] = {}
    line_meta: dict[str, tuple[str, str]] = {}
    unit_lines: dict[str, tuple[int, ...]] = {}
    unit_fragments: dict[str, tuple[str, ...]] = {}
    document_nodes: list[TextNode] = []
    in_yaml = False

    def register_single(
        index: int,
        fragment: str,
        prefix: str,
        suffix: str,
        existing_fragment: str,
        existing_line: str,
        *,
        title: bool = False,
    ) -> None:
        key = str(index)
        unit_lines[key] = (index,)
        unit_fragments[key] = (fragment,)
        line_meta[key] = (prefix, suffix)
        if title:
            title_meta[key] = (prefix, suffix)
        document_nodes.append(
            TextNode(
                key=key,
                path=DocumentPath(("markdown", index)),
                source=fragment,
                existing=existing_fragment,
                context="yaml-title" if title else "line",
            )
        )
        if _needs_translation(fragment, existing_fragment, mode):
            pending[key] = fragment
        else:
            lines_out[index] = existing_line

    index = 0
    while index < len(source_lines):
        line = source_lines[index]
        stripped = line.strip()
        if index == 0 and stripped == "---":
            in_yaml = True
            index += 1
            continue
        if in_yaml and stripped == "---":
            in_yaml = False
            index += 1
            continue
        existing_line = target_lines[index] if index < len(target_lines) else ""

        if in_yaml:
            if not stripped.lower().startswith("title:"):
                index += 1
                continue
            source_title = _extract_yaml_title(line)
            if not source_title:
                index += 1
                continue
            prefix, title, suffix = source_title
            if (
                not title.strip()
                or not looks_like_source_language(title)
                or is_technical_term(title)
            ):
                index += 1
                continue
            existing_title_parts = _extract_yaml_title(existing_line)
            existing_title = existing_title_parts[1] if existing_title_parts else ""
            register_single(
                index,
                title,
                prefix,
                suffix,
                existing_title,
                existing_line,
                title=True,
            )
            index += 1
            continue

        if not stripped:
            index += 1
            continue

        plain_fragment = _plain_paragraph_fragment(line)
        if plain_fragment is not None:
            paragraph_indices = [index]
            paragraph_fragments = [plain_fragment]
            end = index + 1
            while end < len(source_lines):
                next_fragment = _plain_paragraph_fragment(source_lines[end])
                if next_fragment is None:
                    break
                paragraph_indices.append(end)
                paragraph_fragments.append(next_fragment)
                end += 1

            if len(paragraph_indices) > 1:
                existing_fragments: list[str] = []
                existing_lines: list[str] = []
                needs: list[bool] = []
                for line_index, fragment in zip(
                    paragraph_indices,
                    paragraph_fragments,
                ):
                    target_line = (
                        target_lines[line_index]
                        if line_index < len(target_lines)
                        else ""
                    )
                    existing_lines.append(target_line)
                    existing_fragment = (
                        _translation_span(target_line)[1]
                        if target_line
                        else ""
                    )
                    existing_fragments.append(existing_fragment)
                    needs.append(
                        _needs_translation(fragment, existing_fragment, mode)
                    )

                if all(needs) or not any(needs):
                    key = f"paragraph:{paragraph_indices[0]}-{paragraph_indices[-1]}"
                    unit_lines[key] = tuple(paragraph_indices)
                    unit_fragments[key] = tuple(paragraph_fragments)
                    line_meta[key] = ("", "")
                    document_nodes.append(
                        TextNode(
                            key=key,
                            path=DocumentPath(
                                ("markdown", paragraph_indices[0], paragraph_indices[-1])
                            ),
                            source=" ".join(paragraph_fragments),
                            existing=" ".join(existing_fragments),
                            context="paragraph",
                        )
                    )
                    if all(needs):
                        pending[key] = " ".join(paragraph_fragments)
                    else:
                        for line_index, target_line in zip(
                            paragraph_indices,
                            existing_lines,
                        ):
                            lines_out[line_index] = target_line
                    index = end
                    continue

        prefix, source_fragment, suffix = _translation_span(line)
        if not source_fragment.strip():
            index += 1
            continue
        visible_fragment = MARKDOWN_TAG_RE.sub("", source_fragment)
        visible_fragment = MARKDOWN_IMAGE_RE.sub("", visible_fragment).strip()
        if (
            not visible_fragment
            or not looks_like_source_language(visible_fragment)
            or is_technical_term(visible_fragment)
        ):
            index += 1
            continue
        if "|" in source_fragment and source_fragment.count("|") >= 2:
            index += 1
            continue

        existing_fragment = ""
        if existing_line:
            _prefix, existing_fragment, _suffix = _translation_span(existing_line)
        register_single(
            index,
            source_fragment,
            prefix,
            suffix,
            existing_fragment,
            existing_line,
        )
        index += 1

    document = StructuredDocument(
        source=source_text,
        nodes=tuple(document_nodes),
    )
    pending = document.pending(mode)
    return MarkdownSelection(
        document=document,
        skeleton=source_skeleton,
        source_text=source_text,
        lines_out=lines_out,
        pending=pending,
        title_meta=title_meta,
        line_meta=line_meta,
        unit_lines=unit_lines,
        unit_fragments=unit_fragments,
        total_translatable=len(unit_lines),
    )

