from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

from . import patchouli_base as _base
from .core import ProtectedFragment, TranslationPlan, TranslationUnit, ValidationError
from .patchouli_base import PatchouliFingerprint


_PLACEHOLDER_RE = re.compile(r"\[#(\d+)#\]")


@dataclass(frozen=True)
class _SemanticAnchor:
    placeholder: str
    child_id: str
    prefix: str
    suffix: str


class PatchouliBookJsonAdapter(_base.PatchouliBookJsonAdapter):
    """Patchouli adapter with source-owned semantic style/link wrappers.

    Explicit balanced wrappers become nested payloads: the outer sentence owns
    one anchor placeholder while the visible body is translated as a child.
    Ambiguous directives keep the base adapter's protected-token behavior.
    """

    def prepare(self, path: str, source_text: str) -> TranslationPlan:
        root = self._parse(source_text)
        targets = []
        self._collect(root, "", targets)
        units: list[TranslationUnit] = []
        originals: dict[str, str] = {}
        semantic_payloads: dict[str, str] = {}
        field_originals: dict[str, str] = {}
        semantic_anchors: dict[str, tuple[_SemanticAnchor, ...]] = {}

        for locator, node, value in targets:
            if not self._has_prose(value):
                continue
            field_id = f"json:{locator}"
            spans = self._semantic_anchor_spans(value)
            reserved: list[str] = []
            anchors: list[_SemanticAnchor] = []
            outer_parts: list[str] = []
            cursor = 0
            literal_ids = [int(match.group(1)) for match in _PLACEHOLDER_RE.finditer(value)]
            next_marker = max(literal_ids) + 1 if literal_ids else 0

            for anchor_index, (start, end, prefix, body, suffix) in enumerate(spans):
                outer_parts.append(value[cursor:start])
                placeholder = f"[#{next_marker}#]"
                next_marker += 1
                outer_parts.append(placeholder)
                reserved.append(placeholder)
                child_id = f"{field_id}#anchor:{anchor_index}"
                child_text, child_protected = self._protect(body)
                units.append(
                    TranslationUnit(
                        id=child_id,
                        text=child_text,
                        start=node.start,
                        end=node.end,
                        kind="patchouli-semantic-child",
                        context=f"{path}:{locator}:anchor:{anchor_index}",
                        protected=child_protected,
                    )
                )
                originals[child_id] = body
                semantic_payloads[child_id] = child_text
                anchors.append(_SemanticAnchor(placeholder, child_id, prefix, suffix))
                cursor = end

            outer_parts.append(value[cursor:])
            outer_source = "".join(outer_parts)
            masked, protected = self._protect_reserved(outer_source, tuple(reserved))
            units.append(
                TranslationUnit(
                    id=field_id,
                    text=masked,
                    start=node.start,
                    end=node.end,
                    kind="patchouli-text",
                    context=f"{path}:{locator}",
                    protected=protected,
                )
            )
            originals[field_id] = value
            semantic_payloads[field_id] = masked
            field_originals[field_id] = value
            if anchors:
                semantic_anchors[field_id] = tuple(anchors)

        units.sort(key=lambda unit: (unit.start, 0 if unit.kind == "patchouli-text" else 1, unit.id))
        return TranslationPlan(
            path=path,
            source_text=source_text,
            units=tuple(units),
            metadata={
                "fingerprint": self.fingerprint(source_text),
                "originals": originals,
                "semantic_payloads": semantic_payloads,
                "field_originals": field_originals,
                "semantic_anchors": semantic_anchors,
            },
        )

    def apply(self, plan: TranslationPlan, translations: Mapping[str, str]) -> str:
        known = {unit.id for unit in plan.units}
        unknown = set(translations) - known
        if unknown:
            raise ValidationError(f"Unknown translation unit ids: {sorted(unknown)!r}")
        field_originals = plan.metadata.get("field_originals")
        anchors_meta = plan.metadata.get("semantic_anchors", {})
        if not isinstance(field_originals, dict) or not isinstance(anchors_meta, dict):
            raise ValidationError("Patchouli plan is missing semantic field metadata")
        by_id = {unit.id: unit for unit in plan.units}
        replacements: list[tuple[int, int, str]] = []

        for unit in plan.units:
            if unit.kind != "patchouli-text":
                continue
            anchors = anchors_meta.get(unit.id, ())
            related = {unit.id}
            for anchor in anchors:
                if not isinstance(anchor, _SemanticAnchor):
                    raise ValidationError(f"Invalid Patchouli semantic anchor metadata for {unit.id}")
                related.add(anchor.child_id)
            if not any(unit_id in translations for unit_id in related):
                continue

            outer = self._restore(unit, translations.get(unit.id, unit.text))
            for anchor in anchors:
                child = by_id.get(anchor.child_id)
                if child is None:
                    raise ValidationError(f"Missing Patchouli semantic child {anchor.child_id}")
                child_value = self._restore(child, translations.get(child.id, child.text))
                if outer.count(anchor.placeholder) != 1:
                    raise ValidationError(f"Unit {unit.id} changed semantic anchor placement")
                outer = outer.replace(
                    anchor.placeholder,
                    anchor.prefix + child_value + anchor.suffix,
                    1,
                )

            original = field_originals.get(unit.id)
            if not isinstance(original, str):
                raise ValidationError(f"Missing original Patchouli field for {unit.id}")
            if self._line_breaks(outer) != self._line_breaks(original):
                raise ValidationError(f"Patchouli unit {unit.id} changed line-break structure")
            token = (
                plan.source_text[unit.start:unit.end]
                if outer == original
                else __import__("json").dumps(outer, ensure_ascii=False)
            )
            replacements.append((unit.start, unit.end, token))

        output = plan.source_text
        for start, end, token in sorted(replacements, reverse=True):
            output = output[:start] + token + output[end:]
        self.validate(plan.source_text, output)
        return output

    def _protect_reserved(
        self,
        text: str,
        reserved_placeholders: tuple[str, ...],
    ) -> tuple[str, tuple[ProtectedFragment, ...]]:
        reserved = set(reserved_placeholders)
        spans: list[tuple[int, int]] = []
        placeholder_matches = list(_PLACEHOLDER_RE.finditer(text))
        literal_ids = [int(match.group(1)) for match in placeholder_matches]
        spans.extend(
            (match.start(), match.end())
            for match in placeholder_matches
            if match.group(0) not in reserved
        )
        backtick_dollar_re = getattr(_base, "_BACKTICK_DOLLAR_RE", None)
        if backtick_dollar_re is None:
            backtick_dollar_re = _base._CODE_DOLLAR_RE
        for regex in (
            _base._PATCHOULI_TOKEN_RE,
            backtick_dollar_re,
            _base._FORMAT_RE,
            _base._MC_FORMAT_RE,
            _base._MESSAGE_FORMAT_RE,
            _base._LINE_BREAK_RE,
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
            while placeholder in reserved:
                base += 1
                placeholder = f"[#{base + offset}#]"
            out.append(placeholder)
            protected.append(ProtectedFragment(placeholder, text[start:end]))
            cursor = end
        out.append(text[cursor:])
        return "".join(out), tuple(protected)

    @staticmethod
    def _restore(unit: TranslationUnit, translated: str) -> str:
        expected = [match.group(0) for match in _PLACEHOLDER_RE.finditer(unit.text)]
        actual = [match.group(0) for match in _PLACEHOLDER_RE.finditer(translated)]
        if expected != actual:
            raise ValidationError(f"Unit {unit.id} changed protected placeholder order")
        restored = translated
        for fragment in unit.protected:
            restored = restored.replace(fragment.placeholder, fragment.value)
        return restored

    def _semantic_anchor_spans(
        self,
        text: str,
    ) -> list[tuple[int, int, str, str, str]]:
        tokens = list(_base._PATCHOULI_TOKEN_RE.finditer(text))
        out: list[tuple[int, int, str, str, str]] = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if not self._is_anchor_opener(token.group(0)):
                index += 1
                continue
            opener_start = token.start()
            opener_end = token.end()
            openers = [token.group(0)]
            cursor = index + 1
            while (
                cursor < len(tokens)
                and tokens[cursor].start() == opener_end
                and self._is_anchor_opener(tokens[cursor].group(0))
            ):
                openers.append(tokens[cursor].group(0))
                opener_end = tokens[cursor].end()
                cursor += 1
            if cursor >= len(tokens):
                index += 1
                continue
            closer = tokens[cursor]
            body = text[opener_end:closer.start()]
            if (
                not body
                or body != body.strip()
                or "\n" in body
                or "\r" in body
                or not self._has_prose(body)
                or not self._is_anchor_closer(closer.group(0))
            ):
                index += 1
                continue
            closers = [closer.group(0)]
            closer_end = closer.end()
            cursor += 1
            while (
                cursor < len(tokens)
                and tokens[cursor].start() == closer_end
                and self._is_anchor_closer(tokens[cursor].group(0))
            ):
                closers.append(tokens[cursor].group(0))
                closer_end = tokens[cursor].end()
                cursor += 1
            if not self._anchor_closure_is_proven(openers, closers):
                index += 1
                continue
            out.append(
                (
                    opener_start,
                    closer_end,
                    text[opener_start:opener_end],
                    body,
                    text[closer.start():closer_end],
                )
            )
            index = cursor
        return out

    @staticmethod
    def _is_anchor_opener(token: str) -> bool:
        if token == "/$" or not token.startswith("$(") or not token.endswith(")"):
            return False
        body = token[2:-1]
        if not body or body.startswith("/"):
            return False
        lower = body.lower()
        if lower in {"br", "br1", "br2", "p", "li", "np"}:
            return False
        if lower.startswith("k:"):
            return False
        return True

    @staticmethod
    def _is_anchor_closer(token: str) -> bool:
        return token in {"$()", "/$", "$(/l)"}

    @staticmethod
    def _anchor_closure_is_proven(openers: list[str], closers: list[str]) -> bool:
        link_open = any(token.lower().startswith("$(l:") for token in openers)
        style_open = any(not token.lower().startswith("$(l:") for token in openers)
        if link_open and "$(/l)" in closers:
            return not style_open or any(token in {"$()", "/$"} for token in closers)
        if len(openers) == 1 and any(token in {"$()", "/$"} for token in closers):
            return True
        if not link_open and any(token in {"$()", "/$"} for token in closers):
            return True
        return False


__all__ = ["PatchouliBookJsonAdapter", "PatchouliFingerprint"]
