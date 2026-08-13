"""Public, filesystem-independent FormatKit contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping
import json
import re


ANCHOR_PATTERN = re.compile(r"⟦FK\d{4}⟧")
_UNSAFE_PAYLOAD_PATTERN = re.compile(
    r"<[^>\r\n]*>|\$\([^\r\n)]*\)|[§]x?(?:[0-9A-FK-ORa-fk-or])|"
    r"!\[|\]\([^\r\n)]*\)|\r|\n"
)


class FormatValidationError(ValueError):
    """Raised when translated payload cannot be applied losslessly."""


def normalize_anchor_boundaries(
    source_payload: str,
    candidate: str,
    anchors: tuple["ProtectedAnchor", ...],
) -> str:
    """Restore source-owned whitespace immediately beside protected anchors."""
    if not anchors:
        return candidate

    split_pattern = f"({ANCHOR_PATTERN.pattern})"
    source_parts = re.split(split_pattern, source_payload)
    candidate_parts = re.split(split_pattern, candidate)
    if len(source_parts) != len(candidate_parts):
        return candidate

    anchor_map = {anchor.token: anchor.source for anchor in anchors}
    for index in range(1, len(source_parts), 2):
        token = source_parts[index]
        source_anchor = anchor_map.get(token)
        if source_anchor is None or candidate_parts[index] != token:
            return candidate

        normalize_left = bool(
            source_anchor[:1].isspace()
            or source_anchor.startswith(("](", "][", "|"))
        )
        normalize_right = bool(
            source_anchor[-1:].isspace()
            or source_anchor in {"[", "![", "{"}
        )
        if normalize_left:
            expected = re.search(r"[ \t]*$", source_parts[index - 1]).group(0)
            candidate_parts[index - 1] = (
                re.sub(r"[ \t]+$", "", candidate_parts[index - 1])
                + expected
            )
        if normalize_right:
            expected = re.match(r"[ \t]*", source_parts[index + 1]).group(0)
            candidate_parts[index + 1] = (
                expected
                + re.sub(r"^[ \t]+", "", candidate_parts[index + 1])
            )
    return "".join(candidate_parts)


@dataclass(frozen=True)
class ProtectedAnchor:
    token: str
    source: str


@dataclass(frozen=True)
class TranslationUnit:
    id: str
    payload: str
    start: int
    end: int
    context: str
    anchors: tuple[ProtectedAnchor, ...] = ()
    kind: str = "text"
    encoding: str = "plain"

    @property
    def anchor_tokens(self) -> tuple[str, ...]:
        return tuple(anchor.token for anchor in self.anchors)


@dataclass(frozen=True)
class ValidationReport:
    ok: bool
    errors: tuple[str, ...] = ()
    source_fingerprint: str = ""
    target_fingerprint: str = ""


@dataclass(frozen=True)
class ApplyResult:
    text: str
    target_path: str | None
    validation: ValidationReport


Validator = Callable[[str, str], ValidationReport]
VisibleTextValidator = Callable[[str, str], str | None]


@dataclass(frozen=True)
class TranslationPlan:
    adapter_id: str
    logical_path: str
    source_text: str
    target_path: str | None
    units: tuple[TranslationUnit, ...]
    validator: Validator

    def __post_init__(self) -> None:
        previous_end = 0
        known_ids: set[str] = set()
        for unit in self.units:
            if unit.id in known_ids:
                raise ValueError(f"Duplicate translation unit id: {unit.id}")
            known_ids.add(unit.id)
            if unit.start < previous_end or unit.end <= unit.start:
                raise ValueError(
                    f"Overlapping or invalid translation unit range: {unit.id}"
                )
            if unit.end > len(self.source_text):
                raise ValueError(f"Translation unit exceeds source: {unit.id}")
            previous_end = unit.end

    def apply(self, translations: Mapping[str, str]) -> ApplyResult:
        unknown = set(translations) - {unit.id for unit in self.units}
        if unknown:
            raise FormatValidationError(
                "Unknown translation unit ids: " + ", ".join(sorted(unknown))
            )

        output = self.source_text
        for unit in reversed(self.units):
            candidate = translations.get(unit.id, unit.payload)
            if not isinstance(candidate, str):
                raise FormatValidationError(
                    f"Translation for {unit.id} is not a string"
                )
            actual_anchors = tuple(ANCHOR_PATTERN.findall(candidate))
            if actual_anchors != unit.anchor_tokens:
                raise FormatValidationError(
                    f"Protected anchors changed for {unit.id}: "
                    f"{unit.anchor_tokens!r} -> {actual_anchors!r}"
                )
            candidate = normalize_anchor_boundaries(
                unit.payload,
                candidate,
                unit.anchors,
            )
            visible_candidate = ANCHOR_PATTERN.sub("", candidate)
            if _UNSAFE_PAYLOAD_PATTERN.search(visible_candidate):
                raise FormatValidationError(
                    f"Translation introduced protected syntax in {unit.id}"
                )

            replacement = candidate
            for anchor in unit.anchors:
                replacement = replacement.replace(anchor.token, anchor.source, 1)
            if unit.encoding == "json-string-lossless" and unit.id not in translations:
                replacement = self.source_text[unit.start : unit.end]
            elif unit.encoding in {"json-string", "json-string-lossless"}:
                replacement = json.dumps(replacement, ensure_ascii=False)
            elif unit.encoding != "plain":
                raise FormatValidationError(
                    f"Unknown translation unit encoding: {unit.encoding}"
                )
            output = output[: unit.start] + replacement + output[unit.end :]

        report = self.validator(self.source_text, output)
        if not report.ok:
            raise FormatValidationError("; ".join(report.errors))
        return ApplyResult(
            text=output,
            target_path=self.target_path,
            validation=report,
        )

    def apply_resilient(
        self,
        translations: Mapping[str, str],
    ) -> tuple[ApplyResult, dict[str, str]]:
        """Apply all valid units and restore only candidates that break structure."""
        try:
            return self.apply(translations), {}
        except FormatValidationError:
            pass

        accepted: dict[str, str] = {}
        rejected: dict[str, str] = {}
        for unit in self.units:
            if unit.id not in translations:
                continue
            candidate = {**accepted, unit.id: translations[unit.id]}
            try:
                self.apply(candidate)
            except FormatValidationError as exc:
                rejected[unit.id] = str(exc)
            else:
                accepted[unit.id] = translations[unit.id]
        return self.apply(accepted), rejected

    def candidate_error(
        self,
        unit_id: str,
        candidate: str,
        visible_text_validator: VisibleTextValidator | None = None,
    ) -> str | None:
        """Validate immutable syntax, full visible text and link labels."""
        unit = next((item for item in self.units if item.id == unit_id), None)
        if unit is None:
            return f"Unknown translation unit id: {unit_id}"
        try:
            self.apply({unit_id: candidate})
        except FormatValidationError as exc:
            return str(exc)

        source_parts = re.split(f"({ANCHOR_PATTERN.pattern})", unit.payload)
        target_parts = re.split(f"({ANCHOR_PATTERN.pattern})", candidate)
        if len(source_parts) != len(target_parts):
            return "Protected segment layout changed"
        if visible_text_validator is None:
            return None
        source_visible = ANCHOR_PATTERN.sub("", unit.payload)
        target_visible = ANCHOR_PATTERN.sub("", candidate)
        reason = visible_text_validator(source_visible, target_visible)
        if reason:
            return reason

        anchor_sources = {
            anchor.token: anchor.source
            for anchor in unit.anchors
        }
        for index in range(2, len(source_parts) - 1, 2):
            source_part = source_parts[index]
            target_part = target_parts[index]
            left = anchor_sources.get(source_parts[index - 1], "")
            right = anchor_sources.get(source_parts[index + 1], "")
            is_markdown_label = (
                left in {"[", "![", "{"}
                and (right.startswith("](") or right.startswith("|"))
            )
            is_ie_label = left.lower().startswith("<link;") and right.endswith(">")
            if not (is_markdown_label or is_ie_label):
                continue
            reason = visible_text_validator(source_part, target_part)
            if reason:
                return reason
        return None

    def merge_existing(
        self,
        target: "TranslationPlan",
        target_regex: str,
    ) -> tuple["TranslationPlan", frozenset[str]]:
        """Protect already translated target fragments inside source units."""
        if self.adapter_id != target.adapter_id or len(self.units) != len(target.units):
            raise FormatValidationError("Existing target has a different unit layout")

        merged_units: list[TranslationUnit] = []
        pending: set[str] = set()
        for source_unit, target_unit in zip(self.units, target.units):
            if source_unit.kind != target_unit.kind:
                raise FormatValidationError("Existing target has different unit kinds")
            source_anchor_map = {
                anchor.token: anchor.source for anchor in source_unit.anchors
            }
            target_anchor_map = {
                anchor.token: anchor.source for anchor in target_unit.anchors
            }
            source_parts = re.split(f"({ANCHOR_PATTERN.pattern})", source_unit.payload)
            target_parts = re.split(f"({ANCHOR_PATTERN.pattern})", target_unit.payload)
            if len(source_parts) != len(target_parts):
                raise FormatValidationError("Existing target has different protected spans")

            output: list[str] = []
            anchors: list[ProtectedAnchor] = []

            def protect(value: str) -> None:
                token = f"⟦FK{len(anchors):04d}⟧"
                anchors.append(ProtectedAnchor(token=token, source=value))
                output.append(token)

            for source_part, target_part in zip(source_parts, target_parts):
                if ANCHOR_PATTERN.fullmatch(source_part):
                    if not ANCHOR_PATTERN.fullmatch(target_part):
                        raise FormatValidationError(
                            "Existing target moved a protected span"
                        )
                    source_value = source_anchor_map[source_part]
                    if target_anchor_map.get(target_part) != source_value:
                        raise FormatValidationError(
                            "Existing target changed protected syntax"
                        )
                    protect(source_value)
                    continue

                if re.search(target_regex, target_part) and target_part != source_part:
                    protect(target_part)
                else:
                    output.append(source_part)
                    if re.search(r"[A-Za-z]", source_part):
                        pending.add(source_unit.id)

            merged_units.append(
                TranslationUnit(
                    id=source_unit.id,
                    payload="".join(output),
                    start=source_unit.start,
                    end=source_unit.end,
                    context=source_unit.context,
                    anchors=tuple(anchors),
                    kind=source_unit.kind,
                    encoding=source_unit.encoding,
                )
            )

        return (
            TranslationPlan(
                adapter_id=self.adapter_id,
                logical_path=self.logical_path,
                source_text=self.source_text,
                target_path=self.target_path,
                units=tuple(merged_units),
                validator=self.validator,
            ),
            frozenset(pending),
        )
