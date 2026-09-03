from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping

from .core import TranslationPlan, TranslationUnit, ValidationError
from .locale_merge import LocaleMergePlan
from .minecraft_text import MinecraftTextComponentAdapter
from .runtime_locale import (
    LocaleMergePlanner as _RuntimeLocaleMergePlanner,
    MinecraftLangJsonAdapter as _RuntimeMinecraftLangJsonAdapter,
)

_GAG_COMPONENT_KEYS = frozenset({"text", "extra", "strikethrough"})
_TEMPAD_COMPONENT_KEYS = frozenset({"text", "color", "index"})
_SERIALIZED_COMPONENT_KEYS = frozenset({"text", "color", "clickEvent"})
_SERIALIZED_CLICK_EVENT_KEYS = frozenset({"action", "value"})
_NESTED_LOCALE_JSON_STRING = "nested-locale-json-string"


@dataclass(frozen=True)
class StructuredLangFingerprint:
    keys: tuple[str, ...]
    component_locators: tuple[tuple[str, str], ...]
    skeleton: str


@dataclass(frozen=True)
class _TargetValue:
    kind: str
    value: str | None = None
    leaves: Mapping[str, str] | None = None
    fingerprint: str | None = None


class MinecraftLangJsonAdapter(_RuntimeMinecraftLangJsonAdapter):
    """Minecraft locale adapter with strict, corpus-proven Component safety.

    Supported structured locale values are deliberately schema-driven rather
    than recursive JSON translation:

    * the original GAG ``text``/``extra``/``strikethrough`` component shape;
    * Tempad's root-list ``text``/``color``/``index`` component shape;
    * serialized JSON Component arrays stored inside an ordinary locale string,
      as used by Actually Additions and Iron Furnaces update messages.

    Unknown object/list values and unknown serialized JSON shapes stay outside
    the translation plan.
    """

    name = "minecraft-lang-json"

    def prepare(self, path: str, source_text: str) -> TranslationPlan:
        base_plan = super().prepare(path, source_text)
        units = list(base_plan.units)
        metadata = dict(base_plan.metadata)
        original_values = dict(metadata.get("original_values", {}))
        unsupported = list(metadata.get("unsupported_non_string_keys", ()))
        unit_keys = {unit.id: unit.context for unit in base_plan.units}
        structured_locators: dict[str, str] = {}
        structured_fingerprints: dict[str, str] = {}
        structured_keys: list[str] = []
        serialized_keys: list[str] = []
        unit_encodings: dict[str, str] = {}

        for entry in self._parse_entries(source_text):
            if entry.is_string:
                assert isinstance(entry.value, str)
                serialized = self._serialized_component_targets(entry.value)
                if serialized is None:
                    continue

                base_unit_id = f"key:{entry.key}"
                units = [unit for unit in units if unit.id != base_unit_id]
                original_values.pop(base_unit_id, None)
                unit_keys.pop(base_unit_id, None)
                if entry.key not in serialized_keys:
                    serialized_keys.append(entry.key)

                raw_token = source_text[entry.value_start : entry.value_end]
                boundaries = self._decoded_json_string_boundaries(raw_token)
                structured_fingerprints[entry.key] = self._serialized_component_skeleton(
                    entry.value, serialized
                )

                for locator, node, value in serialized:
                    if not self._is_serialized_translatable_text(value):
                        continue
                    masked, protected = self._protect(value)
                    unit_id = f"key:{entry.key}:serialized:{locator}"
                    start = entry.value_start + boundaries[node.start]
                    end = entry.value_start + boundaries[node.end]
                    units.append(
                        TranslationUnit(
                            id=unit_id,
                            text=masked,
                            start=start,
                            end=end,
                            kind="minecraft-serialized-locale-component",
                            context=entry.key,
                            protected=protected,
                        )
                    )
                    original_values[unit_id] = value
                    unit_keys[unit_id] = entry.key
                    structured_locators[unit_id] = locator
                    unit_encodings[unit_id] = _NESTED_LOCALE_JSON_STRING
                continue

            raw = source_text[entry.value_start : entry.value_end]
            targets = self._component_targets(raw)
            if targets is None:
                continue

            structured_keys.append(entry.key)
            unsupported = [key for key in unsupported if key != entry.key]
            structured_fingerprints[entry.key] = self._component_skeleton(raw, targets)

            for locator, node, value in targets:
                if not MinecraftTextComponentAdapter._has_prose(value):
                    continue
                masked, protected = self._protect(value)
                unit_id = f"key:{entry.key}:component:{locator}"
                units.append(
                    TranslationUnit(
                        id=unit_id,
                        text=masked,
                        start=entry.value_start + node.start,
                        end=entry.value_start + node.end,
                        kind="minecraft-structured-locale-component",
                        context=entry.key,
                        protected=protected,
                    )
                )
                original_values[unit_id] = value
                unit_keys[unit_id] = entry.key
                structured_locators[unit_id] = locator

        metadata.update(
            {
                "fingerprint": self.fingerprint(source_text),
                "original_values": original_values,
                "unsupported_non_string_keys": tuple(unsupported),
                "structured_component_keys": tuple(structured_keys),
                "serialized_component_keys": tuple(serialized_keys),
                "unit_keys": unit_keys,
                "structured_unit_locators": structured_locators,
                "structured_key_fingerprints": structured_fingerprints,
                "unit_encodings": unit_encodings,
            }
        )
        return TranslationPlan(
            path=path,
            source_text=source_text,
            units=tuple(units),
            metadata=metadata,
        )

    def apply(self, plan: TranslationPlan, translations: Mapping[str, str]) -> str:
        known = {unit.id for unit in plan.units}
        unknown = set(translations) - known
        if unknown:
            raise ValidationError(f"Unknown translation unit ids: {sorted(unknown)!r}")
        original_values = plan.metadata.get("original_values")
        if not isinstance(original_values, dict):
            raise ValidationError("Translation plan is missing original locale values")
        unit_encodings = plan.metadata.get("unit_encodings", {})
        if not isinstance(unit_encodings, dict):
            raise ValidationError("Translation plan has invalid locale unit encodings")

        replacements: list[tuple[int, int, str]] = []
        for unit in plan.units:
            if unit.id not in translations:
                continue
            restored = self._restore_protected(unit, translations[unit.id])
            if restored == original_values.get(unit.id):
                token = plan.source_text[unit.start : unit.end]
            elif unit_encodings.get(unit.id) == _NESTED_LOCALE_JSON_STRING:
                token = self._encode_nested_locale_string(restored)
            else:
                token = json.dumps(restored, ensure_ascii=False)
            replacements.append((unit.start, unit.end, token))

        output = plan.source_text
        for start, end, token in sorted(replacements, reverse=True):
            output = output[:start] + token + output[end:]
        self.validate(plan.source_text, output)
        return output

    def validate(self, source_text: str, output_text: str) -> None:
        if self.fingerprint(source_text) != self.fingerprint(output_text):
            raise ValidationError("Minecraft lang JSON structure changed during reconstruction")

    def fingerprint(self, text: str) -> StructuredLangFingerprint:
        entries = self._parse_entries(text)
        ignored_metadata = self._repeated_metadata_keys(entries)
        spans: list[tuple[int, int, str]] = []
        locators: list[tuple[str, str]] = []

        for entry in entries:
            if entry.is_string and entry.key not in ignored_metadata:
                assert isinstance(entry.value, str)
                serialized = self._serialized_component_targets(entry.value)
                if serialized is None:
                    spans.append((entry.value_start, entry.value_end, '"<mineai-value>"'))
                    continue

                raw_token = text[entry.value_start : entry.value_end]
                boundaries = self._decoded_json_string_boundaries(raw_token)
                for locator, node, value in serialized:
                    if not self._is_serialized_translatable_text(value):
                        continue
                    spans.append(
                        (
                            entry.value_start + boundaries[node.start],
                            entry.value_start + boundaries[node.end],
                            '\\"<mineai-serialized-text>\\"',
                        )
                    )
                    locators.append((entry.key, "serialized:" + locator))
                continue
            if entry.is_string:
                continue

            raw = text[entry.value_start : entry.value_end]
            targets = self._component_targets(raw)
            if targets is None:
                continue
            for locator, node, _ in targets:
                spans.append(
                    (
                        entry.value_start + node.start,
                        entry.value_start + node.end,
                        '"<mineai-component-text>"',
                    )
                )
                locators.append((entry.key, locator))

        out: list[str] = []
        cursor = 0
        for start, end, marker in sorted(spans):
            if start < cursor:
                raise ValidationError("Overlapping Minecraft locale translation spans")
            out.append(text[cursor:start])
            out.append(marker)
            cursor = end
        out.append(text[cursor:])
        return StructuredLangFingerprint(
            keys=tuple(entry.key for entry in entries),
            component_locators=tuple(locators),
            skeleton="".join(out),
        )

    @staticmethod
    def _component_targets(raw: str):
        parser = MinecraftTextComponentAdapter()
        root = parser._parse(raw)
        gag = MinecraftLangJsonAdapter._gag_component_targets(root)
        if gag is not None:
            return gag
        return MinecraftLangJsonAdapter._tempad_component_targets(root)

    @staticmethod
    def _gag_component_targets(root):
        if root.kind != "array":
            return None
        targets: list[tuple[str, object, str]] = []
        seen = {"object": False, "text": False, "extra": False, "strikethrough": False}

        def walk(node, locator: str) -> bool:
            if node.kind == "string":
                assert isinstance(node.value, str)
                targets.append((locator or "/", node, node.value))
                return True
            if node.kind == "array":
                return all(walk(item, f"{locator}/{index}") for index, item in enumerate(node.items))
            if node.kind != "object":
                return False

            seen["object"] = True
            members = {member.key: member.value for member in node.members}
            if not members or not set(members).issubset(_GAG_COMPONENT_KEYS):
                return False
            if "text" not in members and "extra" not in members:
                return False

            text_node = members.get("text")
            if text_node is not None:
                seen["text"] = True
                if text_node.kind != "string":
                    return False
                assert isinstance(text_node.value, str)
                targets.append((locator + "/text", text_node, text_node.value))

            extra = members.get("extra")
            if extra is not None:
                seen["extra"] = True
                if extra.kind != "array":
                    return False
                for index, item in enumerate(extra.items):
                    if not walk(item, f"{locator}/extra/{index}"):
                        return False

            strike = members.get("strikethrough")
            if strike is not None:
                seen["strikethrough"] = True
                if not (strike.kind == "scalar" and isinstance(strike.value, bool)):
                    return False
            return True

        if not walk(root, "") or not all(seen.values()):
            return None
        return tuple(targets)

    @staticmethod
    def _tempad_component_targets(root):
        if root.kind != "array" or len(root.items) < 2:
            return None
        first = root.items[0]
        if first.kind != "string" or first.value != "":
            return None

        targets: list[tuple[str, object, str]] = []
        seen_text = False
        for index, item in enumerate(root.items[1:], start=1):
            if item.kind != "object":
                return None
            members = {member.key: member.value for member in item.members}
            if not members or not set(members).issubset(_TEMPAD_COMPONENT_KEYS):
                return None
            color = members.get("color")
            if color is None or color.kind != "string":
                return None

            text_node = members.get("text")
            index_node = members.get("index")
            if (text_node is None) == (index_node is None):
                return None
            if text_node is not None:
                if text_node.kind != "string":
                    return None
                assert isinstance(text_node.value, str)
                targets.append((f"/{index}/text", text_node, text_node.value))
                seen_text = True
            else:
                if not (
                    index_node.kind == "scalar"
                    and isinstance(index_node.value, int)
                    and not isinstance(index_node.value, bool)
                ):
                    return None
        return tuple(targets) if seen_text else None

    @staticmethod
    def _component_skeleton(raw: str, targets) -> str:
        out: list[str] = []
        cursor = 0
        for _, node, _ in sorted(targets, key=lambda item: item[1].start):
            out.append(raw[cursor : node.start])
            out.append('"<mineai-component-text>"')
            cursor = node.end
        out.append(raw[cursor:])
        try:
            normalized = json.loads("".join(out))
        except json.JSONDecodeError as exc:
            raise ValidationError("Structured locale Component normalization failed") from exc
        return json.dumps(
            normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

    @staticmethod
    def _serialized_component_targets(decoded: str):
        parser = MinecraftTextComponentAdapter()
        try:
            root = parser._parse(decoded)
        except ValidationError:
            return None
        if root.kind != "array" or len(root.items) < 2:
            return None

        targets: list[tuple[str, object, str]] = []
        seen_color = False
        for index, item in enumerate(root.items):
            if item.kind != "object":
                return None
            members = {member.key: member.value for member in item.members}
            if not members or not set(members).issubset(_SERIALIZED_COMPONENT_KEYS):
                return None
            text_node = members.get("text")
            if text_node is None or text_node.kind != "string":
                return None
            assert isinstance(text_node.value, str)
            targets.append((f"/{index}/text", text_node, text_node.value))

            color = members.get("color")
            if color is not None:
                if color.kind != "string":
                    return None
                seen_color = True

            click = members.get("clickEvent")
            if click is not None:
                if click.kind != "object":
                    return None
                click_members = {member.key: member.value for member in click.members}
                if set(click_members) != _SERIALIZED_CLICK_EVENT_KEYS:
                    return None
                action = click_members["action"]
                value = click_members["value"]
                if action.kind != "string" or value.kind != "string":
                    return None
                if action.value != "open_url" or value.value != "%s":
                    return None

        if not seen_color:
            return None
        return tuple(targets)

    def _serialized_component_skeleton(self, decoded: str, targets) -> str:
        out: list[str] = []
        cursor = 0
        for _, node, value in sorted(targets, key=lambda item: item[1].start):
            if not self._is_serialized_translatable_text(value):
                continue
            out.append(decoded[cursor : node.start])
            out.append('"<mineai-serialized-text>"')
            cursor = node.end
        out.append(decoded[cursor:])
        try:
            normalized = json.loads("".join(out))
        except json.JSONDecodeError as exc:
            raise ValidationError("Serialized locale Component normalization failed") from exc
        return json.dumps(
            normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

    def _is_serialized_translatable_text(self, value: str) -> bool:
        masked, protected = self._protect(value)
        visible = masked
        for fragment in protected:
            visible = visible.replace(fragment.placeholder, "")
        return any(char.isalpha() for char in visible)

    @staticmethod
    def _decoded_json_string_boundaries(raw_token: str) -> tuple[int, ...]:
        """Map decoded-string character boundaries to offsets in its JSON token."""

        if len(raw_token) < 2 or raw_token[0] != '"' or raw_token[-1] != '"':
            raise ValidationError("Serialized locale Component is not a JSON string token")
        try:
            decoded = json.loads(raw_token)
        except json.JSONDecodeError as exc:
            raise ValidationError("Invalid serialized locale JSON string token") from exc
        if not isinstance(decoded, str):
            raise ValidationError("Serialized locale token did not decode to a string")

        boundaries = [1]
        index = 1
        while index < len(raw_token) - 1:
            if raw_token[index] != "\\":
                piece = raw_token[index]
                index += 1
            else:
                if index + 1 >= len(raw_token) - 1:
                    raise ValidationError("Incomplete escape in serialized locale string")
                if raw_token[index + 1] == "u":
                    if index + 6 > len(raw_token) - 1:
                        raise ValidationError("Incomplete Unicode escape in serialized locale string")
                    chunk = raw_token[index : index + 6]
                    try:
                        codepoint = int(raw_token[index + 2 : index + 6], 16)
                    except ValueError as exc:
                        raise ValidationError("Invalid Unicode escape in serialized locale string") from exc
                    if (
                        0xD800 <= codepoint <= 0xDBFF
                        and index + 12 <= len(raw_token) - 1
                        and raw_token[index + 6 : index + 8] == "\\u"
                    ):
                        try:
                            low = int(raw_token[index + 8 : index + 12], 16)
                        except ValueError as exc:
                            raise ValidationError("Invalid surrogate escape in serialized locale string") from exc
                        if 0xDC00 <= low <= 0xDFFF:
                            chunk = raw_token[index : index + 12]
                    try:
                        piece = json.loads('"' + chunk + '"')
                    except json.JSONDecodeError as exc:
                        raise ValidationError("Invalid Unicode escape in serialized locale string") from exc
                    index += len(chunk)
                else:
                    chunk = raw_token[index : index + 2]
                    try:
                        piece = json.loads('"' + chunk + '"')
                    except json.JSONDecodeError as exc:
                        raise ValidationError("Invalid escape in serialized locale string") from exc
                    index += 2
            if not isinstance(piece, str) or len(piece) != 1:
                raise ValidationError("Ambiguous decoded span in serialized locale string")
            boundaries.append(index)

        if len(boundaries) != len(decoded) + 1:
            raise ValidationError("Serialized locale source-span mapping failed")
        return tuple(boundaries)

    @staticmethod
    def _encode_nested_locale_string(value: str) -> str:
        inner = json.dumps(value, ensure_ascii=False)
        return json.dumps(inner, ensure_ascii=False)[1:-1]


class LocaleMergePlanner(_RuntimeLocaleMergePlanner):
    """Merge ordinary and strict structured locale Components from EN structure."""

    def __init__(self, adapter: MinecraftLangJsonAdapter | None = None) -> None:
        super().__init__(adapter or MinecraftLangJsonAdapter())

    def plan(
        self,
        source_path: str,
        source_text: str,
        target_code: str,
        target_text: str | None = None,
        mode: str = "append",
    ) -> LocaleMergePlan:
        if mode not in self.VALID_MODES:
            raise ValueError(f"Unsupported locale merge mode: {mode}")
        source_plan = self.adapter.prepare(source_path, source_text)
        original_values = source_plan.metadata.get("original_values")
        unit_keys = source_plan.metadata.get("unit_keys")
        structured_locators = source_plan.metadata.get("structured_unit_locators")
        structured_fingerprints = source_plan.metadata.get("structured_key_fingerprints")
        if not isinstance(original_values, dict) or not isinstance(unit_keys, dict):
            raise ValidationError("Source locale plan is missing merge metadata")
        if not isinstance(structured_locators, dict) or not isinstance(structured_fingerprints, dict):
            raise ValidationError("Source locale plan is missing structured Component metadata")

        target_parse_failed = False
        if target_text is None:
            target_values = {}
        else:
            try:
                target_values = self._catalog_extended(target_text)
            except ValidationError:
                target_values = {}
                target_parse_failed = True

        source_keys = {unit_keys[unit.id] for unit in source_plan.units}
        target_keys = set(target_values)
        missing = tuple(sorted(source_keys - target_keys))
        orphan = tuple(sorted(target_keys - source_keys))
        existing: dict[str, str] = {}
        pending: list[str] = []
        invalid: set[str] = set(source_keys) if target_parse_failed else set()

        grouped: dict[str, list[TranslationUnit]] = {}
        for unit in source_plan.units:
            grouped.setdefault(unit_keys[unit.id], []).append(unit)

        for key, key_units in grouped.items():
            if mode == "force":
                pending.extend(unit.id for unit in key_units)
                continue
            target = target_values.get(key)
            is_structured = any(unit.id in structured_locators for unit in key_units)

            if target is None:
                pending.extend(unit.id for unit in key_units)
                continue

            if is_structured:
                if (
                    target.kind != "structured"
                    or target.fingerprint != structured_fingerprints.get(key)
                    or target.leaves is None
                ):
                    invalid.add(key)
                    pending.extend(unit.id for unit in key_units)
                    continue
                for unit in key_units:
                    locator = structured_locators.get(unit.id)
                    current = target.leaves.get(locator) if locator is not None else None
                    if current is None or current == "":
                        pending.append(unit.id)
                        continue
                    source_value = original_values[unit.id]
                    if not self._critical_placeholders_match(source_value, current):
                        invalid.add(key)
                        pending.append(unit.id)
                        continue
                    if mode == "append" and current == source_value:
                        pending.append(unit.id)
                        continue
                    existing[unit.id] = current
                continue

            if target.kind != "string" or target.value is None or target.value == "":
                if target.kind != "string":
                    invalid.add(key)
                pending.extend(unit.id for unit in key_units)
                continue
            unit = key_units[0]
            source_value = original_values[unit.id]
            if not self._critical_placeholders_match(source_value, target.value):
                invalid.add(key)
                pending.append(unit.id)
                continue
            if mode == "append" and target.value == source_value:
                pending.append(unit.id)
                continue
            existing[unit.id] = target.value

        return LocaleMergePlan(
            source_plan=source_plan,
            target_path=self.adapter.target_path(source_path, target_code),
            mode=mode,
            pending_ids=tuple(pending),
            existing_values=existing,
            missing_keys=missing,
            orphan_target_keys=orphan,
            invalid_existing_keys=tuple(sorted(invalid)),
        )

    def build(self, plan: LocaleMergePlan, translations: Mapping[str, str]) -> str:
        required = set(plan.pending_ids)
        unknown = set(translations) - required
        if unknown:
            raise ValidationError(f"Translations contain non-pending ids: {sorted(unknown)!r}")
        missing = required - set(translations)
        if missing:
            raise ValidationError(f"Missing translations for pending ids: {sorted(missing)!r}")

        originals = plan.source_plan.metadata.get("original_values")
        if not isinstance(originals, dict):
            raise ValidationError("Source locale plan is missing original values")
        unit_encodings = plan.source_plan.metadata.get("unit_encodings", {})
        if not isinstance(unit_encodings, dict):
            raise ValidationError("Source locale plan has invalid unit encodings")

        replacements: list[tuple[int, int, str]] = []
        for unit in plan.source_plan.units:
            if unit.id in plan.existing_values:
                value = plan.existing_values[unit.id]
            elif unit.id in translations:
                value = self.adapter._restore_protected(unit, translations[unit.id])
            else:
                continue

            if value == originals.get(unit.id):
                token = plan.source_plan.source_text[unit.start : unit.end]
            elif unit_encodings.get(unit.id) == _NESTED_LOCALE_JSON_STRING:
                token = self.adapter._encode_nested_locale_string(value)
            else:
                token = json.dumps(value, ensure_ascii=False)
            replacements.append((unit.start, unit.end, token))

        output = plan.source_plan.source_text
        for start, end, token in sorted(replacements, reverse=True):
            output = output[:start] + token + output[end:]
        self.adapter.validate(plan.source_plan.source_text, output)
        return output

    def _catalog_extended(self, text: str) -> dict[str, _TargetValue]:
        out: dict[str, _TargetValue] = {}
        for entry in self.adapter._parse_entries(text):
            if entry.is_string:
                assert isinstance(entry.value, str)
                serialized = self.adapter._serialized_component_targets(entry.value)
                if serialized is None:
                    out[entry.key] = _TargetValue("string", value=entry.value)
                    continue
                leaves = {
                    locator: value
                    for locator, _, value in serialized
                    if self.adapter._is_serialized_translatable_text(value)
                }
                out[entry.key] = _TargetValue(
                    "structured",
                    leaves=leaves,
                    fingerprint=self.adapter._serialized_component_skeleton(
                        entry.value, serialized
                    ),
                )
                continue

            raw = text[entry.value_start : entry.value_end]
            targets = self.adapter._component_targets(raw)
            if targets is None:
                out[entry.key] = _TargetValue("unsupported")
                continue
            leaves = {locator: value for locator, _, value in targets}
            out[entry.key] = _TargetValue(
                "structured",
                leaves=leaves,
                fingerprint=self.adapter._component_skeleton(raw, targets),
            )
        return out
