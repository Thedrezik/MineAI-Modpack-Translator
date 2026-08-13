from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Mapping

from .core import ProtectedFragment, TranslationPlan, TranslationUnit, ValidationError


_SOURCE_PATH_RE = re.compile(
    r"(^|/)assets/([^/]+)/lang/en_us\.json$",
    re.IGNORECASE,
)
_FORMAT_RE = re.compile(
    r"%(?!\s)(?:(?:\d+\$)?[-+#0 ,(<]*\d*(?:\.\d+)?"
    r"(?:[bBhHsScCdoxXeEfgGaA]|[tT][HIklMSLNpzZsQBbhAaCYyjmdeRTrDFc])|[%n])"
)
_MINECRAFT_FORMAT_RE = re.compile(r"§[0-9A-FK-ORa-fk-or]")
_MESSAGE_FORMAT_RE = re.compile(r"\{\d+(?:,[^{}]+)?\}")
_PLACEHOLDER_RE = re.compile(r"\[#(\d+)#\]")
_LINE_BREAK_RE = re.compile(r"\r\n|\r|\n")
_IGNORED_METADATA_KEYS = frozenset({"_comment", "comment_id"})


@dataclass(frozen=True)
class _JsonEntry:
    key: str
    value: object
    value_start: int
    value_end: int
    is_string: bool


@dataclass(frozen=True)
class LangJsonFingerprint:
    keys: tuple[str, ...]
    skeleton: str


class MinecraftLangJsonAdapter:
    """Structure-preserving adapter for Minecraft ``lang/*.json`` files.

    The source document stays immutable. Only complete JSON string value tokens
    are replaced, so key ordering, whitespace, commas and unrelated data are
    preserved exactly. Technical formatting fragments are represented by
    placeholders before a value is handed to a translator.
    """

    name = "minecraft-lang-json"

    def matches(self, path: str) -> bool:
        slash = path.replace("\\", "/")
        return bool(_SOURCE_PATH_RE.search("/" + slash.lstrip("/")))

    def target_path(self, path: str, target_code: str) -> str:
        slash = path.replace("\\", "/")
        normalized = "/" + slash.lstrip("/")
        match = _SOURCE_PATH_RE.search(normalized)
        if not match:
            raise ValueError(f"Unsupported Minecraft lang source path: {path}")
        prefix_len = 1 if not slash.startswith("/") else 0
        start = match.end() - len("en_us.json") - prefix_len
        end = start + len("en_us.json")
        return slash[:start] + f"{target_code}.json" + slash[end:]

    def prepare(self, path: str, source_text: str) -> TranslationPlan:
        entries = self._parse_entries(source_text)
        ignored_metadata = self._repeated_metadata_keys(entries)
        units: list[TranslationUnit] = []
        original_values: dict[str, str] = {}
        unsupported_non_string_keys: list[str] = []
        ignored_metadata_keys: list[str] = []

        for entry in entries:
            if entry.key in ignored_metadata:
                ignored_metadata_keys.append(entry.key)
                continue
            if not entry.is_string:
                unsupported_non_string_keys.append(entry.key)
                continue
            assert isinstance(entry.value, str)
            if not entry.value:
                continue
            masked, protected = self._protect(entry.value)
            unit_id = f"key:{entry.key}"
            units.append(
                TranslationUnit(
                    id=unit_id,
                    text=masked,
                    start=entry.value_start,
                    end=entry.value_end,
                    kind="minecraft-lang-value",
                    context=entry.key,
                    protected=protected,
                )
            )
            original_values[unit_id] = entry.value

        return TranslationPlan(
            path=path,
            source_text=source_text,
            units=tuple(units),
            metadata={
                "fingerprint": self.fingerprint(source_text),
                "original_values": original_values,
                "unsupported_non_string_keys": tuple(unsupported_non_string_keys),
                "ignored_metadata_keys": tuple(dict.fromkeys(ignored_metadata_keys)),
            },
        )

    def apply(self, plan: TranslationPlan, translations: Mapping[str, str]) -> str:
        known = {unit.id for unit in plan.units}
        unknown = set(translations) - known
        if unknown:
            raise ValidationError(f"Unknown translation unit ids: {sorted(unknown)!r}")

        original_values = plan.metadata.get("original_values")
        if not isinstance(original_values, dict):
            raise ValidationError("Translation plan is missing original locale values")

        replacements: list[tuple[int, int, str]] = []
        for unit in plan.units:
            if unit.id not in translations:
                continue
            translated = translations[unit.id]
            restored = self._restore_protected(unit, translated)
            original_value = original_values.get(unit.id)

            # Identity translations must preserve the exact original JSON token,
            # including its original escape spelling.
            if restored == original_value:
                encoded = plan.source_text[unit.start : unit.end]
            else:
                encoded = json.dumps(restored, ensure_ascii=False)
            replacements.append((unit.start, unit.end, encoded))

        output = plan.source_text
        for start, end, value in sorted(replacements, reverse=True):
            output = output[:start] + value + output[end:]

        self.validate(plan.source_text, output)
        return output

    def validate(self, source_text: str, output_text: str) -> None:
        before = self.fingerprint(source_text)
        after = self.fingerprint(output_text)
        if before != after:
            raise ValidationError(
                "Minecraft lang JSON structure changed during reconstruction"
            )

    def fingerprint(self, text: str) -> LangJsonFingerprint:
        entries = self._parse_entries(text)
        ignored_metadata = self._repeated_metadata_keys(entries)
        string_spans = [
            (entry.value_start, entry.value_end)
            for entry in entries
            if entry.is_string and entry.key not in ignored_metadata
        ]
        skeleton_parts: list[str] = []
        cursor = 0
        for start, end in string_spans:
            skeleton_parts.append(text[cursor:start])
            skeleton_parts.append('"<mineai-value>"')
            cursor = end
        skeleton_parts.append(text[cursor:])
        return LangJsonFingerprint(
            keys=tuple(entry.key for entry in entries),
            skeleton="".join(skeleton_parts),
        )

    @staticmethod
    def _repeated_metadata_keys(entries: tuple[_JsonEntry, ...]) -> frozenset[str]:
        counts = Counter(entry.key for entry in entries)
        return frozenset(
            key for key, count in counts.items()
            if count > 1 and key in _IGNORED_METADATA_KEYS
        )

    def _parse_entries(self, text: str) -> tuple[_JsonEntry, ...]:
        decoder = json.JSONDecoder()
        length = len(text)
        index = self._skip_ws(text, 0)
        if index >= length or text[index] != "{":
            raise ValidationError("Minecraft lang JSON must be a top-level object")
        index += 1

        entries: list[_JsonEntry] = []
        seen: set[str] = set()

        while True:
            index = self._skip_ws(text, index)
            if index >= length:
                raise ValidationError("Unexpected end of Minecraft lang JSON")
            if text[index] == "}":
                index += 1
                break
            if text[index] != '"':
                raise ValidationError("Minecraft lang JSON keys must be strings")

            key_end = self._scan_string_end(text, index)
            try:
                key = json.loads(text[index:key_end])
            except json.JSONDecodeError as exc:
                raise ValidationError("Invalid JSON key string") from exc
            if key in seen and key not in _IGNORED_METADATA_KEYS:
                raise ValidationError(f"Duplicate Minecraft lang key: {key}")
            seen.add(key)
            index = self._skip_ws(text, key_end)
            if index >= length or text[index] != ":":
                raise ValidationError(f"Missing ':' after Minecraft lang key {key!r}")
            index = self._skip_ws(text, index + 1)
            if index >= length:
                raise ValidationError(f"Missing value for Minecraft lang key {key!r}")

            value_start = index
            if text[index] == '"':
                value_end = self._scan_string_end(text, index)
                try:
                    value = json.loads(text[value_start:value_end])
                except json.JSONDecodeError as exc:
                    raise ValidationError(f"Invalid JSON string for key {key!r}") from exc
                is_string = True
            else:
                try:
                    value, value_end = decoder.raw_decode(text, index)
                except json.JSONDecodeError as exc:
                    raise ValidationError(f"Invalid JSON value for key {key!r}") from exc
                is_string = False

            entries.append(
                _JsonEntry(
                    key=key,
                    value=value,
                    value_start=value_start,
                    value_end=value_end,
                    is_string=is_string,
                )
            )
            index = self._skip_ws(text, value_end)
            if index >= length:
                raise ValidationError("Unexpected end of Minecraft lang JSON")
            if text[index] == ",":
                index += 1
                continue
            if text[index] == "}":
                index += 1
                break
            raise ValidationError(f"Expected ',' or '}}' after Minecraft lang key {key!r}")

        if self._skip_ws(text, index) != length:
            raise ValidationError("Trailing data after Minecraft lang JSON object")
        return tuple(entries)

    @staticmethod
    def _skip_ws(text: str, index: int) -> int:
        while index < len(text) and text[index] in " \t\r\n":
            index += 1
        return index

    @staticmethod
    def _scan_string_end(text: str, start: int) -> int:
        index = start + 1
        escaped = False
        while index < len(text):
            char = text[index]
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                return index + 1
            elif ord(char) < 0x20:
                raise ValidationError("Unescaped control character in JSON string")
            index += 1
        raise ValidationError("Unterminated JSON string")

    def _protect(self, text: str) -> tuple[str, tuple[ProtectedFragment, ...]]:
        spans: list[tuple[int, int]] = []
        literal_ids = [int(match.group(1)) for match in _PLACEHOLDER_RE.finditer(text)]
        spans.extend((m.start(), m.end()) for m in _PLACEHOLDER_RE.finditer(text))
        for regex in (
            _FORMAT_RE,
            _MINECRAFT_FORMAT_RE,
            _MESSAGE_FORMAT_RE,
            _LINE_BREAK_RE,
        ):
            spans.extend((match.start(), match.end()) for match in regex.finditer(text))

        merged = self._merge_spans(spans)
        protected: list[ProtectedFragment] = []
        out: list[str] = []
        cursor = 0
        base_id = (max(literal_ids) + 1) if literal_ids else 0
        for offset, (start, end) in enumerate(merged):
            out.append(text[cursor:start])
            placeholder = f"[#{base_id + offset}#]"
            out.append(placeholder)
            protected.append(ProtectedFragment(placeholder, text[start:end]))
            cursor = end
        out.append(text[cursor:])
        return "".join(out), tuple(protected)

    @staticmethod
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

    @staticmethod
    def _restore_protected(unit: TranslationUnit, translated: str) -> str:
        expected = Counter(fragment.placeholder for fragment in unit.protected)
        actual = Counter(f"[#{value}#]" for value in _PLACEHOLDER_RE.findall(translated))
        if actual != expected:
            raise ValidationError(
                f"Unit {unit.id} changed protected placeholders: "
                f"expected {expected}, got {actual}"
            )
        restored = translated
        for fragment in unit.protected:
            restored = restored.replace(fragment.placeholder, fragment.value)
        return restored
