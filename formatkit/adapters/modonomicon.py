"""Lossless adapter for Modonomicon data-pack books."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

from formatkit.adapters.markdown import _protect_markdown
from formatkit.contracts import TranslationPlan, TranslationUnit, ValidationReport


_PATH = re.compile(
    r"(?:^|/)data/[^/]+/modonomicon/books/.+\.json$",
    re.IGNORECASE,
)
_TRANSLATABLE_KEY = re.compile(
    r"^(?:names?|titles?\d*|texts?\d*|descriptions?|subtexts?|subtitles?|"
    r"labels?|headers?|headings?|tooltips?|hover_text|link_text|"
    r"entity_name|multiblock_name)$",
    re.IGNORECASE,
)
_LOCALIZATION_KEY = re.compile(
    r"^(?=[a-z0-9_./-]*\.)[a-z0-9_-]+(?:[./][a-z0-9_-]+)+$"
)
_BOOK_RESOURCE_PATH = re.compile(
    r"(?:^|/)data/(?P<namespace>[^/]+)/modonomicon/books/"
    r"(?P<book>[^/]+)/(?P<relative>.+)\.json$",
    re.IGNORECASE,
)


def is_modonomicon_path(logical_path: str) -> bool:
    return bool(_PATH.search(logical_path.replace("\\", "/")))


@dataclass(frozen=True)
class _StringToken:
    path: tuple[str | int, ...]
    start: int
    end: int
    value: str
    is_key: bool


@dataclass(frozen=True)
class ModonomiconLocalization:
    data_text: str
    source_lang_path: str
    target_lang_path: str
    source_entries: dict[str, str]
    target_entries: dict[str, str]


def _decode_string(raw: str) -> str:
    """Decode Gson-lenient strings while retaining their exact source span."""
    escaped: list[str] = []
    for char in raw:
        if char == "\r":
            escaped.append("\\r")
        elif char == "\n":
            escaped.append("\\n")
        elif char == "\t":
            escaped.append("\\t")
        elif ord(char) < 0x20:
            escaped.append(f"\\u{ord(char):04x}")
        else:
            escaped.append(char)
    return json.loads("".join(escaped))


class _JsonWalker:
    """Track JSON string spans without reserializing the surrounding document."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.index = 0
        self.tokens: list[_StringToken] = []

    def walk(self) -> list[_StringToken]:
        self._value(())
        self._space()
        if self.index != len(self.text):
            raise ValueError("Trailing Modonomicon JSON content")
        return self.tokens

    def _space(self) -> None:
        while self.index < len(self.text) and self.text[self.index].isspace():
            self.index += 1

    def _string(self) -> tuple[int, int, str]:
        self._space()
        start = self.index
        if self.index >= len(self.text) or self.text[self.index] != '"':
            raise ValueError("Expected Modonomicon JSON string")
        self.index += 1
        escaped = False
        while self.index < len(self.text):
            char = self.text[self.index]
            self.index += 1
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                raw = self.text[start:self.index]
                return start, self.index, _decode_string(raw)
        raise ValueError("Unterminated Modonomicon JSON string")

    def _value(self, path: tuple[str | int, ...]) -> None:
        self._space()
        if self.index >= len(self.text):
            raise ValueError("Missing Modonomicon JSON value")
        char = self.text[self.index]
        if char == '"':
            start, end, value = self._string()
            self.tokens.append(_StringToken(path, start, end, value, False))
            return
        if char == "{":
            self.index += 1
            self._space()
            if self.index < len(self.text) and self.text[self.index] == "}":
                self.index += 1
                return
            while True:
                start, end, key = self._string()
                child = path + (key,)
                self.tokens.append(_StringToken(child, start, end, key, True))
                self._space()
                if self.index >= len(self.text) or self.text[self.index] != ":":
                    raise ValueError("Expected Modonomicon JSON colon")
                self.index += 1
                self._value(child)
                self._space()
                if self.index < len(self.text) and self.text[self.index] == ",":
                    self.index += 1
                    continue
                if self.index < len(self.text) and self.text[self.index] == "}":
                    self.index += 1
                    return
                raise ValueError("Expected Modonomicon object delimiter")
        if char == "[":
            self.index += 1
            self._space()
            if self.index < len(self.text) and self.text[self.index] == "]":
                self.index += 1
                return
            item = 0
            while True:
                self._value(path + (item,))
                item += 1
                self._space()
                if self.index < len(self.text) and self.text[self.index] == ",":
                    self.index += 1
                    continue
                if self.index < len(self.text) and self.text[self.index] == "]":
                    self.index += 1
                    return
                raise ValueError("Expected Modonomicon array delimiter")
        match = re.match(
            r"(?:-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?|"
            r"true|false|null)",
            self.text[self.index :],
        )
        if match is None:
            raise ValueError("Invalid Modonomicon JSON value")
        self.index += len(match.group(0))


def _is_selected_path(path: tuple[str | int, ...]) -> bool:
    return bool(path and isinstance(path[-1], str) and _TRANSLATABLE_KEY.fullmatch(path[-1]))


def _description_id(
    logical_path: str,
    value_path: tuple[str | int, ...],
) -> tuple[str, str]:
    normalized = logical_path.replace("\\", "/")
    match = _BOOK_RESOURCE_PATH.search(normalized)
    if match is None:
        raise ValueError(f"Unsupported Modonomicon book path: {logical_path}")

    def clean(value: object) -> str:
        text = re.sub(r"[^a-z0-9_-]+", "_", str(value).casefold())
        return text.strip("_") or "value"

    relative = match.group("relative").split("/")
    field_parts = [
        f"page_{part}" if isinstance(part, int) else clean(part)
        for part in value_path
    ]
    parts = (
        "mineai",
        "book",
        clean(match.group("namespace")),
        clean(match.group("book")),
        *(clean(part) for part in relative),
        *field_parts,
    )
    return match.group("namespace"), ".".join(parts)


def build_localized_overlay(
    logical_path: str,
    source_text: str,
    translated_text: str,
    target_locale: str,
) -> ModonomiconLocalization:
    """Move literal book text into stable language keys without reformatting JSON."""
    source_tokens = _JsonWalker(source_text).walk()
    target_by_path = {
        token.path: token
        for token in _JsonWalker(translated_text).walk()
        if not token.is_key
    }
    replacements: list[tuple[int, int, str]] = []
    source_entries: dict[str, str] = {}
    target_entries: dict[str, str] = {}
    namespace = ""
    for token in source_tokens:
        if token.is_key or not _is_selected_path(token.path):
            continue
        source_value = token.value.strip()
        if not source_value or _LOCALIZATION_KEY.fullmatch(source_value):
            continue
        target_token = target_by_path.get(token.path)
        if target_token is None:
            raise ValueError(f"Missing translated Modonomicon field: {token.path!r}")
        namespace, description_id = _description_id(logical_path, token.path)
        source_entries[description_id] = token.value
        target_entries[description_id] = target_token.value
        replacements.append(
            (
                token.start,
                token.end,
                json.dumps(description_id, ensure_ascii=False),
            )
        )

    data_text = source_text
    for start, end, replacement in reversed(replacements):
        data_text = data_text[:start] + replacement + data_text[end:]
    report = _validator(source_text, data_text)
    if not report.ok:
        raise ValueError("; ".join(report.errors))
    return ModonomiconLocalization(
        data_text=data_text,
        source_lang_path=f"assets/{namespace}/lang/en_us.json",
        target_lang_path=f"assets/{namespace}/lang/{target_locale}.json",
        source_entries=source_entries,
        target_entries=target_entries,
    )


def _normalized_structure(text: str) -> tuple[object, ...]:
    tokens = _JsonWalker(text).walk()
    return tuple(
        (
            token.path,
            token.is_key,
            "<mineai-translatable>" if not token.is_key and _is_selected_path(token.path) else token.value,
        )
        for token in tokens
    )


def _validator(source: str, target: str) -> ValidationReport:
    try:
        source_structure = _normalized_structure(source)
        target_structure = _normalized_structure(target)
    except (ValueError, json.JSONDecodeError) as exc:
        return ValidationReport(False, (f"Invalid Modonomicon JSON: {exc}",))
    source_hash = hashlib.sha256(repr(source_structure).encode("utf-8")).hexdigest()
    target_hash = hashlib.sha256(repr(target_structure).encode("utf-8")).hexdigest()
    return ValidationReport(
        source_hash == target_hash,
        () if source_hash == target_hash else ("Modonomicon non-text structure changed",),
        source_hash,
        target_hash,
    )


class ModonomiconAdapter:
    adapter_id = "modonomicon-json-v1"

    def supports(self, logical_path: str, text: str) -> bool:
        del text
        return is_modonomicon_path(logical_path)

    def plan(
        self,
        logical_path: str,
        text: str,
        target_locale: str,
        *,
        target_path_hint: str | None = None,
    ) -> TranslationPlan:
        del target_locale
        units: list[TranslationUnit] = []
        for token in _JsonWalker(text).walk():
            if token.is_key or not _is_selected_path(token.path):
                continue
            value = token.value
            if not value.strip() or _LOCALIZATION_KEY.fullmatch(value.strip()):
                continue
            payload, anchors = _protect_markdown(value)
            units.append(
                TranslationUnit(
                    id=f"{self.adapter_id}:{len(units):05d}",
                    payload=payload,
                    start=token.start,
                    end=token.end,
                    context=payload,
                    anchors=anchors,
                    kind="modonomicon-text",
                    encoding="json-string-lossless",
                )
            )
        return TranslationPlan(
            self.adapter_id,
            logical_path,
            text,
            target_path_hint or logical_path,
            tuple(units),
            _validator,
        )

    def companion_lang_keys(self, text: str) -> set[str]:
        return {
            token.value.strip()
            for token in _JsonWalker(text).walk()
            if not token.is_key
            and _is_selected_path(token.path)
            and _LOCALIZATION_KEY.fullmatch(token.value.strip())
        }
