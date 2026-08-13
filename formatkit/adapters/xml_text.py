"""Lossless adapter for translatable XML text nodes."""

from __future__ import annotations

import hashlib
import re

from formatkit.adapters.markdown import _anchor
from formatkit.contracts import (
    ProtectedAnchor,
    TranslationPlan,
    TranslationUnit,
    ValidationReport,
)
from formatkit.tokenizer import GAME_TOKEN_SOURCE


_MARKUP = re.compile(r"<!--[\s\S]*?-->|<!\[CDATA\[[\s\S]*?\]\]>|<[^>]*>")
_PROTECTED = re.compile(rf"&(?:#\d+|#x[0-9a-f]+|[a-z]+);|{GAME_TOKEN_SOURCE}", re.I)


def _protect(value: str) -> tuple[str, tuple[ProtectedAnchor, ...]]:
    anchors: list[ProtectedAnchor] = []
    output: list[str] = []
    cursor = 0
    for match in _PROTECTED.finditer(value):
        output.append(value[cursor : match.start()])
        output.append(_anchor(anchors, match.group(0)))
        cursor = match.end()
    output.append(value[cursor:])
    return "".join(output), tuple(anchors)


def _fingerprint(text: str) -> str:
    structure = (
        tuple(match.group(0) for match in _MARKUP.finditer(text)),
        tuple(re.findall(r"\r\n|\r|\n", text)),
        tuple(re.findall(r"&(?:#\d+|#x[0-9a-f]+|[a-z]+);", text, re.I)),
    )
    return hashlib.sha256(repr(structure).encode("utf-8")).hexdigest()


def _validator(source: str, target: str) -> ValidationReport:
    source_fingerprint = _fingerprint(source)
    target_fingerprint = _fingerprint(target)
    return ValidationReport(
        source_fingerprint == target_fingerprint,
        () if source_fingerprint == target_fingerprint else (
            "XML structure fingerprint changed",
        ),
        source_fingerprint,
        target_fingerprint,
    )


class XmlTextAdapter:
    adapter_id = "xml-text-v1"

    def supports(self, logical_path: str, text: str) -> bool:
        del text
        normalized = logical_path.replace("\\", "/").casefold()
        return normalized.endswith(".xml") and "/en_us/" in normalized

    def plan(
        self,
        logical_path: str,
        text: str,
        target_locale: str,
        *,
        target_path_hint: str | None = None,
    ) -> TranslationPlan:
        target_path = target_path_hint or re.sub(
            r"(?i)(?<=/)en_us(?=/)",
            target_locale,
            logical_path.replace("\\", "/"),
            count=1,
        )
        units: list[TranslationUnit] = []
        cursor = 0
        for markup in list(_MARKUP.finditer(text)) + [None]:
            boundary = markup.start() if markup is not None else len(text)
            segment = text[cursor:boundary]
            leading = len(segment) - len(segment.lstrip())
            trailing = len(segment) - len(segment.rstrip())
            start = cursor + leading
            end = boundary - trailing
            value = text[start:end]
            if value and re.search(r"[^\W\d_]", value):
                payload, anchors = _protect(value)
                units.append(
                    TranslationUnit(
                        id=f"{self.adapter_id}:{start}:{end}",
                        payload=payload,
                        start=start,
                        end=end,
                        context=payload,
                        anchors=anchors,
                        kind="xml-text",
                    )
                )
            cursor = markup.end() if markup is not None else len(text)
        return TranslationPlan(
            adapter_id=self.adapter_id,
            logical_path=logical_path,
            source_text=text,
            target_path=target_path,
            units=tuple(units),
            validator=_validator,
        )
