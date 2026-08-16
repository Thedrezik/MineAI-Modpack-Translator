from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

from . import ie_manual_base as _base
from .core import ProtectedFragment, TranslationPlan, TranslationUnit, ValidationError
from .ie_manual_base import IeManualFingerprint


_PLACEHOLDER_RE = re.compile(r"\[#(\d+)#\]")


@dataclass(frozen=True)
class _IeSemanticAnchor:
    placeholder: str
    child_id: str
    prefix: str
    suffix: str


class ImmersiveEngineeringManualAdapter(_base.ImmersiveEngineeringManualAdapter):
    """IE manual adapter with source-owned explicit style/reset spans."""

    def prepare(self, path: str, source_text: str) -> TranslationPlan:
        spans: list[TranslationUnit] = []
        semantic_anchors: dict[str, tuple[_IeSemanticAnchor, ...]] = {}
        cursor = 0
        token_index = 0
        for token_match in _base._TOKEN_RE.finditer(source_text):
            self._add_plain_units_semantic(
                path,
                source_text,
                cursor,
                token_match.start(),
                spans,
                semantic_anchors,
            )
            self._add_token_units(path, source_text, token_match, token_index, spans)
            cursor = token_match.end()
            token_index += 1
        self._add_plain_units_semantic(
            path,
            source_text,
            cursor,
            len(source_text),
            spans,
            semantic_anchors,
        )
        spans.sort(
            key=lambda unit: (
                unit.start,
                0 if unit.kind == "ie-manual-prose" else 1,
                unit.id,
            )
        )
        return TranslationPlan(
            path=path,
            source_text=source_text,
            units=tuple(spans),
            metadata={
                "fingerprint": self.fingerprint(source_text),
                "semantic_payloads": {unit.id: unit.text for unit in spans},
                "semantic_anchors": semantic_anchors,
            },
        )

    def apply(self, plan: TranslationPlan, translations: Mapping[str, str]) -> str:
        known = {unit.id for unit in plan.units}
        unknown = set(translations) - known
        if unknown:
            raise ValidationError(f"Unknown translation unit ids: {sorted(unknown)!r}")
        anchors_meta = plan.metadata.get("semantic_anchors", {})
        if not isinstance(anchors_meta, dict):
            raise ValidationError("IE manual plan is missing semantic anchor metadata")
        by_id = {unit.id: unit for unit in plan.units}
        replacements: list[tuple[int, int, str]] = []

        for unit in plan.units:
            if unit.kind == "ie-format-semantic-child":
                continue
            anchors = anchors_meta.get(unit.id, ())
            related = {unit.id}
            for anchor in anchors:
                if not isinstance(anchor, _IeSemanticAnchor):
                    raise ValidationError(f"Invalid IE semantic anchor metadata for {unit.id}")
                related.add(anchor.child_id)
            if not any(unit_id in translations for unit_id in related):
                continue
            candidate = translations.get(unit.id, unit.text)
            self._validate_candidate_text(unit, candidate)
            restored = self._restore_protected(unit, candidate)
            for anchor in anchors:
                child = by_id.get(anchor.child_id)
                if child is None:
                    raise ValidationError(f"Missing IE semantic child {anchor.child_id}")
                child_candidate = translations.get(child.id, child.text)
                self._validate_candidate_text(child, child_candidate)
                child_restored = self._restore_protected(child, child_candidate)
                if restored.count(anchor.placeholder) != 1:
                    raise ValidationError(f"Unit {unit.id} changed semantic anchor placement")
                restored = restored.replace(
                    anchor.placeholder,
                    anchor.prefix + child_restored + anchor.suffix,
                    1,
                )
            replacements.append((unit.start, unit.end, restored))

        output = plan.source_text
        for start, end, replacement in sorted(replacements, reverse=True):
            output = output[:start] + replacement + output[end:]
        self.validate(plan.source_text, output)
        return output

    @staticmethod
    def _validate_candidate_text(unit: TranslationUnit, translated: str) -> None:
        if "\n" in translated or "\r" in translated:
            raise ValidationError(f"Unit {unit.id} introduced a newline")
        if unit.kind in {"ie-link-label", "ie-config-label"} and any(
            delimiter in translated for delimiter in (";", "<", ">")
        ):
            raise ValidationError(f"Unit {unit.id} changed manual-token delimiters")

    def _add_plain_units_semantic(
        self,
        path: str,
        text: str,
        start: int,
        end: int,
        out: list[TranslationUnit],
        semantic_anchors: dict[str, tuple[_IeSemanticAnchor, ...]],
    ) -> None:
        if start >= end:
            return
        block = text[start:end]
        for match in re.finditer(r"[^\r\n]+", block):
            raw = match.group(0)
            leading = len(raw) - len(raw.lstrip(" \t"))
            payload = raw.strip(" \t")
            if not payload or not self._has_prose(payload):
                continue
            unit_start = start + match.start() + leading
            unit_end = unit_start + len(payload)
            outer_id = f"span:{unit_start}:prose"
            anchor_spans = self._semantic_format_spans(payload)
            outer_parts: list[str] = []
            reserved: list[str] = []
            anchors: list[_IeSemanticAnchor] = []
            cursor = 0
            literal_ids = [int(m.group(1)) for m in _PLACEHOLDER_RE.finditer(payload)]
            next_marker = max(literal_ids) + 1 if literal_ids else 0
            for anchor_index, (a_start, a_end, prefix, body, suffix) in enumerate(anchor_spans):
                outer_parts.append(payload[cursor:a_start])
                placeholder = f"[#{next_marker}#]"
                next_marker += 1
                outer_parts.append(placeholder)
                reserved.append(placeholder)
                child_id = f"{outer_id}#anchor:{anchor_index}"
                child_masked, child_protected = self._protect_formatting(body)
                out.append(
                    TranslationUnit(
                        id=child_id,
                        text=child_masked,
                        start=unit_start,
                        end=unit_end,
                        kind="ie-format-semantic-child",
                        context=f"{path}:format-anchor:{anchor_index}",
                        protected=child_protected,
                    )
                )
                anchors.append(_IeSemanticAnchor(placeholder, child_id, prefix, suffix))
                cursor = a_end
            outer_parts.append(payload[cursor:])
            outer_source = "".join(outer_parts)
            masked, protected = self._protect_formatting_reserved(
                outer_source,
                tuple(reserved),
            )
            if not anchors and not self._has_prose(_PLACEHOLDER_RE.sub(" ", masked)):
                continue
            out.append(
                TranslationUnit(
                    id=outer_id,
                    text=masked,
                    start=unit_start,
                    end=unit_end,
                    kind="ie-manual-prose",
                    context=path,
                    protected=protected,
                )
            )
            if anchors:
                semantic_anchors[outer_id] = tuple(anchors)

    def _protect_formatting_reserved(
        self,
        text: str,
        reserved_placeholders: tuple[str, ...],
    ) -> tuple[str, tuple[ProtectedFragment, ...]]:
        reserved = set(reserved_placeholders)
        spans = [
            (match.start(), match.end())
            for match in _base._SECTION_RESET_BOUNDARY_RE.finditer(text)
        ]
        spans.extend(
            (match.start(), match.end())
            for match in _base._SECTION_FORMAT_RE.finditer(text)
        )
        placeholder_matches = list(_PLACEHOLDER_RE.finditer(text))
        literal_ids = [int(match.group(1)) for match in placeholder_matches]
        spans.extend(
            (match.start(), match.end())
            for match in placeholder_matches
            if match.group(0) not in reserved
        )
        merged: list[list[int]] = []
        for span_start, span_end in sorted(spans):
            if not merged or span_start >= merged[-1][1]:
                merged.append([span_start, span_end])
            elif span_end > merged[-1][1]:
                merged[-1][1] = span_end
        base = max(literal_ids) + 1 if literal_ids else 0
        cursor = 0
        masked: list[str] = []
        protected: list[ProtectedFragment] = []
        for offset, (span_start, span_end) in enumerate(merged):
            masked.append(text[cursor:span_start])
            placeholder = f"[#{base + offset}#]"
            while placeholder in reserved:
                base += 1
                placeholder = f"[#{base + offset}#]"
            masked.append(placeholder)
            protected.append(ProtectedFragment(placeholder, text[span_start:span_end]))
            cursor = span_end
        masked.append(text[cursor:])
        return "".join(masked), tuple(protected)

    @staticmethod
    def _restore_protected(unit: TranslationUnit, translated: str) -> str:
        expected = [match.group(0) for match in _PLACEHOLDER_RE.finditer(unit.text)]
        actual = [match.group(0) for match in _PLACEHOLDER_RE.finditer(translated)]
        if actual != expected:
            raise ValidationError(f"Unit {unit.id} changed protected placeholder order")
        restored = translated
        for fragment in unit.protected:
            restored = restored.replace(fragment.placeholder, fragment.value)
        return restored

    def _semantic_format_spans(
        self,
        text: str,
    ) -> list[tuple[int, int, str, str, str]]:
        out: list[tuple[int, int, str, str, str]] = []
        index = 0
        opener_re = re.compile(r"(?:§[0-9A-FK-Oa-fk-o])+")
        while index < len(text):
            opener = opener_re.search(text, index)
            if opener is None:
                break
            reset = _base._SECTION_FORMAT_RE.search(text, opener.end())
            if reset is None or reset.group(0).lower() != "§r":
                index = opener.end()
                continue
            body = text[opener.end():reset.start()]
            if (
                not body
                or body != body.strip()
                or "\n" in body
                or "\r" in body
                or not self._has_prose(body)
            ):
                index = reset.end()
                continue
            suffix_end = reset.end()
            boundary = _base._SECTION_RESET_BOUNDARY_RE.match(text, reset.start())
            if boundary is not None:
                suffix_end = boundary.end()
            out.append(
                (
                    opener.start(),
                    suffix_end,
                    text[opener.start():opener.end()],
                    body,
                    text[reset.start():suffix_end],
                )
            )
            index = suffix_end
        return out


__all__ = ["ImmersiveEngineeringManualAdapter", "IeManualFingerprint"]
