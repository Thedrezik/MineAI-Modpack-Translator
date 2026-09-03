"""Lossless Markdown planning based on immutable source spans."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re

from formatkit.contracts import (
    ProtectedAnchor,
    TranslationPlan,
    TranslationUnit,
    ValidationReport,
)
from formatkit.tokenizer import GAME_TOKEN_SOURCE


_LOCALE_SEGMENT = re.compile(r"/(?:_?en_us)/", re.IGNORECASE)
_TABLE_DELIMITER = re.compile(r"^:?-{3,}:?$")
_INLINE_TOKEN = re.compile(
    r"(?P<code>(?P<ticks>`+)[^`\r\n]*(?P=ticks))|"
    r"(?P<image>!\[[^\]]*\]\([^)]*\))|"
    rf"(?P<game>{GAME_TOKEN_SOURCE})|"
    r"(?P<link>\[[^\]]+\]\([^)]*\))|"
    r"(?P<braced_link>\{[^{}|]+\|[^{}]+\})|"
    r"(?P<tag><[^>]*>)|"
    r"(?P<escape>\\[^\r\n])|"
    r"(?P<emphasis>\*{1,3}|_{1,3}|~~)|"
    r"(?P<newline>\r\n[ \t]*|\r[ \t]*|\n[ \t]*)",
    re.IGNORECASE,
)
_LABEL_TOKEN = re.compile(
    rf"{GAME_TOKEN_SOURCE}|\r\n[ \t]*|\r[ \t]*|\n[ \t]*|"
    r"\\[^\r\n]|\*{1,3}|_{1,3}|~~",
    re.IGNORECASE,
)
_BLOCK_PREFIX = re.compile(
    r"^(?P<prefix>\s*(?:#{1,6}\s+|(?:[-+*]|\d+[.)])\s+|(?:>\s*)+))"
)
_LIST_PREFIX = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+")


@dataclass(frozen=True)
class _Line:
    start: int
    content_end: int
    end: int
    content: str
    ending: str


def _lines(text: str) -> list[_Line]:
    result: list[_Line] = []
    cursor = 0
    for match in re.finditer(r".*?(?:\r\n|\r|\n|\Z)", text):
        raw = match.group(0)
        if not raw and match.start() == len(text):
            continue
        ending_match = re.search(r"(?:\r\n|\r|\n)$", raw)
        ending = ending_match.group(0) if ending_match else ""
        content = raw[: len(raw) - len(ending)] if ending else raw
        result.append(
            _Line(
                start=cursor,
                content_end=cursor + len(content),
                end=cursor + len(raw),
                content=content,
                ending=ending,
            )
        )
        cursor += len(raw)
        if cursor >= len(text):
            break
    return result


def _target_path(
    logical_path: str,
    target_locale: str,
    target_path_hint: str | None,
) -> str | None:
    if target_path_hint:
        return target_path_hint.replace("\\", "/")
    normalized = logical_path.replace("\\", "/")
    match = _LOCALE_SEGMENT.search(normalized)
    if not match:
        return None
    source_segment = match.group(0)
    prefix = "_" if "/_" in source_segment else ""
    return normalized[: match.start()] + f"/{prefix}{target_locale}/" + normalized[match.end() :]


def _anchor(
    anchors: list[ProtectedAnchor],
    source: str,
) -> str:
    token = f"⟦FK{len(anchors):04d}⟧"
    anchors.append(ProtectedAnchor(token=token, source=source))
    return token


def _protect_markdown(segment: str) -> tuple[str, tuple[ProtectedAnchor, ...]]:
    anchors: list[ProtectedAnchor] = []
    output: list[str] = []
    cursor = 0

    def append_label(label: str) -> None:
        label_cursor = 0
        for label_match in _LABEL_TOKEN.finditer(label):
            output.append(label[label_cursor : label_match.start()])
            output.append(_anchor(anchors, label_match.group(0)))
            label_cursor = label_match.end()
        output.append(label[label_cursor:])

    for match in _INLINE_TOKEN.finditer(segment):
        output.append(segment[cursor : match.start()])
        token = match.group(0)
        if match.lastgroup in {"link", "image"}:
            image_prefix = "![" if token.startswith("![") else "["
            label_start = len(image_prefix)
            label_end = token.find("]", label_start)
            label = token[label_start:label_end]
            suffix = token[label_end:]
            output.append(_anchor(anchors, image_prefix))
            append_label(label)
            output.append(_anchor(anchors, suffix))
        elif match.lastgroup == "braced_link":
            separator = token.index("|")
            output.append(_anchor(anchors, "{"))
            append_label(token[1:separator])
            output.append(_anchor(anchors, token[separator:]))
        else:
            output.append(_anchor(anchors, token))
        cursor = match.end()
    output.append(segment[cursor:])
    return "".join(output), tuple(anchors)


def _is_table(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2


def _table_cells(line: _Line) -> list[tuple[int, int, str]]:
    positions = [match.start() for match in re.finditer(r"\|", line.content)]
    result: list[tuple[int, int, str]] = []
    for left, right in zip(positions, positions[1:]):
        raw = line.content[left + 1 : right]
        leading = len(raw) - len(raw.lstrip())
        trailing = len(raw) - len(raw.rstrip())
        start = line.start + left + 1 + leading
        end = line.start + right - trailing
        value = line.content[start - line.start : end - line.start]
        if value and not _TABLE_DELIMITER.fullmatch(value.strip()):
            result.append((start, end, value))
    return result


def _structure_fingerprint(text: str) -> str:
    structural: list[object] = []
    in_front_matter = False
    in_fence: str | None = None
    records = _lines(text)
    for index, line in enumerate(records):
        stripped = line.content.strip()
        fence = re.match(r"^(`{3,}|~{3,})", stripped)
        if fence:
            marker = fence.group(1)[0]
            structural.append(("fence", marker, stripped))
            in_fence = None if in_fence == marker else marker
            continue
        if in_fence:
            structural.append(("fenced", line.content))
            continue
        if index == 0 and stripped == "---":
            in_front_matter = True
            structural.append(("front", "---"))
            continue
        if in_front_matter:
            if stripped == "---":
                in_front_matter = False
                structural.append(("front", "---"))
            else:
                key = re.match(r"^(\s*[A-Za-z0-9_-]+\s*:\s*)", line.content)
                if key:
                    structural.append(("yaml-key", key.group(1)))
            continue
        prefix = _BLOCK_PREFIX.match(line.content)
        if prefix:
            structural.append(("block-prefix", prefix.group("prefix")))
        if _is_table(line.content):
            structural.append(("table", line.content.count("|")))

    _payload, anchors = _protect_markdown(text)
    structural.extend(("anchor", anchor.source) for anchor in anchors)
    return hashlib.sha256(repr(tuple(structural)).encode("utf-8")).hexdigest()


def _basic_validator(source: str, target: str) -> ValidationReport:
    source_breaks = re.findall(r"\r\n|\r|\n", source)
    target_breaks = re.findall(r"\r\n|\r|\n", target)
    source_fingerprint = _structure_fingerprint(source)
    target_fingerprint = _structure_fingerprint(target)
    if source_breaks != target_breaks:
        return ValidationReport(
            False,
            ("Line endings changed",),
            source_fingerprint,
            target_fingerprint,
        )
    if source_fingerprint != target_fingerprint:
        return ValidationReport(
            False,
            ("Markdown structure fingerprint changed",),
            source_fingerprint,
            target_fingerprint,
        )
    return ValidationReport(
        True,
        (),
        source_fingerprint,
        target_fingerprint,
    )


class MarkdownAdapter:
    adapter_id = "markdown-v2"

    def supports(self, logical_path: str, text: str) -> bool:
        del text
        normalized = logical_path.replace("\\", "/").lower()
        return normalized.endswith((".md", ".markdown", ".txt"))

    def plan(
        self,
        logical_path: str,
        text: str,
        target_locale: str,
        *,
        target_path_hint: str | None = None,
    ) -> TranslationPlan:
        units = self._collect_units(text)
        return TranslationPlan(
            adapter_id=self.adapter_id,
            logical_path=logical_path,
            source_text=text,
            target_path=_target_path(logical_path, target_locale, target_path_hint),
            units=tuple(units),
            validator=_basic_validator,
        )

    def _collect_units(self, text: str) -> list[TranslationUnit]:
        records = _lines(text)
        units: list[TranslationUnit] = []
        in_front_matter = False
        fence_marker: str | None = None
        index = 0

        def add_unit(start: int, end: int, value: str, kind: str) -> None:
            payload, anchors = _protect_markdown(value)
            if not re.search(r"[^\W\d_]", payload, re.UNICODE):
                return
            units.append(
                TranslationUnit(
                    id=f"{self.adapter_id}:{start}:{end}",
                    payload=payload,
                    start=start,
                    end=end,
                    context=payload,
                    anchors=anchors,
                    kind=kind,
                )
            )

        while index < len(records):
            line = records[index]
            stripped = line.content.strip()
            fence = re.match(r"^(`{3,}|~{3,})", stripped)
            if fence:
                marker = fence.group(1)[0]
                fence_marker = None if fence_marker == marker else marker
                index += 1
                continue
            if fence_marker is not None:
                index += 1
                continue
            if index == 0 and stripped == "---":
                in_front_matter = True
                index += 1
                continue
            if in_front_matter:
                if stripped == "---":
                    in_front_matter = False
                else:
                    title = re.match(r"^(\s*title\s*:\s*[\"']?)(.*?)([\"']?)$", line.content, re.I)
                    if title and title.group(2):
                        start = line.start + title.start(2)
                        add_unit(start, start + len(title.group(2)), title.group(2), "title")
                index += 1
                continue
            if not stripped:
                index += 1
                continue
            if _is_table(line.content):
                for start, end, value in _table_cells(line):
                    add_unit(start, end, value, "table-cell")
                index += 1
                continue
            if stripped.startswith("<") and stripped.endswith(">") and re.fullmatch(r"(?:<[^>]*>\s*)+", stripped):
                index += 1
                continue

            prefix = _BLOCK_PREFIX.match(line.content)
            if prefix:
                start = line.start + prefix.end()
                block_end = line.content_end
                end_index = index + 1
                if _LIST_PREFIX.match(line.content):
                    while end_index < len(records):
                        next_line = records[end_index]
                        next_stripped = next_line.content.strip()
                        if (
                            not next_stripped
                            or re.match(r"^(`{3,}|~{3,})", next_stripped)
                            or _is_table(next_line.content)
                            or _BLOCK_PREFIX.match(next_line.content)
                            or (
                                next_stripped.startswith("<")
                                and next_stripped.endswith(">")
                            )
                        ):
                            break
                        block_end = next_line.content_end
                        end_index += 1
                if start < block_end:
                    add_unit(start, block_end, text[start:block_end], "block")
                index = end_index
                continue

            paragraph_start = line.start + len(line.content) - len(line.content.lstrip())
            paragraph_end = line.content_end
            end_index = index + 1
            while end_index < len(records):
                next_line = records[end_index]
                next_stripped = next_line.content.strip()
                if (
                    not next_stripped
                    or re.match(r"^(`{3,}|~{3,})", next_stripped)
                    or _is_table(next_line.content)
                    or _BLOCK_PREFIX.match(next_line.content)
                    or (next_stripped.startswith("<") and next_stripped.endswith(">"))
                ):
                    break
                paragraph_end = next_line.content_end
                end_index += 1
            add_unit(
                paragraph_start,
                paragraph_end,
                text[paragraph_start:paragraph_end],
                "paragraph",
            )
            index = end_index

        return units
