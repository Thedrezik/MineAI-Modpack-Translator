from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Mapping

from .core import ProtectedFragment, TranslationPlan, TranslationUnit, ValidationError


_SOURCE_PATH_RE = re.compile(
    r"(^|/)patchouli_books/[^/]+/en_us/(?:categories|entries)/.+\.json$",
    re.IGNORECASE,
)
_PLACEHOLDER_RE = re.compile(r"\[#(\d+)#\]")
_PATCHOULI_TOKEN_RE = re.compile(r"\$\([^)]*\)|/\$")
_FORMAT_RE = re.compile(
    r"%(?!\s)(?:(?:\d+\$)?[-+#0 ,(<]*\d*(?:\.\d+)?"
    r"(?:[bBhHsScCdoxXeEfgGaA]|[tT][HIklMSLNpzZsQBbhAaCYyjmdeRTrDFc])|[%n])"
)
_MC_FORMAT_RE = re.compile(r"§[0-9A-FK-ORa-fk-or]")
_MESSAGE_FORMAT_RE = re.compile(r"\{\d+(?:,[^{}]+)?\}")
_LINE_BREAK_RE = re.compile(r"\r\n|\r|\n")
_BACKTICK_DOLLAR_RE = re.compile(r"`\$`")
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё]{2,}")

_TRANSLATABLE_TOP_LEVEL = {"name", "description"}
_TRANSLATABLE_PAGE_KEYS = {"text", "title", "heading", "name"}


@dataclass(frozen=True)
class _Member:
    key: str
    value: "_Node"


@dataclass(frozen=True)
class _Node:
    kind: str
    start: int
    end: int
    value: object
    members: tuple[_Member, ...] = ()
    items: tuple["_Node", ...] = ()


@dataclass(frozen=True)
class PatchouliFingerprint:
    locators: tuple[str, ...]
    skeleton: str


class PatchouliBookJsonAdapter:
    """Span-preserving adapter for localized Patchouli category/entry JSON.

    The adapter is intentionally context-aware. It translates only proven
    player-visible fields while resource locations, page types, recipes,
    icons, advancements, anchors and custom-page machine data stay immutable.
    Patchouli formatting/link directives are protected as exact placeholders.
    """

    name = "patchouli-book-json"

    def matches(self, path: str) -> bool:
        slash = "/" + path.replace("\\", "/").lstrip("/")
        return bool(_SOURCE_PATH_RE.search(slash))

    def target_path(self, path: str, target_code: str) -> str:
        slash = path.replace("\\", "/")
        normalized = "/" + slash.lstrip("/")
        match = _SOURCE_PATH_RE.search(normalized)
        if not match:
            raise ValueError(f"Unsupported Patchouli source path: {path}")
        marker = "/en_us/"
        lower = slash.lower()
        index = lower.find(marker)
        if index < 0:
            raise ValueError(f"Unsupported Patchouli source path: {path}")
        return slash[:index] + f"/{target_code}/" + slash[index + len(marker):]

    def prepare(self, path: str, source_text: str) -> TranslationPlan:
        root = self._parse(source_text)
        targets: list[tuple[str, _Node, str]] = []
        self._collect(root, "", targets)
        units: list[TranslationUnit] = []
        originals: dict[str, str] = {}
        for locator, node, value in targets:
            if not self._has_prose(value):
                continue
            masked, protected = self._protect(value)
            unit_id = f"json:{locator}"
            units.append(
                TranslationUnit(
                    id=unit_id,
                    text=masked,
                    start=node.start,
                    end=node.end,
                    kind="patchouli-text",
                    context=f"{path}:{locator}",
                    protected=protected,
                )
            )
            originals[unit_id] = value
        return TranslationPlan(
            path=path,
            source_text=source_text,
            units=tuple(units),
            metadata={
                "fingerprint": self.fingerprint(source_text),
                "originals": originals,
            },
        )

    def apply(self, plan: TranslationPlan, translations: Mapping[str, str]) -> str:
        known = {unit.id for unit in plan.units}
        unknown = set(translations) - known
        if unknown:
            raise ValidationError(f"Unknown translation unit ids: {sorted(unknown)!r}")
        originals = plan.metadata.get("originals")
        if not isinstance(originals, dict):
            raise ValidationError("Patchouli plan is missing original values")

        replacements: list[tuple[int, int, str]] = []
        for unit in plan.units:
            if unit.id not in translations:
                continue
            restored = self._restore(unit, translations[unit.id])
            original = originals.get(unit.id)
            if not isinstance(original, str):
                raise ValidationError(f"Missing original Patchouli value for {unit.id}")
            if self._line_breaks(restored) != self._line_breaks(original):
                raise ValidationError(f"Patchouli unit {unit.id} changed line-break structure")
            token = (
                plan.source_text[unit.start:unit.end]
                if restored == original
                else json.dumps(restored, ensure_ascii=False)
            )
            replacements.append((unit.start, unit.end, token))

        output = plan.source_text
        for start, end, token in sorted(replacements, reverse=True):
            output = output[:start] + token + output[end:]
        self.validate(plan.source_text, output)
        return output

    def validate(self, source_text: str, output_text: str) -> None:
        if self.fingerprint(source_text) != self.fingerprint(output_text):
            raise ValidationError("Patchouli JSON structure changed during reconstruction")

    def fingerprint(self, text: str) -> PatchouliFingerprint:
        root = self._parse(text)
        targets: list[tuple[str, _Node, str]] = []
        self._collect(root, "", targets)
        selected = [(loc, node) for loc, node, value in targets if self._has_prose(value)]
        out: list[str] = []
        cursor = 0
        for locator, node in sorted(selected, key=lambda item: item[1].start):
            out.append(text[cursor:node.start])
            out.append('"<mineai-patchouli-text>"')
            cursor = node.end
        out.append(text[cursor:])
        return PatchouliFingerprint(
            locators=tuple(locator for locator, _ in selected),
            skeleton="".join(out),
        )

    def _collect(self, root: _Node, path: str, out: list[tuple[str, _Node, str]]) -> None:
        if root.kind != "object":
            raise ValidationError("Patchouli document must be a JSON object")
        members = {member.key: member.value for member in root.members}
        for key in _TRANSLATABLE_TOP_LEVEL:
            node = members.get(key)
            if node is not None and node.kind == "string":
                assert isinstance(node.value, str)
                out.append((f"/{self._escape(key)}", node, node.value))

        pages = members.get("pages")
        if pages is None:
            return
        if pages.kind != "array":
            raise ValidationError("Patchouli 'pages' must be an array")
        for index, page in enumerate(pages.items):
            if page.kind != "object":
                raise ValidationError("Patchouli page must be an object")
            for member in page.members:
                if member.value.kind != "string":
                    continue
                key = member.key
                if (
                    key in _TRANSLATABLE_PAGE_KEYS
                    or key.endswith(".heading")
                    or key.endswith(".text")
                ):
                    assert isinstance(member.value.value, str)
                    out.append(
                        (
                            f"/pages/{index}/{self._escape(key)}",
                            member.value,
                            member.value.value,
                        )
                    )

    def _parse(self, text: str) -> _Node:
        node, end = self._parse_value(text, self._skip_ws(text, 0))
        if self._skip_ws(text, end) != len(text):
            raise ValidationError("Trailing data after Patchouli JSON")
        return node

    def _parse_value(self, text: str, index: int) -> tuple[_Node, int]:
        index = self._skip_ws(text, index)
        if index >= len(text):
            raise ValidationError("Unexpected end of Patchouli JSON")
        if text[index] == '"':
            end = self._scan_string_end(text, index)
            try:
                value = json.loads(text[index:end])
            except json.JSONDecodeError as exc:
                raise ValidationError("Invalid Patchouli JSON string") from exc
            return _Node("string", index, end, value), end
        if text[index] == "{":
            start = index
            index += 1
            members: list[_Member] = []
            seen: set[str] = set()
            while True:
                index = self._skip_ws(text, index)
                if index < len(text) and text[index] == "}":
                    return _Node("object", start, index + 1, None, tuple(members)), index + 1
                if index >= len(text) or text[index] != '"':
                    raise ValidationError("Patchouli JSON object key must be a string")
                key_end = self._scan_string_end(text, index)
                try:
                    key = json.loads(text[index:key_end])
                except json.JSONDecodeError as exc:
                    raise ValidationError("Invalid Patchouli JSON key") from exc
                if key in seen:
                    raise ValidationError(f"Duplicate Patchouli JSON key: {key}")
                seen.add(key)
                index = self._skip_ws(text, key_end)
                if index >= len(text) or text[index] != ":":
                    raise ValidationError("Missing ':' after Patchouli JSON key")
                value, index = self._parse_value(text, index + 1)
                members.append(_Member(key, value))
                index = self._skip_ws(text, index)
                if index < len(text) and text[index] == ",":
                    index += 1
                    continue
                if index < len(text) and text[index] == "}":
                    return _Node("object", start, index + 1, None, tuple(members)), index + 1
                raise ValidationError("Expected ',' or '}' in Patchouli JSON object")
        if text[index] == "[":
            start = index
            index += 1
            items: list[_Node] = []
            while True:
                index = self._skip_ws(text, index)
                if index < len(text) and text[index] == "]":
                    return _Node("array", start, index + 1, None, items=tuple(items)), index + 1
                item, index = self._parse_value(text, index)
                items.append(item)
                index = self._skip_ws(text, index)
                if index < len(text) and text[index] == ",":
                    index += 1
                    continue
                if index < len(text) and text[index] == "]":
                    return _Node("array", start, index + 1, None, items=tuple(items)), index + 1
                raise ValidationError("Expected ',' or ']' in Patchouli JSON array")
        decoder = json.JSONDecoder()
        try:
            value, end = decoder.raw_decode(text, index)
        except json.JSONDecodeError as exc:
            raise ValidationError("Invalid Patchouli JSON value") from exc
        return _Node("scalar", index, end, value), end

    @staticmethod
    def _skip_ws(text: str, index: int) -> int:
        while index < len(text) and text[index] in " \t\r\n":
            index += 1
        return index

    @staticmethod
    def _scan_string_end(text: str, start: int) -> int:
        index = start + 1
        while index < len(text):
            if text[index] == "\\":
                index += 2
                continue
            if text[index] == '"':
                return index + 1
            if ord(text[index]) < 0x20:
                raise ValidationError("Unescaped control character in Patchouli JSON string")
            index += 1
        raise ValidationError("Unterminated Patchouli JSON string")

    def _protect(self, text: str) -> tuple[str, tuple[ProtectedFragment, ...]]:
        spans: list[tuple[int, int]] = []
        literal_ids = [int(match.group(1)) for match in _PLACEHOLDER_RE.finditer(text)]
        spans.extend((match.start(), match.end()) for match in _PLACEHOLDER_RE.finditer(text))
        for regex in (
            _PATCHOULI_TOKEN_RE,
            _FORMAT_RE,
            _MC_FORMAT_RE,
            _MESSAGE_FORMAT_RE,
            _LINE_BREAK_RE,
            _BACKTICK_DOLLAR_RE,
        ):
            spans.extend((match.start(), match.end()) for match in regex.finditer(text))
        merged: list[list[int]] = []
        for start, end in sorted(spans):
            if start >= end:
                continue
            if not merged or start >= merged[-1][1]:
                merged.append([start, end])
            elif end > merged[-1][1]:
                merged[-1][1] = end
        base = max(literal_ids) + 1 if literal_ids else 0
        out: list[str] = []
        protected: list[ProtectedFragment] = []
        cursor = 0
        for offset, (start, end) in enumerate(merged):
            out.append(text[cursor:start])
            placeholder = f"[#{base + offset}#]"
            out.append(placeholder)
            protected.append(ProtectedFragment(placeholder, text[start:end]))
            cursor = end
        out.append(text[cursor:])
        return "".join(out), tuple(protected)

    @staticmethod
    def _restore(unit: TranslationUnit, translated: str) -> str:
        expected = [fragment.placeholder for fragment in unit.protected]
        actual = [f"[#{value}#]" for value in _PLACEHOLDER_RE.findall(translated)]
        if expected != actual:
            raise ValidationError(f"Unit {unit.id} changed protected placeholder order")
        restored = translated
        for fragment in unit.protected:
            restored = restored.replace(fragment.placeholder, fragment.value)
        return restored

    @staticmethod
    def _line_breaks(value: str) -> tuple[int, int]:
        return value.count("\n"), value.count("\r")

    @staticmethod
    def _escape(value: str) -> str:
        return value.replace("~", "~0").replace("/", "~1")

    @staticmethod
    def _has_prose(value: str) -> bool:
        return bool(_WORD_RE.search(value))
