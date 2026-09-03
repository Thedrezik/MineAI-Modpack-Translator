from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

from .core import ProtectedFragment, TranslationPlan, TranslationUnit, ValidationError


_MANUAL_PATH_RE = re.compile(
    r"(^|/)assets/[^/]+/manual/en_us/.+\.txt$",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"<[^>\r\n]+>")
_SECTION_FORMAT_RE = re.compile(r"§[0-9A-FK-ORa-fk-or]")
_SECTION_RESET_BOUNDARY_RE = re.compile(
    r"§r(?:[^\w\r\n<§]*[ \t]+|[^\w\s\r\n<§]+)",
    re.IGNORECASE,
)
_PLACEHOLDER_RE = re.compile(r"\[#(\d+)#\]")
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё]{2,}")


@dataclass(frozen=True)
class IeManualFingerprint:
    line_count: int
    newline_count: int
    tokens: tuple[str, ...]


class ImmersiveEngineeringManualAdapter:
    """Span-preserving adapter for Immersive Engineering manual ``.txt`` files.

    It translates prose, link labels, and the documented display branches of
    boolean ``<config;...>`` tokens. Targets, anchors, config keys, keybinds,
    page/line controls and every other manual directive stay immutable.
    """

    name = "immersive-engineering-manual"

    def matches(self, path: str) -> bool:
        slash = "/" + path.replace("\\", "/").lstrip("/")
        return bool(_MANUAL_PATH_RE.search(slash))

    def target_path(self, path: str, target_code: str) -> str:
        slash = path.replace("\\", "/")
        if not self.matches(slash):
            raise ValueError(f"Unsupported Immersive Engineering manual source path: {path}")
        return re.sub(
            r"/manual/en_us/",
            f"/manual/{target_code}/",
            slash,
            count=1,
            flags=re.IGNORECASE,
        )

    def prepare(self, path: str, source_text: str) -> TranslationPlan:
        spans: list[TranslationUnit] = []
        cursor = 0
        token_index = 0
        for token_match in _TOKEN_RE.finditer(source_text):
            self._add_plain_units(path, source_text, cursor, token_match.start(), spans)
            self._add_token_units(path, source_text, token_match, token_index, spans)
            cursor = token_match.end()
            token_index += 1
        self._add_plain_units(path, source_text, cursor, len(source_text), spans)
        spans.sort(key=lambda unit: unit.start)
        return TranslationPlan(
            path=path,
            source_text=source_text,
            units=tuple(spans),
            metadata={"fingerprint": self.fingerprint(source_text)},
        )

    def apply(self, plan: TranslationPlan, translations: Mapping[str, str]) -> str:
        known = {unit.id for unit in plan.units}
        unknown = set(translations) - known
        if unknown:
            raise ValidationError(f"Unknown translation unit ids: {sorted(unknown)!r}")

        replacements: list[tuple[int, int, str]] = []
        for unit in plan.units:
            if unit.id not in translations:
                continue
            translated = translations[unit.id]
            if "\n" in translated or "\r" in translated:
                raise ValidationError(f"Unit {unit.id} introduced a newline")
            if unit.kind in {"ie-link-label", "ie-config-label"} and any(
                delimiter in translated for delimiter in (";", "<", ">")
            ):
                raise ValidationError(f"Unit {unit.id} changed manual-token delimiters")
            restored = self._restore_protected(unit, translated)
            replacements.append((unit.start, unit.end, restored))

        output = plan.source_text
        for start, end, replacement in sorted(replacements, reverse=True):
            output = output[:start] + replacement + output[end:]
        self.validate(plan.source_text, output)
        return output

    def validate(self, source_text: str, output_text: str) -> None:
        if self.fingerprint(source_text) != self.fingerprint(output_text):
            raise ValidationError("Immersive Engineering manual structure changed")

    def fingerprint(self, text: str) -> IeManualFingerprint:
        normalized: list[str] = []
        for match in _TOKEN_RE.finditer(text):
            normalized.append(self._normalize_token(match.group(0)))
        return IeManualFingerprint(
            line_count=len(text.splitlines()),
            newline_count=text.count("\n"),
            tokens=tuple(normalized),
        )

    def _add_plain_units(
        self,
        path: str,
        text: str,
        start: int,
        end: int,
        out: list[TranslationUnit],
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
            masked, protected = self._protect_formatting(payload)
            if not self._has_prose(_PLACEHOLDER_RE.sub(" ", masked)):
                continue
            out.append(
                TranslationUnit(
                    id=f"span:{unit_start}:prose",
                    text=masked,
                    start=unit_start,
                    end=unit_end,
                    kind="ie-manual-prose",
                    context=path,
                    protected=protected,
                )
            )

    def _add_token_units(
        self,
        path: str,
        text: str,
        match: re.Match[str],
        token_index: int,
        out: list[TranslationUnit],
    ) -> None:
        token = match.group(0)
        body = token[1:-1]
        parts = body.split(";")
        if not parts:
            return
        token_type = parts[0].lower()
        indices: tuple[int, ...] = ()
        kind = ""
        if token_type == "link" and len(parts) in (3, 4):
            indices = (2,)
            kind = "ie-link-label"
        elif token_type == "config" and len(parts) in (4, 5) and parts[1].lower() == "b":
            indices = tuple(range(3, len(parts)))
            kind = "ie-config-label"
        else:
            return

        # Exact field spans inside the original token. ``split(';')`` is valid
        # because the official parser uses semicolon-delimited fields too.
        offsets: list[tuple[int, int]] = []
        field_start = 1  # after '<'
        for part in parts:
            field_end = field_start + len(part)
            offsets.append((field_start, field_end))
            field_start = field_end + 1

        for field_index in indices:
            value = parts[field_index]
            if not value or not self._has_prose(value):
                continue
            local_start, local_end = offsets[field_index]
            unit_start = match.start() + local_start
            unit_end = match.start() + local_end
            masked, protected = self._protect_formatting(value)
            out.append(
                TranslationUnit(
                    id=f"token:{token_index}:field:{field_index}",
                    text=masked,
                    start=unit_start,
                    end=unit_end,
                    kind=kind,
                    context=path,
                    protected=protected,
                )
            )

    @staticmethod
    def _normalize_token(token: str) -> str:
        body = token[1:-1]
        parts = body.split(";")
        if parts and parts[0].lower() == "link" and len(parts) in (3, 4):
            parts[2] = "<mineai-text>"
            return "<" + ";".join(parts) + ">"
        if (
            parts
            and parts[0].lower() == "config"
            and len(parts) in (4, 5)
            and parts[1].lower() == "b"
        ):
            for index in range(3, len(parts)):
                parts[index] = "<mineai-text>"
            return "<" + ";".join(parts) + ">"
        return token

    @staticmethod
    def _has_prose(text: str) -> bool:
        return bool(_WORD_RE.search(text))

    def _protect_formatting(self, text: str) -> tuple[str, tuple[ProtectedFragment, ...]]:
        spans = [
            (match.start(), match.end())
            for match in _SECTION_RESET_BOUNDARY_RE.finditer(text)
        ]
        spans.extend(
            (match.start(), match.end()) for match in _SECTION_FORMAT_RE.finditer(text)
        )
        literal_ids = [int(match.group(1)) for match in _PLACEHOLDER_RE.finditer(text)]
        spans.extend((match.start(), match.end()) for match in _PLACEHOLDER_RE.finditer(text))
        merged: list[list[int]] = []
        for start, end in sorted(spans):
            if not merged or start >= merged[-1][1]:
                merged.append([start, end])
            elif end > merged[-1][1]:
                merged[-1][1] = end
        base = max(literal_ids) + 1 if literal_ids else 0
        cursor = 0
        masked: list[str] = []
        protected: list[ProtectedFragment] = []
        for index, (start, end) in enumerate(merged):
            masked.append(text[cursor:start])
            placeholder = f"[#{base + index}#]"
            masked.append(placeholder)
            protected.append(ProtectedFragment(placeholder, text[start:end]))
            cursor = end
        masked.append(text[cursor:])
        return "".join(masked), tuple(protected)

    @staticmethod
    def _restore_protected(unit: TranslationUnit, translated: str) -> str:
        expected = [fragment.placeholder for fragment in unit.protected]
        actual = [f"[#{value}#]" for value in _PLACEHOLDER_RE.findall(translated)]
        if actual != expected:
            raise ValidationError(f"Unit {unit.id} changed protected placeholder order")
        restored = translated
        for fragment in unit.protected:
            restored = restored.replace(fragment.placeholder, fragment.value)
        return restored
