"""Lossless adapter for locale ``key=value`` resources."""

from __future__ import annotations

import hashlib
import re

from formatkit.adapters.markdown import _anchor, _lines
from formatkit.contracts import (
    ProtectedAnchor,
    TranslationPlan,
    TranslationUnit,
    ValidationReport,
)
from formatkit.tokenizer import GAME_TOKEN_PATTERN


_ENTRY = re.compile(r"^(?P<prefix>\s*[^#!\s][^=:]*?\s*[=:]\s*)(?P<value>.*)$")


def _protect(value: str) -> tuple[str, tuple[ProtectedAnchor, ...]]:
    anchors: list[ProtectedAnchor] = []
    output: list[str] = []
    cursor = 0
    for match in GAME_TOKEN_PATTERN.finditer(value):
        output.append(value[cursor : match.start()])
        output.append(_anchor(anchors, match.group(0)))
        cursor = match.end()
    output.append(value[cursor:])
    return "".join(output), tuple(anchors)


def _fingerprint(text: str) -> str:
    structure: list[tuple[str, str]] = []
    for line in _lines(text):
        match = _ENTRY.match(line.content)
        structure.append(
            (
                match.group("prefix") if match else line.content,
                line.ending,
            )
        )
    return hashlib.sha256(repr(tuple(structure)).encode("utf-8")).hexdigest()


def _validator(source: str, target: str) -> ValidationReport:
    source_fingerprint = _fingerprint(source)
    target_fingerprint = _fingerprint(target)
    return ValidationReport(
        source_fingerprint == target_fingerprint,
        () if source_fingerprint == target_fingerprint else (
            "Properties structure fingerprint changed",
        ),
        source_fingerprint,
        target_fingerprint,
    )


class PropertiesAdapter:
    adapter_id = "properties-v1"

    def supports(self, logical_path: str, text: str) -> bool:
        del text
        normalized = logical_path.replace("\\", "/").casefold()
        return normalized.endswith(".lang") and (
            "/en_us/" in normalized
            or normalized.endswith("/en_us.lang")
        )

    def plan(
        self,
        logical_path: str,
        text: str,
        target_locale: str,
        *,
        target_path_hint: str | None = None,
    ) -> TranslationPlan:
        target_path = target_path_hint or re.sub(
            r"(?i)(?<=/)en_us(?=/|\.lang$)",
            target_locale,
            logical_path.replace("\\", "/"),
            count=1,
        )
        units: list[TranslationUnit] = []
        for line in _lines(text):
            match = _ENTRY.match(line.content)
            if match is None or not re.search(r"[^\W\d_]", match.group("value")):
                continue
            raw_value = match.group("value")
            leading = len(raw_value) - len(raw_value.lstrip())
            trailing = len(raw_value) - len(raw_value.rstrip())
            start = line.start + match.start("value") + leading
            end = line.start + match.end("value") - trailing
            value = text[start:end]
            if not value:
                continue
            payload, anchors = _protect(value)
            units.append(
                TranslationUnit(
                    id=f"{self.adapter_id}:{start}:{end}",
                    payload=payload,
                    start=start,
                    end=end,
                    context=match.group("prefix").strip() + " " + payload,
                    anchors=anchors,
                    kind="property-value",
                )
            )
        return TranslationPlan(
            adapter_id=self.adapter_id,
            logical_path=logical_path,
            source_text=text,
            target_path=target_path,
            units=tuple(units),
            validator=_validator,
        )
