"""Lossless separation of visible prose from immutable game formatting."""

from dataclasses import dataclass
import re

from formatkit.contracts import (
    ANCHOR_PATTERN,
    FormatValidationError,
    ProtectedAnchor,
    normalize_anchor_boundaries,
)
from formatkit.tokenizer import (
    MODONOMICON_STYLE_SOURCE,
    STYLE_RESET_BOUNDARY_SOURCE,
)
from mineai.constants import IGNORE_TERMS
from mineai.text_processing import (
    COMPOUND_TECHNICAL_TOKEN_PATTERN,
    JSON_TEXT_VALUE_PATTERN,
)


_IGNORE_ALTERNATION = "|".join(
    re.escape(term)
    for term in sorted(IGNORE_TERMS, key=lambda value: (-len(value), value))
)

_IMMUTABLE_PATTERN = re.compile(
    r"(?P<code>(?P<ticks>`+)[^`\r\n]*(?P=ticks))|"
    r"(?P<tag><(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^'\">\r\n])*>)|"
    rf"(?P<modonomicon_style>{MODONOMICON_STYLE_SOURCE})|"
    r"(?P<patchouli_tooltip>\$\(t:[^\r\n)]*\))|"
    r"(?P<patchouli>\$\([^\r\n)]*\))|"
    r"(?P<patchouli_close>/\$)|"
    r"(?P<script_variable>\$[A-Za-z_][A-Za-z0-9_]*=?)|"
    r"(?P<function_name>\b[A-Za-z_][A-Za-z0-9_]*(?=\())|"
    r"(?P<resource_id>\b[a-z0-9_.-]+:[a-z0-9_./-]+\b)|"
    r"(?P<member_name>\.[A-Za-z_][A-Za-z0-9_]*)|"
    r"(?P<markdown_tail>\](?:\((?:[^()\r\n]|\([^()\r\n]*\))*\)|"
    r"\[[^\]\r\n]*\]))|"
    r"(?P<game_ref>\[[a-z0-9_.-]+:[a-z0-9_./-]+\]|"
    r"\([a-z0-9_.-]+:[a-z0-9_./-]+\))|"
    r"(?P<markdown_open>!\[|\[)|"
    r"(?P<escape>\\[^\r\n])|"
    rf"(?P<style_reset_boundary>{STYLE_RESET_BOUNDARY_SOURCE})|"
    r"(?P<minecraft_code>[&§]x(?:[&§][0-9a-f]){6}|"
    r"&#[0-9a-f]{6}|[&§][0-9a-fk-or])|"
    r"(?P<placeholder>#[A-Za-z_][A-Za-z0-9_.:/-]*#|\{[^}\r\n]+\}|"
    r"%[0-9.,]*\$?[a-zA-Z%])|"
    r"(?P<emphasis>\*{1,3}|_{1,3}|~~)|"
    r"(?P<newline>\r\n|\r|\n)|"
    r"(?P<compound>" + COMPOUND_TECHNICAL_TOKEN_PATTERN.pattern + r")|"
    r"(?P<term>(?<![A-Za-z0-9])(?:" + _IGNORE_ALTERNATION + r")(?![A-Za-z0-9]))",
    flags=re.IGNORECASE,
)

_UNSAFE_TRANSLATED_TEXT_PATTERN = re.compile(
    r"<(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^'\">\r\n])*>|"
    + MODONOMICON_STYLE_SOURCE
    + r"|"
    r"\$\([^\r\n)]*\)|"
    r"/\$|\$[A-Za-z_][A-Za-z0-9_]*=?|"
    r"\](?:\((?:[^()\r\n]|\([^()\r\n]*\))*\)|\[[^\]\r\n]*\])|"
    r"!\[|"
    r"\\[*_~`]|"
    r"[&§]x(?:[&§][0-9a-f]){6}|&#[0-9a-f]{6}|[&§][0-9a-fk-or]|"
    r"#[A-Za-z_][A-Za-z0-9_.:/-]*#|"
    r"\{[^}\r\n]+\}|"
    r"%[0-9.,]*\$?[a-zA-Z%]|"
    r"`+|\*{1,3}|_{2,3}|~~|"
    r"\r|\n",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class RichTextPart:
    index: int
    text: str
    translatable: bool


@dataclass(frozen=True)
class RichTextTemplate:
    parts: tuple[RichTextPart, ...]

    def translatable_parts(self) -> tuple[RichTextPart, ...]:
        return tuple(part for part in self.parts if part.translatable)

    def render(self, translations: dict[int, str]) -> str:
        return "".join(
            translations.get(part.index, part.text)
            if part.translatable
            else part.text
            for part in self.parts
        )

    def translation_payload(
        self,
    ) -> tuple[str, tuple[ProtectedAnchor, ...]]:
        """Return one contextual prose unit with immutable spans as anchors."""
        output: list[str] = []
        anchors: list[ProtectedAnchor] = []
        immutable: list[str] = []

        def flush_immutable() -> None:
            if not immutable:
                return
            token = f"⟦FK{len(anchors):04d}⟧"
            source = "".join(immutable)
            anchors.append(ProtectedAnchor(token=token, source=source))
            output.append(token)
            immutable.clear()

        for part in self.parts:
            if part.translatable:
                flush_immutable()
                output.append(part.text)
            else:
                immutable.append(part.text)
        flush_immutable()
        return "".join(output), tuple(anchors)

    def render_translation(self, candidate: str) -> str:
        """Restore every immutable source span into a translated payload."""
        payload, anchors = self.translation_payload()
        expected = tuple(anchor.token for anchor in anchors)
        actual = tuple(ANCHOR_PATTERN.findall(candidate))
        if actual != expected:
            raise FormatValidationError(
                f"Rich-text anchors changed: {expected!r} -> {actual!r}"
            )
        candidate = normalize_anchor_boundaries(payload, candidate, anchors)
        for anchor in anchors:
            candidate = candidate.replace(anchor.token, anchor.source, 1)
        return candidate


def _append_part(
    parts: list[RichTextPart],
    text: str,
    *,
    translatable: bool,
) -> None:
    if not text:
        return
    parts.append(
        RichTextPart(
            index=len(parts),
            text=text,
            translatable=translatable,
        )
    )


def _append_visible(parts: list[RichTextPart], text: str) -> None:
    """Keep boundary whitespace outside model-controlled text nodes."""
    if not text:
        return
    if not text.strip():
        _append_part(parts, text, translatable=False)
        return
    leading_length = len(text) - len(text.lstrip())
    trailing_length = len(text) - len(text.rstrip())
    body_end = len(text) - trailing_length if trailing_length else len(text)
    if leading_length:
        _append_part(parts, text[:leading_length], translatable=False)
    _append_part(
        parts,
        text[leading_length:body_end],
        translatable=True,
    )
    if trailing_length:
        _append_part(parts, text[body_end:], translatable=False)


def _parse_standard(text: str) -> list[RichTextPart]:
    parts: list[RichTextPart] = []
    cursor = 0
    for match in _IMMUTABLE_PATTERN.finditer(text):
        _append_visible(parts, text[cursor : match.start()])
        if match.group("patchouli_tooltip") is not None:
            tooltip = match.group(0)
            _append_part(parts, tooltip[:4], translatable=False)
            for nested in _parse_standard(tooltip[4:-1]):
                _append_part(
                    parts,
                    nested.text,
                    translatable=nested.translatable,
                )
            _append_part(parts, tooltip[-1:], translatable=False)
        else:
            _append_part(parts, match.group(0), translatable=False)
        cursor = match.end()
    _append_visible(parts, text[cursor:])
    return parts


def parse_rich_text(text: str) -> RichTextTemplate:
    """Return a byte-preserving template whose syntax never reaches the LLM."""
    json_matches = list(JSON_TEXT_VALUE_PATTERN.finditer(text))
    if not json_matches:
        return RichTextTemplate(parts=tuple(_parse_standard(text)))

    parts: list[RichTextPart] = []
    cursor = 0
    for match in json_matches:
        _append_part(
            parts,
            text[cursor : match.start("value")],
            translatable=False,
        )
        for nested in _parse_standard(match.group("value")):
            _append_part(
                parts,
                nested.text,
                translatable=nested.translatable,
            )
        cursor = match.end("value")
    _append_part(parts, text[cursor:], translatable=False)
    return RichTextTemplate(parts=tuple(parts))


def contains_unsafe_formatting(text: str) -> bool:
    """True when a translated prose node tries to introduce new syntax."""
    return bool(_UNSAFE_TRANSLATED_TEXT_PATTERN.search(text))
