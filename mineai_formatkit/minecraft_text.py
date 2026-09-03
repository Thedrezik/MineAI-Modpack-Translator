from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Mapping

from .core import ProtectedFragment, TranslationPlan, TranslationUnit, ValidationError


_PATH_RE = re.compile(
    r"(^|/)data/[^/]+/(?:advancements?|loot_tables?|enchantments?)/.+\.json$",
    re.IGNORECASE,
)
_FORMAT_RE = re.compile(
    r"%(?!\s)(?:(?:\d+\$)?[-+#0 ,(<]*\d*(?:\.\d+)?"
    r"(?:[bBhHsScCdoxXeEfgGaA]|[tT][HIklMSLNpzZsQBbhAaCYyjmdeRTrDFc])|[%n])"
)
_MC_FORMAT_RE = re.compile(r"§[0-9A-FK-ORa-fk-or]")
_MESSAGE_FORMAT_RE = re.compile(r"\{\d+(?:,[^{}]+)?\}")
_PLACEHOLDER_RE = re.compile(r"\[#(\d+)#\]")
_LINE_BREAK_RE = re.compile(r"\r\n|\r|\n")
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё]{2,}")
_RESOURCE_LOCATION_RE = re.compile(r"^[a-z0-9_.-]+:[a-z0-9_./-]+$", re.IGNORECASE)


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
class TextComponentFingerprint:
    locators: tuple[str, ...]
    skeleton: str


class MinecraftTextComponentAdapter:
    """Extract only known player-visible Minecraft JSON text-component fields."""

    name = "minecraft-text-components"

    def matches(self, path: str) -> bool:
        slash = "/" + path.replace("\\", "/").lstrip("/")
        return bool(_PATH_RE.search(slash))

    def prepare(self, path: str, source_text: str) -> TranslationPlan:
        root = self._parse(source_text)
        targets: list[tuple[str, _Node, str, str]] = []
        self._collect(root, "", targets)
        units: list[TranslationUnit] = []
        encodings: dict[str, str] = {}
        originals: dict[str, str] = {}
        for locator, node, text, encoding in targets:
            if not self._has_prose(text):
                continue
            masked, protected = self._protect(text)
            unit_id = f"json:{locator}"
            units.append(TranslationUnit(
                id=unit_id,
                text=masked,
                start=node.start,
                end=node.end,
                kind="minecraft-text-component",
                context=locator,
                protected=protected,
            ))
            encodings[unit_id] = encoding
            originals[unit_id] = text
        return TranslationPlan(
            path=path,
            source_text=source_text,
            units=tuple(units),
            metadata={
                "fingerprint": self.fingerprint(source_text),
                "encodings": encodings,
                "originals": originals,
            },
        )

    def apply(self, plan: TranslationPlan, translations: Mapping[str, str]) -> str:
        known = {u.id for u in plan.units}
        unknown = set(translations) - known
        if unknown:
            raise ValidationError(f"Unknown translation unit ids: {sorted(unknown)!r}")
        encodings = plan.metadata.get("encodings")
        if not isinstance(encodings, dict):
            raise ValidationError("Translation plan is missing text-component encodings")
        originals = plan.metadata.get("originals")
        if not isinstance(originals, dict):
            raise ValidationError("Translation plan is missing original text values")
        replacements: list[tuple[int, int, str]] = []
        for unit in plan.units:
            if unit.id not in translations:
                continue
            restored = self._restore(unit, translations[unit.id])
            encoding = encodings[unit.id]
            if restored == originals.get(unit.id):
                token = plan.source_text[unit.start:unit.end]
            elif encoding == "json-string":
                token = json.dumps(restored, ensure_ascii=False)
            elif encoding == "nested-json-string":
                token = json.dumps(json.dumps(restored, ensure_ascii=False), ensure_ascii=False)
            else:
                raise ValidationError(f"Unknown encoding {encoding!r}")
            replacements.append((unit.start, unit.end, token))
        output = plan.source_text
        for start, end, token in sorted(replacements, reverse=True):
            output = output[:start] + token + output[end:]
        self.validate(plan.source_text, output)
        return output

    def validate(self, source_text: str, output_text: str) -> None:
        if self.fingerprint(source_text) != self.fingerprint(output_text):
            raise ValidationError("Minecraft text-component structure changed")

    def fingerprint(self, text: str) -> TextComponentFingerprint:
        root = self._parse(text)
        targets: list[tuple[str, _Node, str, str]] = []
        self._collect(root, "", targets)
        spans = [(n.start, n.end, loc) for loc, n, value, enc in targets if self._has_prose(value)]
        out: list[str] = []
        cursor = 0
        for start, end, _ in spans:
            out.append(text[cursor:start])
            out.append('"<mineai-text>"')
            cursor = end
        out.append(text[cursor:])
        return TextComponentFingerprint(tuple(loc for _, _, loc in spans), "".join(out))

    def _collect(self, node: _Node, path: str, out: list[tuple[str, _Node, str, str]]) -> None:
        if node.kind == "object":
            members = {m.key: m.value for m in node.members}
            translate = members.get("translate")
            fallback = members.get("fallback")
            if translate and translate.kind == "string" and fallback and fallback.kind == "string":
                value = fallback.value
                assert isinstance(value, str)
                if not _RESOURCE_LOCATION_RE.fullmatch(value):
                    out.append((path + "/fallback", fallback, value, "json-string"))
            text_node = members.get("text")
            if text_node and text_node.kind == "string":
                value = text_node.value
                assert isinstance(value, str)
                out.append((path + "/text", text_node, value, "json-string"))

            function_node = members.get("function")
            function = function_node.value if function_node and function_node.kind == "string" else None
            if function == "minecraft:set_name":
                name = members.get("name")
                if name and name.kind == "string":
                    assert isinstance(name.value, str)
                    out.append((path + "/name", name, name.value, "json-string"))
            if function == "minecraft:set_lore":
                lore = members.get("lore")
                if lore and lore.kind == "array":
                    for idx, item in enumerate(lore.items):
                        if item.kind == "string":
                            assert isinstance(item.value, str)
                            out.append((f"{path}/lore/{idx}", item, item.value, "json-string"))
            components = members.get("components")
            if function == "minecraft:set_components" and components and components.kind == "object":
                for member in components.members:
                    if member.key == "minecraft:custom_name" and member.value.kind == "string":
                        raw = member.value.value
                        assert isinstance(raw, str)
                        try:
                            nested = json.loads(raw)
                        except json.JSONDecodeError:
                            nested = None
                        if isinstance(nested, str):
                            out.append((path + "/components/minecraft:custom_name", member.value, nested, "nested-json-string"))
            for member in node.members:
                self._collect(member.value, path + "/" + self._escape(member.key), out)
        elif node.kind == "array":
            for idx, item in enumerate(node.items):
                self._collect(item, f"{path}/{idx}", out)

    def _parse(self, text: str) -> _Node:
        node, index = self._parse_value(text, self._skip_ws(text, 0))
        if self._skip_ws(text, index) != len(text):
            raise ValidationError("Trailing data after JSON document")
        return node

    def _parse_value(self, text: str, index: int) -> tuple[_Node, int]:
        index = self._skip_ws(text, index)
        if index >= len(text):
            raise ValidationError("Unexpected end of JSON")
        if text[index] == '"':
            end = self._scan_string_end(text, index)
            try:
                value = json.loads(text[index:end])
            except json.JSONDecodeError as exc:
                raise ValidationError("Invalid JSON string") from exc
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
                    raise ValidationError("JSON object key must be a string")
                key_end = self._scan_string_end(text, index)
                key = json.loads(text[index:key_end])
                if key in seen:
                    raise ValidationError(f"Duplicate JSON key: {key}")
                seen.add(key)
                index = self._skip_ws(text, key_end)
                if index >= len(text) or text[index] != ":":
                    raise ValidationError("Missing ':' after JSON key")
                value, index = self._parse_value(text, index + 1)
                members.append(_Member(key, value))
                index = self._skip_ws(text, index)
                if index < len(text) and text[index] == ",":
                    index += 1
                    continue
                if index < len(text) and text[index] == "}":
                    return _Node("object", start, index + 1, None, tuple(members)), index + 1
                raise ValidationError("Expected ',' or '}' in JSON object")
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
                raise ValidationError("Expected ',' or ']' in JSON array")
        decoder = json.JSONDecoder()
        try:
            value, end = decoder.raw_decode(text, index)
        except json.JSONDecodeError as exc:
            raise ValidationError("Invalid JSON value") from exc
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
            index += 1
        raise ValidationError("Unterminated JSON string")

    @staticmethod
    def _escape(value: str) -> str:
        return value.replace("~", "~0").replace("/", "~1")

    @staticmethod
    def _has_prose(text: str) -> bool:
        return bool(_WORD_RE.search(text))

    def _protect(self, text: str) -> tuple[str, tuple[ProtectedFragment, ...]]:
        spans: list[tuple[int, int]] = []
        literal_ids = [int(m.group(1)) for m in _PLACEHOLDER_RE.finditer(text)]
        spans.extend((m.start(), m.end()) for m in _PLACEHOLDER_RE.finditer(text))
        for regex in (_FORMAT_RE, _MC_FORMAT_RE, _MESSAGE_FORMAT_RE, _LINE_BREAK_RE):
            spans.extend((m.start(), m.end()) for m in regex.finditer(text))
        merged: list[list[int]] = []
        for start, end in sorted(spans):
            if not merged or start >= merged[-1][1]:
                merged.append([start, end])
            elif end > merged[-1][1]:
                merged[-1][1] = end
        base = max(literal_ids) + 1 if literal_ids else 0
        out: list[str] = []
        protected: list[ProtectedFragment] = []
        cursor = 0
        for idx, (start, end) in enumerate(merged):
            out.append(text[cursor:start])
            placeholder = f"[#{base + idx}#]"
            out.append(placeholder)
            protected.append(ProtectedFragment(placeholder, text[start:end]))
            cursor = end
        out.append(text[cursor:])
        return "".join(out), tuple(protected)

    @staticmethod
    def _restore(unit: TranslationUnit, translated: str) -> str:
        expected = Counter(f.placeholder for f in unit.protected)
        actual = Counter(f"[#{v}#]" for v in _PLACEHOLDER_RE.findall(translated))
        if expected != actual:
            raise ValidationError(f"Unit {unit.id} changed protected placeholders")
        restored = translated
        for fragment in unit.protected:
            restored = restored.replace(fragment.placeholder, fragment.value)
        return restored
