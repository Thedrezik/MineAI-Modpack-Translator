from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Mapping

from .core import ProtectedFragment, TranslationPlan, TranslationUnit, ValidationError


_LANG_PATH_RE = re.compile(
    r"(^|/)ftbquests/quests/lang/en_us\.snbt$",
    re.IGNORECASE,
)
_PLACEHOLDER_RE = re.compile(r"\[#(\d+)#\]")
_FTB_FORMAT_RE = re.compile(r"&[0-9A-FK-ORZa-fk-orz]")
_ESCAPED_AMP_RE = re.compile(r"\\&\\")
_FTB_DIRECTIVE_RE = re.compile(r"\{(?:@[^{}]+|image:[^{}]+|ftbquests\.[^{}]+)\}")
_URL_RE = re.compile(r"https?://[^\s\]\[(){}<>\"']+")
_COMMAND_RE = re.compile(r"(?<![:/])/[A-Za-z][A-Za-z0-9_.:-]*")
_ANGLE_PLACEHOLDER_RE = re.compile(r"<[A-Za-z][A-Za-z0-9_.:-]*>")
_KEY_CHORD_RE = re.compile(r"\b(?:Ctrl|Shift|Alt)\s*\+\s*[A-Za-z0-9]+\b", re.IGNORECASE)
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё]{2,}")


@dataclass(frozen=True)
class _LocaleEntry:
    key: str
    kind: str
    values: tuple[str, ...]
    spans: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class _JsonMember:
    key: str
    value: "_JsonNode"


@dataclass(frozen=True)
class _JsonNode:
    kind: str
    start: int
    end: int
    value: object
    members: tuple[_JsonMember, ...] = ()
    items: tuple["_JsonNode", ...] = ()


@dataclass(frozen=True)
class FtbQuestsLangFingerprint:
    keys: tuple[str, ...]
    shapes: tuple[tuple[str, str, int], ...]
    skeleton: str
    component_skeletons: tuple[tuple[str, str], ...]


class FtbQuestsLangAdapter:
    """Structure-safe adapter for FTB Quests ``quests/lang/en_us.snbt``.

    FTB Quests keeps its canonical quest titles/subtitles/descriptions in one
    top-level SNBT compound. Values are either one quoted string or a list of
    quoted strings. The adapter never parses or rewrites chapter graph files.

    JSON text components embedded *inside* SNBT strings are handled as nested
    structures: only visible literal text leaves become translation units;
    colors, click events, URLs, keybind IDs and other component metadata remain
    immutable.
    """

    name = "ftb-quests-lang"

    def matches(self, path: str) -> bool:
        slash = "/" + path.replace("\\", "/").lstrip("/")
        return bool(_LANG_PATH_RE.search(slash))

    def target_path(self, path: str, target_code: str) -> str:
        slash = path.replace("\\", "/")
        normalized = "/" + slash.lstrip("/")
        match = _LANG_PATH_RE.search(normalized)
        if not match:
            raise ValueError(f"Unsupported FTB Quests locale source path: {path}")
        offset = 1 if not slash.startswith("/") else 0
        start = match.end() - len("en_us.snbt") - offset
        end = start + len("en_us.snbt")
        return slash[:start] + f"{target_code}.snbt" + slash[end:]

    def prepare(self, path: str, source_text: str) -> TranslationPlan:
        entries = self._parse_entries(source_text)
        units: list[TranslationUnit] = []
        plain_originals: dict[str, str] = {}
        component_groups: dict[str, dict[str, object]] = {}

        for entry in entries:
            for index, (value, span) in enumerate(zip(entry.values, entry.spans)):
                outer_id = self._outer_id(entry, index)
                component = self._component_targets(value)
                if component is not None:
                    root, targets = component
                    visible = self._component_visible_text(targets)
                    target_meta: dict[str, tuple[int, int, str]] = {}
                    for locator, node, text in targets:
                        if not self._has_prose(text):
                            continue
                        masked, protected = self._protect(text)
                        if not self._has_prose(masked):
                            continue
                        unit_id = f"{outer_id}#json:{locator}"
                        units.append(
                            TranslationUnit(
                                id=unit_id,
                                text=masked,
                                start=span[0],
                                end=span[1],
                                kind="ftb-quests-json-text",
                                context=(
                                    f"{entry.key}; component {locator}; "
                                    f"full visible text: {visible}"
                                ),
                                protected=protected,
                            )
                        )
                        target_meta[unit_id] = (node.start, node.end, text)
                    if target_meta:
                        component_groups[outer_id] = {
                            "span": span,
                            "original": value,
                            "targets": target_meta,
                        }
                        continue

                if not value:
                    continue
                masked, protected = self._protect(value)
                if not self._has_prose(masked):
                    continue
                unit_id = outer_id
                units.append(
                    TranslationUnit(
                        id=unit_id,
                        text=masked,
                        start=span[0],
                        end=span[1],
                        kind=self._unit_kind(entry.key),
                        context=entry.key,
                        protected=protected,
                    )
                )
                plain_originals[unit_id] = value

        return TranslationPlan(
            path=path,
            source_text=source_text,
            units=tuple(units),
            metadata={
                "fingerprint": self.fingerprint(source_text),
                "plain_originals": plain_originals,
                "component_groups": component_groups,
            },
        )

    def apply(self, plan: TranslationPlan, translations: Mapping[str, str]) -> str:
        known = {unit.id for unit in plan.units}
        unknown = set(translations) - known
        if unknown:
            raise ValidationError(f"Unknown translation unit ids: {sorted(unknown)!r}")

        unit_map = plan.by_id()
        plain_originals = plan.metadata.get("plain_originals")
        groups = plan.metadata.get("component_groups")
        if not isinstance(plain_originals, dict) or not isinstance(groups, dict):
            raise ValidationError("FTB Quests translation plan metadata is incomplete")

        grouped_ids = {
            unit_id
            for group in groups.values()
            if isinstance(group, dict)
            for unit_id in self._group_target_ids(group)
        }
        replacements: list[tuple[int, int, str]] = []

        for unit in plan.units:
            if unit.id in grouped_ids or unit.id not in translations:
                continue
            original = plain_originals.get(unit.id)
            if not isinstance(original, str):
                raise ValidationError(f"Missing original FTB Quests value for {unit.id}")
            restored = self._restore(unit, translations[unit.id])
            self._reject_newlines(unit.id, restored)
            token = (
                plan.source_text[unit.start : unit.end]
                if restored == original
                else json.dumps(restored, ensure_ascii=False)
            )
            replacements.append((unit.start, unit.end, token))

        for outer_id, group in groups.items():
            if not isinstance(group, dict):
                raise ValidationError(f"Invalid component group metadata for {outer_id}")
            span = group.get("span")
            original = group.get("original")
            targets = group.get("targets")
            if (
                not isinstance(span, tuple)
                or len(span) != 2
                or not isinstance(original, str)
                or not isinstance(targets, dict)
            ):
                raise ValidationError(f"Invalid component group metadata for {outer_id}")

            inner_replacements: list[tuple[int, int, str]] = []
            for unit_id, meta in targets.items():
                if unit_id not in translations:
                    continue
                if unit_id not in unit_map:
                    raise ValidationError(f"Missing component unit {unit_id}")
                if not isinstance(meta, tuple) or len(meta) != 3:
                    raise ValidationError(f"Invalid component target metadata for {unit_id}")
                start, end, original_leaf = meta
                if not isinstance(start, int) or not isinstance(end, int) or not isinstance(original_leaf, str):
                    raise ValidationError(f"Invalid component target metadata for {unit_id}")
                restored = self._restore(unit_map[unit_id], translations[unit_id])
                self._reject_newlines(unit_id, restored)
                token = original[start:end] if restored == original_leaf else json.dumps(restored, ensure_ascii=False)
                inner_replacements.append((start, end, token))

            if not inner_replacements:
                continue
            rebuilt = original
            for start, end, token in sorted(inner_replacements, reverse=True):
                rebuilt = rebuilt[:start] + token + rebuilt[end:]
            outer_token = (
                plan.source_text[span[0] : span[1]]
                if rebuilt == original
                else json.dumps(rebuilt, ensure_ascii=False)
            )
            replacements.append((span[0], span[1], outer_token))

        output = plan.source_text
        for start, end, token in sorted(replacements, reverse=True):
            output = output[:start] + token + output[end:]
        self.validate(plan.source_text, output)
        return output

    def validate(self, source_text: str, output_text: str) -> None:
        if self.fingerprint(source_text) != self.fingerprint(output_text):
            raise ValidationError("FTB Quests locale structure changed during reconstruction")

    def fingerprint(self, text: str) -> FtbQuestsLangFingerprint:
        entries = self._parse_entries(text)
        spans: list[tuple[int, int]] = []
        component_skeletons: list[tuple[str, str]] = []
        for entry in entries:
            for idx, (value, span) in enumerate(zip(entry.values, entry.spans)):
                spans.append(span)
                component = self._component_targets(value)
                if component is not None:
                    _, targets = component
                    if targets:
                        component_skeletons.append(
                            (self._outer_id(entry, idx), self._component_skeleton(value, targets))
                        )

        skeleton_parts: list[str] = []
        cursor = 0
        for start, end in sorted(spans):
            skeleton_parts.append(text[cursor:start])
            skeleton_parts.append('\"<mineai-ftb-value>\"')
            cursor = end
        skeleton_parts.append(text[cursor:])
        return FtbQuestsLangFingerprint(
            keys=tuple(entry.key for entry in entries),
            shapes=tuple((entry.key, entry.kind, len(entry.values)) for entry in entries),
            skeleton="".join(skeleton_parts),
            component_skeletons=tuple(component_skeletons),
        )

    def _parse_entries(self, text: str) -> tuple[_LocaleEntry, ...]:
        index = self._skip_ws(text, 0)
        if index >= len(text) or text[index] != "{":
            raise ValidationError("FTB Quests locale must be a top-level SNBT compound")
        index += 1
        entries: list[_LocaleEntry] = []
        seen: set[str] = set()

        while True:
            index = self._skip_ws_commas(text, index)
            if index >= len(text):
                raise ValidationError("Unexpected end of FTB Quests locale")
            if text[index] == "}":
                index += 1
                break

            key, index = self._parse_key(text, index)
            if key in seen:
                raise ValidationError(f"Duplicate FTB Quests locale key: {key}")
            seen.add(key)
            index = self._skip_ws(text, index)
            if index >= len(text) or text[index] != ":":
                raise ValidationError(f"Missing ':' after FTB Quests key {key!r}")
            index = self._skip_ws(text, index + 1)
            if index >= len(text):
                raise ValidationError(f"Missing value for FTB Quests key {key!r}")

            if text[index] == '"':
                end = self._scan_string_end(text, index)
                value = self._decode_string(text[index:end], key)
                entries.append(_LocaleEntry(key, "string", (value,), ((index, end),)))
                index = end
                continue

            if text[index] == "[":
                values, spans, index = self._parse_string_list(text, index, key)
                entries.append(_LocaleEntry(key, "list", tuple(values), tuple(spans)))
                continue

            raise ValidationError(
                f"FTB Quests locale key {key!r} must contain a string or list of strings"
            )

        if self._skip_ws_commas(text, index) != len(text):
            raise ValidationError("Trailing data after FTB Quests locale compound")
        return tuple(entries)

    def _parse_key(self, text: str, index: int) -> tuple[str, int]:
        if text[index] == '"':
            end = self._scan_string_end(text, index)
            return self._decode_string(text[index:end], "<key>"), end
        start = index
        while index < len(text) and text[index] not in ":\r\n{}[],'\"":
            index += 1
        key = text[start:index].strip()
        if not key:
            raise ValidationError("Empty FTB Quests locale key")
        return key, index

    def _parse_string_list(
        self, text: str, index: int, key: str
    ) -> tuple[list[str], list[tuple[int, int]], int]:
        index += 1
        values: list[str] = []
        spans: list[tuple[int, int]] = []
        while True:
            index = self._skip_ws_commas(text, index)
            if index >= len(text):
                raise ValidationError(f"Unterminated list for FTB Quests key {key!r}")
            if text[index] == "]":
                return values, spans, index + 1
            if text[index] != '"':
                raise ValidationError(
                    f"FTB Quests description list {key!r} must contain only strings"
                )
            end = self._scan_string_end(text, index)
            values.append(self._decode_string(text[index:end], key))
            spans.append((index, end))
            index = end

    @staticmethod
    def _skip_ws(text: str, index: int) -> int:
        while index < len(text) and text[index] in " \t\r\n":
            index += 1
        return index

    def _skip_ws_commas(self, text: str, index: int) -> int:
        while True:
            index = self._skip_ws(text, index)
            if index < len(text) and text[index] == ",":
                index += 1
                continue
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
                raise ValidationError("Unescaped control character in FTB Quests string")
            index += 1
        raise ValidationError("Unterminated FTB Quests string")

    @staticmethod
    def _decode_string(token: str, key: str) -> str:
        try:
            value = json.loads(token)
        except json.JSONDecodeError as exc:
            # FTB Quests uses Mojang SNBT escaping, which permits escaping
            # characters JSON does not (notably ``\ `` in long descriptions).
            # Decode that narrow fallback without relaxing structure checks.
            if len(token) < 2 or token[0] != '"' or token[-1] != '"':
                raise ValidationError(f"Invalid quoted SNBT string for {key!r}") from exc
            raw = token[1:-1]
            chars: list[str] = []
            index = 0
            escapes = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}
            while index < len(raw):
                char = raw[index]
                if char != "\\" or index + 1 >= len(raw):
                    chars.append(char)
                    index += 1
                    continue
                escaped = raw[index + 1]
                chars.append(escapes.get(escaped, escaped))
                index += 2
            value = "".join(chars)
        if not isinstance(value, str):
            raise ValidationError(f"FTB Quests string for {key!r} did not decode to text")
        return value

    def _component_targets(
        self, text: str
    ) -> tuple[_JsonNode, tuple[tuple[str, _JsonNode, str], ...]] | None:
        stripped = text.lstrip()
        if not stripped.startswith(("[", "{")):
            return None
        offset = len(text) - len(stripped)
        try:
            root, end = self._parse_json_value(text, offset)
        except ValidationError:
            return None
        if self._skip_json_ws(text, end) != len(text):
            return None
        if root.kind not in {"array", "object"}:
            return None
        targets: list[tuple[str, _JsonNode, str]] = []
        self._collect_component_targets(root, "", targets, allow_literal=True)
        return root, tuple(targets)

    def _collect_component_targets(
        self,
        node: _JsonNode,
        path: str,
        out: list[tuple[str, _JsonNode, str]],
        *,
        allow_literal: bool,
    ) -> None:
        if node.kind == "string":
            if allow_literal:
                assert isinstance(node.value, str)
                out.append((path or "/", node, node.value))
            return
        if node.kind == "array":
            for idx, item in enumerate(node.items):
                self._collect_component_targets(
                    item, f"{path}/{idx}", out, allow_literal=True
                )
            return
        if node.kind != "object":
            return

        members = {member.key: member.value for member in node.members}
        text_node = members.get("text")
        if text_node is not None and text_node.kind == "string":
            assert isinstance(text_node.value, str)
            out.append((path + "/text", text_node, text_node.value))

        translate = members.get("translate")
        fallback = members.get("fallback")
        if (
            translate is not None
            and translate.kind == "string"
            and fallback is not None
            and fallback.kind == "string"
        ):
            assert isinstance(fallback.value, str)
            out.append((path + "/fallback", fallback, fallback.value))

        for key in ("extra", "with"):
            child = members.get(key)
            if child is not None:
                self._collect_component_targets(
                    child, path + "/" + key, out, allow_literal=True
                )

        hover = members.get("hoverEvent")
        if hover is not None and hover.kind == "object":
            hover_members = {member.key: member.value for member in hover.members}
            action = hover_members.get("action")
            contents = hover_members.get("contents")
            if (
                action is not None
                and action.kind == "string"
                and action.value == "show_text"
                and contents is not None
            ):
                self._collect_component_targets(
                    contents,
                    path + "/hoverEvent/contents",
                    out,
                    allow_literal=True,
                )

    def _parse_json_value(self, text: str, index: int) -> tuple[_JsonNode, int]:
        index = self._skip_json_ws(text, index)
        if index >= len(text):
            raise ValidationError("Unexpected end of nested JSON component")
        if text[index] == '"':
            end = self._scan_string_end(text, index)
            try:
                value = json.loads(text[index:end])
            except json.JSONDecodeError as exc:
                raise ValidationError("Invalid nested JSON string") from exc
            return _JsonNode("string", index, end, value), end
        if text[index] == "[":
            start = index
            index += 1
            items: list[_JsonNode] = []
            while True:
                index = self._skip_json_ws(text, index)
                if index < len(text) and text[index] == "]":
                    return _JsonNode("array", start, index + 1, None, items=tuple(items)), index + 1
                item, index = self._parse_json_value(text, index)
                items.append(item)
                index = self._skip_json_ws(text, index)
                if index < len(text) and text[index] == ",":
                    index += 1
                    continue
                if index < len(text) and text[index] == "]":
                    return _JsonNode("array", start, index + 1, None, items=tuple(items)), index + 1
                raise ValidationError("Invalid nested JSON array")
        if text[index] == "{":
            start = index
            index += 1
            members: list[_JsonMember] = []
            seen: set[str] = set()
            while True:
                index = self._skip_json_ws(text, index)
                if index < len(text) and text[index] == "}":
                    return _JsonNode("object", start, index + 1, None, members=tuple(members)), index + 1
                if index >= len(text) or text[index] != '"':
                    raise ValidationError("Nested JSON object key must be a string")
                key_end = self._scan_string_end(text, index)
                key = json.loads(text[index:key_end])
                if key in seen:
                    raise ValidationError(f"Duplicate nested JSON key: {key}")
                seen.add(key)
                index = self._skip_json_ws(text, key_end)
                if index >= len(text) or text[index] != ":":
                    raise ValidationError("Missing ':' in nested JSON object")
                value, index = self._parse_json_value(text, index + 1)
                members.append(_JsonMember(key, value))
                index = self._skip_json_ws(text, index)
                if index < len(text) and text[index] == ",":
                    index += 1
                    continue
                if index < len(text) and text[index] == "}":
                    return _JsonNode("object", start, index + 1, None, members=tuple(members)), index + 1
                raise ValidationError("Invalid nested JSON object")

        decoder = json.JSONDecoder()
        try:
            value, end = decoder.raw_decode(text, index)
        except json.JSONDecodeError as exc:
            raise ValidationError("Invalid nested JSON value") from exc
        return _JsonNode("scalar", index, end, value), end

    @staticmethod
    def _skip_json_ws(text: str, index: int) -> int:
        while index < len(text) and text[index] in " \t\r\n":
            index += 1
        return index

    @staticmethod
    def _component_visible_text(
        targets: tuple[tuple[str, _JsonNode, str], ...]
    ) -> str:
        return "".join(text for _, _, text in targets).strip()

    @staticmethod
    def _component_skeleton(
        source: str, targets: tuple[tuple[str, _JsonNode, str], ...]
    ) -> str:
        out: list[str] = []
        cursor = 0
        for _, node, _ in sorted(targets, key=lambda item: item[1].start):
            out.append(source[cursor : node.start])
            out.append('\"<mineai-text>\"')
            cursor = node.end
        out.append(source[cursor:])
        return "".join(out)

    def _protect(self, text: str) -> tuple[str, tuple[ProtectedFragment, ...]]:
        spans: list[tuple[int, int]] = []
        literal_ids = [int(match.group(1)) for match in _PLACEHOLDER_RE.finditer(text)]
        spans.extend((match.start(), match.end()) for match in _PLACEHOLDER_RE.finditer(text))
        for regex in (
            _FTB_FORMAT_RE,
            _ESCAPED_AMP_RE,
            _FTB_DIRECTIVE_RE,
            _URL_RE,
            _COMMAND_RE,
            _ANGLE_PLACEHOLDER_RE,
            _KEY_CHORD_RE,
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
        expected = Counter(fragment.placeholder for fragment in unit.protected)
        actual = Counter(f"[#{value}#]" for value in _PLACEHOLDER_RE.findall(translated))
        if expected != actual:
            raise ValidationError(
                f"Unit {unit.id} changed protected placeholders: expected {expected}, got {actual}"
            )
        restored = translated
        for fragment in unit.protected:
            restored = restored.replace(fragment.placeholder, fragment.value)
        return restored

    @staticmethod
    def _reject_newlines(unit_id: str, text: str) -> None:
        if "\n" in text or "\r" in text:
            raise ValidationError(
                f"FTB Quests unit {unit_id} introduced a line break; paragraph/list structure is immutable"
            )

    @staticmethod
    def _has_prose(text: str) -> bool:
        return bool(_WORD_RE.search(text))

    @staticmethod
    def _unit_kind(key: str) -> str:
        if key.endswith(".quest_desc"):
            return "ftb-quests-description"
        if key.endswith(".quest_subtitle"):
            return "ftb-quests-subtitle"
        return "ftb-quests-title"

    @staticmethod
    def _outer_id(entry: _LocaleEntry, index: int) -> str:
        if entry.kind == "string":
            return f"key:{entry.key}"
        return f"key:{entry.key}[{index}]"

    @staticmethod
    def _group_target_ids(group: dict[str, object]) -> tuple[str, ...]:
        targets = group.get("targets")
        if not isinstance(targets, dict):
            return ()
        return tuple(str(key) for key in targets)


_CHAPTER_PATH_RE = re.compile(
    r"(^|/)ftbquests/quests/(?:chapters|reward_tables)/[^/]+\.snbt$",
    re.IGNORECASE,
)
_DIRECT_CHAPTER_KEYS = {
    "description",
    "feedback_message",
    "minecraft:custom_name",
    "minecraft:lore",
}


@dataclass(frozen=True)
class _ChapterField:
    key: str
    values: tuple[str, ...]
    spans: tuple[tuple[int, int], ...]
    occurrence: int


@dataclass(frozen=True)
class FtbQuestsChapterFingerprint:
    skeleton: str
    fields: tuple[tuple[str, int], ...]
    component_skeletons: tuple[tuple[str, str], ...]


class FtbQuestsChapterAdapter(FtbQuestsLangAdapter):
    """Extract rare direct player-visible text from FTB Quests SNBT.

    Normal quest titles/subtitles/descriptions belong in ``lang/en_us.snbt``.
    Chapter and reward-table files are therefore treated as technical documents except for a
    very small allow-list observed in the real FTB Evolution corpus:

    * ``feedback_message`` and ``description`` string fields;
    * ``minecraft:custom_name`` JSON text components stored in SNBT strings;
    * ``minecraft:lore`` lists whose elements are JSON text components.

    Everything else in a chapter file stays byte-identical.
    """

    name = "ftb-quests-chapter-text"

    def matches(self, path: str) -> bool:
        slash = "/" + path.replace("\\", "/").lstrip("/")
        return bool(_CHAPTER_PATH_RE.search(slash))

    def prepare(self, path: str, source_text: str) -> TranslationPlan:
        fields = self._find_chapter_fields(source_text)
        units: list[TranslationUnit] = []
        plain_originals: dict[str, str] = {}
        component_groups: dict[str, dict[str, object]] = {}

        for field in fields:
            for value_index, (value, span) in enumerate(zip(field.values, field.spans)):
                outer_id = self._chapter_outer_id(field, value_index)
                if field.key in {"minecraft:custom_name", "minecraft:lore"}:
                    component = self._component_targets_any(value)
                    if component is None:
                        raise ValidationError(
                            f"{field.key} must contain a valid JSON text component"
                        )
                    _, targets = component
                    visible = self._component_visible_text(targets)
                    target_meta: dict[str, tuple[int, int, str]] = {}
                    for locator, node, text in targets:
                        if not self._has_prose(text):
                            continue
                        masked, protected = self._protect(text)
                        if not self._has_prose(masked):
                            continue
                        unit_id = f"{outer_id}#json:{locator}"
                        units.append(
                            TranslationUnit(
                                id=unit_id,
                                text=masked,
                                start=span[0],
                                end=span[1],
                                kind="ftb-quests-chapter-component",
                                context=(
                                    f"{path}; {field.key}; component {locator}; "
                                    f"full visible text: {visible}"
                                ),
                                protected=protected,
                            )
                        )
                        target_meta[unit_id] = (node.start, node.end, text)
                    if target_meta:
                        component_groups[outer_id] = {
                            "span": span,
                            "original": value,
                            "targets": target_meta,
                        }
                    continue

                if not value:
                    continue
                masked, protected = self._protect(value)
                if not self._has_prose(masked):
                    continue
                unit_id = outer_id
                units.append(
                    TranslationUnit(
                        id=unit_id,
                        text=masked,
                        start=span[0],
                        end=span[1],
                        kind="ftb-quests-chapter-message",
                        context=f"{path}; {field.key}",
                        protected=protected,
                    )
                )
                plain_originals[unit_id] = value

        return TranslationPlan(
            path=path,
            source_text=source_text,
            units=tuple(units),
            metadata={
                "fingerprint": self.fingerprint(source_text),
                "plain_originals": plain_originals,
                "component_groups": component_groups,
            },
        )

    def fingerprint(self, text: str) -> FtbQuestsChapterFingerprint:
        fields = self._find_chapter_fields(text)
        spans: list[tuple[int, int]] = []
        components: list[tuple[str, str]] = []
        for field in fields:
            for value_index, (value, span) in enumerate(zip(field.values, field.spans)):
                spans.append(span)
                if field.key in {"minecraft:custom_name", "minecraft:lore"}:
                    component = self._component_targets_any(value)
                    if component is None:
                        raise ValidationError(
                            f"{field.key} must contain a valid JSON text component"
                        )
                    _, targets = component
                    components.append(
                        (
                            self._chapter_outer_id(field, value_index),
                            self._component_skeleton(value, targets),
                        )
                    )
        out: list[str] = []
        cursor = 0
        for start, end in sorted(spans):
            out.append(text[cursor:start])
            out.append('\"<mineai-ftb-chapter-text>\"')
            cursor = end
        out.append(text[cursor:])
        return FtbQuestsChapterFingerprint(
            skeleton="".join(out),
            fields=tuple((field.key, len(field.values)) for field in fields),
            component_skeletons=tuple(components),
        )

    def validate(self, source_text: str, output_text: str) -> None:
        if self.fingerprint(source_text) != self.fingerprint(output_text):
            raise ValidationError("FTB Quests chapter structure changed during reconstruction")

    def _find_chapter_fields(self, text: str) -> tuple[_ChapterField, ...]:
        fields: list[_ChapterField] = []
        counts: Counter[str] = Counter()
        index = 0
        while index < len(text):
            char = text[index]
            key: str | None = None
            key_end = index

            if char == '"':
                end = self._scan_string_end(text, index)
                j = self._skip_ws(text, end)
                if j < len(text) and text[j] == ":":
                    try:
                        candidate = self._decode_string(text[index:end], "<chapter-key>")
                    except ValidationError:
                        candidate = ""
                    if candidate in _DIRECT_CHAPTER_KEYS:
                        key = candidate
                        key_end = j
                if key is None:
                    index = end
                    continue
            elif char.isalpha() or char in "_#":
                end = index + 1
                while end < len(text) and (
                    text[end].isalnum() or text[end] in "_.+-#"
                ):
                    end += 1
                candidate = text[index:end]
                j = self._skip_ws(text, end)
                if j < len(text) and text[j] == ":" and candidate in _DIRECT_CHAPTER_KEYS:
                    key = candidate
                    key_end = j
                else:
                    index = end
                    continue
            else:
                index += 1
                continue

            assert key is not None
            value_index = self._skip_ws(text, key_end + 1)
            occurrence = counts[key]
            counts[key] += 1

            if key in {"description", "minecraft:lore"} and value_index < len(text) and text[value_index] == "[":
                values, spans, end = self._parse_string_list(
                    text, value_index, key
                )
                fields.append(
                    _ChapterField(key, tuple(values), tuple(spans), occurrence)
                )
                index = end
                continue

            if value_index >= len(text) or text[value_index] != '"':
                # The allow-listed field may exist with a non-text payload in a
                # future version. Fail closed rather than guessing.
                raise ValidationError(
                    f"FTB Quests chapter field {key!r} must contain a quoted string"
                )
            end = self._scan_string_end(text, value_index)
            value = self._decode_string(text[value_index:end], key)
            fields.append(
                _ChapterField(key, (value,), ((value_index, end),), occurrence)
            )
            index = end

        return tuple(fields)

    def _component_targets_any(
        self, text: str
    ) -> tuple[_JsonNode, tuple[tuple[str, _JsonNode, str], ...]] | None:
        index = self._skip_json_ws(text, 0)
        if index >= len(text) or text[index] not in '[{\"':
            return None
        try:
            root, end = self._parse_json_value(text, index)
        except ValidationError:
            return None
        if self._skip_json_ws(text, end) != len(text):
            return None
        targets: list[tuple[str, _JsonNode, str]] = []
        self._collect_component_targets(root, "", targets, allow_literal=True)
        return root, tuple(targets)

    @staticmethod
    def _chapter_outer_id(field: _ChapterField, value_index: int) -> str:
        base = f"field:{field.key}[{field.occurrence}]"
        if len(field.values) == 1:
            return base
        return f"{base}[{value_index}]"
