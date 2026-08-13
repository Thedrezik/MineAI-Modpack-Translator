"""Lossless adapter for Immersive Engineering manual text files."""

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
from formatkit.tokenizer import GAME_TOKEN_SOURCE


_LINK = re.compile(r"<link;([^;>\r\n]+);([^;>\r\n]*)(;[^>\r\n]*)?>", re.I)
_TOKEN = re.compile(
    r"(?P<link><link;[^>\r\n]*>)|"
    r"(?P<tag><[^>\r\n]*>)|"
    rf"(?P<game>{GAME_TOKEN_SOURCE})|"
    r"(?P<newline>\r\n[ \t]*|\r[ \t]*|\n[ \t]*)",
    re.IGNORECASE,
)


def _protect_ie(value: str) -> tuple[str, tuple[ProtectedAnchor, ...]]:
    anchors: list[ProtectedAnchor] = []
    output: list[str] = []
    cursor = 0
    for match in _TOKEN.finditer(value):
        output.append(value[cursor : match.start()])
        token = match.group(0)
        link = _LINK.fullmatch(token)
        if link:
            prefix = f"<link;{link.group(1)};"
            suffix = (link.group(3) or "") + ">"
            output.append(_anchor(anchors, prefix))
            label = link.group(2)
            label_cursor = 0
            for code in re.finditer(GAME_TOKEN_SOURCE, label, re.I):
                output.append(label[label_cursor : code.start()])
                output.append(_anchor(anchors, code.group(0)))
                label_cursor = code.end()
            output.append(label[label_cursor:])
            output.append(_anchor(anchors, suffix))
        else:
            output.append(_anchor(anchors, token))
        cursor = match.end()
    output.append(value[cursor:])
    return "".join(output), tuple(anchors)


def _ie_fingerprint(text: str) -> str:
    _payload, anchors = _protect_ie(text)
    structure = (
        tuple(re.findall(r"\r\n|\r|\n", text)),
        tuple(anchor.source for anchor in anchors),
    )
    return hashlib.sha256(repr(structure).encode("utf-8")).hexdigest()


def _ie_validator(source: str, target: str) -> ValidationReport:
    source_fingerprint = _ie_fingerprint(source)
    target_fingerprint = _ie_fingerprint(target)
    if source_fingerprint != target_fingerprint:
        return ValidationReport(
            False,
            ("IE manual structure fingerprint changed",),
            source_fingerprint,
            target_fingerprint,
        )
    return ValidationReport(
        True,
        (),
        source_fingerprint,
        target_fingerprint,
    )


class ImmersiveEngineeringManualAdapter:
    adapter_id = "ie-manual-v1"

    def supports(self, logical_path: str, text: str) -> bool:
        del text
        normalized = logical_path.replace("\\", "/").lower()
        return bool(re.match(
            r"^assets/[a-z0-9_.-]+/manual/(?:_?[a-z]{2}_[a-z]{2}/)?.+\.txt$",
            normalized,
        ))

    def companion_prefixes_for(self, logical_path: str) -> tuple[str, ...]:
        normalized = logical_path.replace("\\", "/")
        match = re.match(r"(?i)^assets/([^/]+)/manual/", normalized)
        return (f"manual.{match.group(1)}.",) if match else ()

    def plan(
        self,
        logical_path: str,
        text: str,
        target_locale: str,
        *,
        target_path_hint: str | None = None,
    ) -> TranslationPlan:
        normalized = logical_path.replace("\\", "/")
        target_path = target_path_hint or re.sub(
            r"/en_us/",
            f"/{target_locale}/",
            normalized,
            count=1,
            flags=re.IGNORECASE,
        )
        units: list[TranslationUnit] = []
        for line in _lines(text):
            if not line.content.strip() or not re.search(
                r"[^\W\d_]",
                line.content,
                re.UNICODE,
            ):
                continue
            payload, anchors = _protect_ie(line.content)
            units.append(
                TranslationUnit(
                    id=f"{self.adapter_id}:{line.start}:{line.content_end}",
                    payload=payload,
                    start=line.start,
                    end=line.content_end,
                    context=payload,
                    anchors=anchors,
                    kind="manual-line",
                )
            )
        return TranslationPlan(
            adapter_id=self.adapter_id,
            logical_path=logical_path,
            source_text=text,
            target_path=target_path,
            units=tuple(units),
            validator=_ie_validator,
        )
