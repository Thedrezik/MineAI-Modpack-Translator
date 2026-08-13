from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Mapping

from .core import ProtectedFragment, TranslationPlan, TranslationUnit, ValidationError


_GUIDEME_LOCALE_SEGMENT_RE = re.compile(r"^_?[a-z]{2}_[a-z]{2}$", re.IGNORECASE)
_GUIDEME_ROOT_RE = re.compile(r"(^|/)(ae2guide/)", re.IGNORECASE)
_SOURCE_LOCALE_RE = re.compile(r"/en_us/", re.IGNORECASE)

_YAML_TITLE_RE = re.compile(r"^(\s*title\s*:\s*)([\"']?)(.*?)(\2)(\s*)$", re.IGNORECASE)
_BLOCK_PREFIX_RE = re.compile(
    r"^(?P<prefix>[ \t]*(?:>[ \t]*)*"
    r"(?:(?:#{1,6}[ \t]+)|"
    r"(?:(?:[-+*]|\d+[.)])[ \t]+(?:\[[ xX]\][ \t]+)?))?)"
    r"(?P<body>.*)$"
)
_TABLE_DELIMITER_CELL_RE = re.compile(r"^:?-{3,}:?$")
_COMPONENT_TAG_RE = re.compile(r"<[^>\n]+>")
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_IMAGE_RE = re.compile(r"!\[[^\]\n]*\]\([^\)\n]+\)")
_LINK_DEST_RE = re.compile(r"(?<=\])\(([^()\n]+)\)")
_FORMAT_RE = re.compile(
    r"%(?!\s)(?:(?:\d+\$)?[-+#0 ,(<]*\d*(?:\.\d+)?"
    r"(?:[bBhHsScCdoxXeEfgGaA]|[tT][HIklMSLNpzZsQBbhAaCYyjmdeRTrDFc])|[%n])"
)
_MINECRAFT_FORMAT_RE = re.compile(r"§[0-9A-FK-ORa-fk-or]")
_PLACEHOLDER_RE = re.compile(r"\[#(\d+)#\]")
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё]{2,}")


@dataclass(frozen=True)
class StructuralFingerprint:
    line_count: int
    newline_count: int
    yaml_delimiters: int
    component_tags: tuple[tuple[str, int], ...]
    link_destinations: tuple[tuple[str, int], ...]
    image_tokens: tuple[tuple[str, int], ...]
    inline_code_tokens: tuple[tuple[str, int], ...]


class GuideMeMarkdownAdapter:
    """Span-preserving Markdown/GuideME adapter.

    The adapter never reparses-and-renders the whole document. It records exact
    character spans for translatable payloads and patches only those spans.
    This makes whitespace, wrapping, GuideME components, YAML, links and asset
    references immutable unless they are explicitly part of a translation unit.
    """

    name = "guideme-markdown"

    def matches(self, path: str) -> bool:
        normalized = path.replace("\\", "/").lower()
        if not normalized.endswith((".md", ".txt")):
            return False
        marker = "ae2guide/"
        if marker not in normalized:
            return False
        tail = normalized.split(marker, 1)[1]
        first = tail.split("/", 1)[0]
        return not _GUIDEME_LOCALE_SEGMENT_RE.fullmatch(first)

    def target_path(self, path: str, target_code: str) -> str:
        slash = path.replace("\\", "/")
        if _SOURCE_LOCALE_RE.search(slash):
            return _SOURCE_LOCALE_RE.sub(f"/{target_code}/", slash, count=1)
        if _GUIDEME_ROOT_RE.search(slash):
            return _GUIDEME_ROOT_RE.sub(
                lambda match: f"{match.group(1)}{match.group(2)}_{target_code}/",
                slash,
                count=1,
            )
        raise ValueError(f"Unsupported GuideME source path: {path}")

    def prepare(self, path: str, source_text: str) -> TranslationPlan:
        units: list[TranslationUnit] = []
        in_yaml = False
        in_fence = False
        line_no = 0
        offset = 0

        for raw_line in source_text.splitlines(keepends=True):
            line_no += 1
            content = raw_line.rstrip("\r\n")
            stripped = content.strip()

            # GuideME front matter is YAML only when the document starts with
            # `---`. Later horizontal rules must never reopen YAML mode.
            if line_no == 1 and stripped == "---":
                in_yaml = True
                offset += len(raw_line)
                continue
            if in_yaml and stripped == "---":
                in_yaml = False
                offset += len(raw_line)
                continue

            if stripped.startswith("```") and not in_yaml:
                in_fence = not in_fence
                offset += len(raw_line)
                continue

            if in_fence:
                offset += len(raw_line)
                continue

            if in_yaml:
                yaml_unit = self._yaml_title_unit(path, content, offset, line_no)
                if yaml_unit:
                    units.append(yaml_unit)
                offset += len(raw_line)
                continue

            if self._is_table_row(content):
                units.extend(self._table_units(path, content, offset, line_no))
                offset += len(raw_line)
                continue

            unit = self._line_unit(path, content, offset, line_no)
            if unit:
                units.append(unit)
            offset += len(raw_line)

        return TranslationPlan(
            path=path,
            source_text=source_text,
            units=tuple(units),
            metadata={"fingerprint": self.fingerprint(source_text)},
        )

    def apply(self, plan: TranslationPlan, translations: Mapping[str, str]) -> str:
        unknown = set(translations) - {unit.id for unit in plan.units}
        if unknown:
            raise ValidationError(f"Unknown translation unit ids: {sorted(unknown)!r}")

        replacements: list[tuple[int, int, str]] = []
        for unit in plan.units:
            if unit.id not in translations:
                continue
            translated = translations[unit.id]
            if "\n" in translated or "\r" in translated:
                raise ValidationError(f"Unit {unit.id} introduced a newline")
            if unit.kind == "table-cell" and "|" in translated:
                raise ValidationError(f"Unit {unit.id} introduced a table delimiter")
            restored = self._restore_protected(unit, translated)
            replacements.append((unit.start, unit.end, restored))

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
                "GuideME structural fingerprint changed during reconstruction"
            )

    def fingerprint(self, text: str) -> StructuralFingerprint:
        return StructuralFingerprint(
            line_count=len(text.splitlines()),
            newline_count=text.count("\n"),
            yaml_delimiters=sum(
                1 for line in text.splitlines() if line.strip() == "---"
            ),
            component_tags=tuple(
                sorted(Counter(_COMPONENT_TAG_RE.findall(text)).items())
            ),
            link_destinations=tuple(
                sorted(Counter(self._link_destinations(text)).items())
            ),
            image_tokens=tuple(sorted(Counter(_IMAGE_RE.findall(text)).items())),
            inline_code_tokens=tuple(
                sorted(Counter(_INLINE_CODE_RE.findall(text)).items())
            ),
        )

    def _yaml_title_unit(
        self, path: str, line: str, offset: int, line_no: int
    ) -> TranslationUnit | None:
        match = _YAML_TITLE_RE.match(line)
        if not match:
            return None
        title = match.group(3)
        if not self._has_prose(title):
            return None
        start = offset + match.start(3)
        end = offset + match.end(3)
        masked, protected = self._protect(title)
        return TranslationUnit(
            id=f"line:{line_no}:yaml-title",
            text=masked,
            start=start,
            end=end,
            kind="yaml-title",
            context=path,
            protected=protected,
        )

    def _line_unit(
        self, path: str, line: str, offset: int, line_no: int
    ) -> TranslationUnit | None:
        if not line.strip():
            return None
        if self._is_technical_only(line):
            return None

        match = _BLOCK_PREFIX_RE.match(line)
        assert match is not None
        body = match.group("body")
        body_start = match.start("body")

        # Keep trailing whitespace outside the translation span, including the
        # two spaces used by Markdown hard breaks.
        stripped_body = body.rstrip(" \t")
        if not stripped_body or not self._has_prose(stripped_body):
            return None

        leading = len(stripped_body) - len(stripped_body.lstrip(" \t"))
        payload = stripped_body[leading:]
        start = offset + body_start + leading
        end = start + len(payload)
        masked, protected = self._protect(payload)
        if not self._has_prose(self._visible_text(masked)):
            return None
        return TranslationUnit(
            id=f"line:{line_no}",
            text=masked,
            start=start,
            end=end,
            kind="markdown-line",
            context=path,
            protected=protected,
        )

    def _table_units(
        self, path: str, line: str, offset: int, line_no: int
    ) -> list[TranslationUnit]:
        # Find real pipes while respecting backslash escapes. Unknown nested
        # pipe syntax stays untranslated because only source spans are patched.
        pipes = [
            index
            for index, char in enumerate(line)
            if char == "|" and (index == 0 or line[index - 1] != "\\")
        ]
        if len(pipes) < 2:
            return []

        out: list[TranslationUnit] = []
        for cell_idx, (left, right) in enumerate(zip(pipes, pipes[1:])):
            raw = line[left + 1 : right]
            trimmed = raw.strip(" \t")
            if not trimmed or _TABLE_DELIMITER_CELL_RE.fullmatch(trimmed):
                continue
            masked, protected = self._protect(trimmed)
            if not self._has_prose(self._visible_text(masked)):
                continue
            lead = len(raw) - len(raw.lstrip(" \t"))
            start = offset + left + 1 + lead
            end = start + len(trimmed)
            out.append(
                TranslationUnit(
                    id=f"line:{line_no}:cell:{cell_idx}",
                    text=masked,
                    start=start,
                    end=end,
                    kind="table-cell",
                    context=path,
                    protected=protected,
                )
            )
        return out

    @staticmethod
    def _is_table_row(line: str) -> bool:
        stripped = line.strip()
        return (
            stripped.startswith("|")
            and stripped.endswith("|")
            and stripped.count("|") >= 2
        )

    def _is_technical_only(self, text: str) -> bool:
        visible = self._visible_text(self._protect(text.strip())[0]).strip()
        return not self._has_prose(visible)

    @staticmethod
    def _has_prose(text: str) -> bool:
        return bool(_WORD_RE.search(text))

    @staticmethod
    def _visible_text(masked: str) -> str:
        return _PLACEHOLDER_RE.sub(" ", masked)

    def _protect(self, text: str) -> tuple[str, tuple[ProtectedFragment, ...]]:
        spans: list[tuple[int, int]] = []
        literal_ids = [int(match.group(1)) for match in _PLACEHOLDER_RE.finditer(text)]
        spans.extend(
            (match.start(), match.end()) for match in _PLACEHOLDER_RE.finditer(text)
        )
        for regex in (
            _INLINE_CODE_RE,
            _IMAGE_RE,
            _COMPONENT_TAG_RE,
            _FORMAT_RE,
            _MINECRAFT_FORMAT_RE,
        ):
            spans.extend((match.start(), match.end()) for match in regex.finditer(text))

        # Protect Markdown destinations but leave labels visible/translatable.
        for match in _LINK_DEST_RE.finditer(text):
            spans.append((match.start(1), match.end(1)))

        spans = self._merge_spans(spans)
        protected: list[ProtectedFragment] = []
        out: list[str] = []
        cursor = 0
        base_id = (max(literal_ids) + 1) if literal_ids else 0
        for index, (start, end) in enumerate(spans):
            if start < cursor:
                continue
            out.append(text[cursor:start])
            placeholder = f"[#{base_id + index}#]"
            out.append(placeholder)
            protected.append(ProtectedFragment(placeholder, text[start:end]))
            cursor = end
        out.append(text[cursor:])
        return "".join(out), tuple(protected)

    @staticmethod
    def _merge_spans(spans: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
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
        actual = Counter(
            f"[#{value}#]" for value in _PLACEHOLDER_RE.findall(translated)
        )
        if actual != expected:
            raise ValidationError(
                f"Unit {unit.id} changed protected placeholders: "
                f"expected {expected}, got {actual}"
            )
        restored = translated
        for fragment in unit.protected:
            restored = restored.replace(fragment.placeholder, fragment.value)
        return restored

    @staticmethod
    def _link_destinations(text: str) -> list[str]:
        destinations: list[str] = []
        for match in re.finditer(r"(?<!!)\[[^\]\n]*\]\(([^)\n]+)\)", text):
            destinations.append(match.group(1))
        return destinations
