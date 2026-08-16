from __future__ import annotations

import re
from dataclasses import replace

from .core import ProtectedFragment, TranslationPlan, ValidationError
from .locale_safe import (
    LocaleMergePlanner as _BaseLocaleMergePlanner,
    MinecraftLangJsonAdapter as _BaseMinecraftLangJsonAdapter,
)
from .modonomicon_gson import ModonomiconGsonSpanJsonAdapter as _SpanJsonAdapter


_BOOK_PATH_RE = re.compile(
    r"(^|/)data/[^/]+/modonomicon/books/[^/]+/"
    r"(?:book\.json|(?:categories|entries|commands)/.+\.json)$",
    re.IGNORECASE,
)
_DESCRIPTION_ID_RE = re.compile(r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+){2,}$")
_RESOURCE_LOCATION_RE = re.compile(r"^[a-z0-9_.-]+:[a-z0-9_./-]+$", re.IGNORECASE)
_PLACEHOLDER_RE = re.compile(r"\[#(\d+)#\]")

# Real Modonomicon 1.21.1 corpus + current upstream localization syntax.
_NESTED_TRANSLATION_RE = re.compile(r"<t>[^<>\r\n]+(?:</t>|<t>)")
_COLOR_TOKEN_RE = re.compile(r"\[#\]\([^()\r\n]*\)")
_LINK_DEST_RE = re.compile(
    r"(?<=\])\((?:entry|category|item|command|page)://[^()\r\n]+\)",
    re.IGNORECASE,
)
_STAR_RUN_RE = re.compile(r"(?<!\\)\*{1,3}")
_UNDERLINE_RE = re.compile(r"(?<!\\)\+\+")
_STRIKE_RE = re.compile(r"(?<!\\)~~")

_TOP_LEVEL_TEXT_KEYS = frozenset(
    {"name", "description", "tooltip", "success_message", "failure_message"}
)
_PAGE_TEXT_KEYS = frozenset(
    {"text", "title", "title1", "title2", "multiblock_name"}
)


def _merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if start >= end:
            continue
        if not merged or start >= merged[-1][1]:
            merged.append([start, end])
        elif end > merged[-1][1]:
            merged[-1][1] = end
    return [(start, end) for start, end in merged]


def modonomicon_markup_spans(text: str) -> list[tuple[int, int]]:
    """Return exact source spans that are structural Modonomicon markup."""

    spans: list[tuple[int, int]] = []
    for regex in (
        _NESTED_TRANSLATION_RE,
        _COLOR_TOKEN_RE,
        _LINK_DEST_RE,
        _STAR_RUN_RE,
        _UNDERLINE_RE,
        _STRIKE_RE,
    ):
        spans.extend((match.start(), match.end()) for match in regex.finditer(text))
    return _merge_spans(spans)


def protect_modonomicon_markup(
    text: str,
    protected: tuple[ProtectedFragment, ...] = (),
) -> tuple[str, tuple[ProtectedFragment, ...]]:
    """Layer Modonomicon markup protection after an existing token layer."""

    spans = []
    for start, end in modonomicon_markup_spans(text):
        if _PLACEHOLDER_RE.search(text[start:end]):
            continue
        spans.append((start, end))
    spans = _merge_spans(spans)
    if not spans:
        return text, protected

    placeholder_ids = [int(match.group(1)) for match in _PLACEHOLDER_RE.finditer(text)]
    next_id = max(placeholder_ids) + 1 if placeholder_ids else 0
    out: list[str] = []
    extra: list[ProtectedFragment] = []
    cursor = 0
    for offset, (start, end) in enumerate(spans):
        out.append(text[cursor:start])
        placeholder = f"[#{next_id + offset}#]"
        out.append(placeholder)
        extra.append(ProtectedFragment(placeholder, text[start:end]))
        cursor = end
    out.append(text[cursor:])
    masked = "".join(out)
    fragments = protected + tuple(extra)
    ordered = tuple(
        sorted(fragments, key=lambda fragment: masked.index(fragment.placeholder))
    )
    return masked, ordered


def modonomicon_markup_fingerprint(text: str) -> tuple[str, ...]:
    spans = modonomicon_markup_spans(text)
    return tuple(text[start:end] for start, end in spans)


def _is_description_id(value: str) -> bool:
    return bool(_DESCRIPTION_ID_RE.fullmatch(value))


def _is_resource_location(value: str) -> bool:
    return bool(_RESOURCE_LOCATION_RE.fullmatch(value))


def _is_modonomicon_lang_key(value: str) -> bool:
    return value.startswith("book.")


class ModonomiconBookJsonAdapter(_SpanJsonAdapter):
    """Span-preserving adapter for legacy inline Modonomicon book prose."""

    name = "modonomicon-book-json"

    def matches(self, path: str) -> bool:
        slash = "/" + path.replace("\\", "/").lstrip("/")
        return bool(_BOOK_PATH_RE.search(slash))

    def target_path(self, path: str, target_code: str) -> str:
        raise ValueError(
            "Modonomicon book JSON has no locale-specific target path; modern books use lang JSON"
        )

    def _collect(self, root, path: str, out: list) -> None:
        if root.kind != "object":
            raise ValidationError("Modonomicon book document must be a JSON object")

        members = {member.key: member.value for member in root.members}
        # Preserve physical source order. Stable unit order matters to external
        # translators, XLIFF and future translation-memory keys.
        for member in root.members:
            if member.key not in _TOP_LEVEL_TEXT_KEYS or member.value.kind != "string":
                continue
            value = member.value.value
            assert isinstance(value, str)
            if self._is_inline_prose(value):
                out.append((f"/{self._escape(member.key)}", member.value, value))

        pages = members.get("pages")
        if pages is not None:
            if pages.kind != "array":
                raise ValidationError("Modonomicon 'pages' must be an array")
            for index, page in enumerate(pages.items):
                if page.kind != "object":
                    raise ValidationError("Modonomicon page must be an object")
                for member in page.members:
                    if member.value.kind != "string":
                        continue
                    key = member.key
                    if key not in _PAGE_TEXT_KEYS and not key.endswith((".text", ".title")):
                        continue
                    value = member.value.value
                    assert isinstance(value, str)
                    if self._is_inline_prose(value):
                        out.append(
                            (
                                f"/pages/{index}/{self._escape(key)}",
                                member.value,
                                value,
                            )
                        )

    @staticmethod
    def _is_inline_prose(value: str) -> bool:
        if not value or _is_description_id(value) or _is_resource_location(value):
            return False
        return _SpanJsonAdapter._has_prose(value)

    def _protect(self, text: str) -> tuple[str, tuple[ProtectedFragment, ...]]:
        masked, protected = super()._protect(text)
        return protect_modonomicon_markup(masked, protected)

    def validate(self, source_text: str, output_text: str) -> None:
        super().validate(source_text, output_text)
        before = self._markup_by_locator(source_text)
        after = self._markup_by_locator(output_text)
        if before != after:
            raise ValidationError("Modonomicon inline markup changed during reconstruction")

    def _markup_by_locator(self, text: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
        root = self._parse(text)
        targets: list[tuple[str, object, str]] = []
        self._collect(root, "", targets)
        return tuple(
            (locator, modonomicon_markup_fingerprint(value))
            for locator, _node, value in targets
        )


class ModonomiconAwareMinecraftLangJsonAdapter(_BaseMinecraftLangJsonAdapter):
    """Minecraft lang adapter hardened only for proven Modonomicon book keys."""

    name = "minecraft-lang-json"

    def prepare(self, path: str, source_text: str) -> TranslationPlan:
        plan = super().prepare(path, source_text)
        units = []
        for unit in plan.units:
            if _is_modonomicon_lang_key(unit.context):
                masked, protected = protect_modonomicon_markup(unit.text, unit.protected)
                unit = replace(unit, text=masked, protected=protected)
            units.append(unit)
        return replace(plan, units=tuple(units))

    def validate(self, source_text: str, output_text: str) -> None:
        super().validate(source_text, output_text)
        before = self._markup_by_key(source_text)
        after = self._markup_by_key(output_text)
        if before != after:
            raise ValidationError("Modonomicon lang markup changed during reconstruction")

    def _markup_by_key(self, text: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
        entries = self._parse_entries(text)
        out = []
        for entry in entries:
            if not (
                entry.is_string
                and isinstance(entry.value, str)
                and _is_modonomicon_lang_key(entry.key)
            ):
                continue
            fingerprint = modonomicon_markup_fingerprint(entry.value)
            if fingerprint:
                out.append((entry.key, fingerprint))
        return tuple(out)


class ModonomiconAwareLocaleMergePlanner(_BaseLocaleMergePlanner):
    """Public locale merge planner using the Modonomicon-aware lang adapter."""

    def __init__(self, adapter: ModonomiconAwareMinecraftLangJsonAdapter | None = None) -> None:
        super().__init__(adapter or ModonomiconAwareMinecraftLangJsonAdapter())
