from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import replace

from .core import ProtectedFragment, TranslationPlan, ValidationError
from .locale_merge import LocaleMergePlan
from .minecraft_lang import _IGNORED_METADATA_KEYS, _JsonEntry, _PLACEHOLDER_RE
from .structured_locale import (
    LocaleMergePlanner as _StructuredLocaleMergePlanner,
    MinecraftLangJsonAdapter as _StructuredMinecraftLangJsonAdapter,
)

# Proven by the FTB Evolution KubeJS locale corpus. Keep these deliberately
# narrower than a generic ampersand/brace/private-character matcher.
_FTB_FORMAT_RE = re.compile(r"&[0-9a-fk-orz]")
_FTB_IMAGE_RE = re.compile(r"\{image:[^{}\r\n]+\}")
_PRIVATE_USE_RE = re.compile(r"[\ue000-\uf8ff]")
_FTB_FORMAT_COUNTER_TOKEN = "<mineai-ftb-format-code>"

# Malum's codex renderer consumes these markers from ordinary lang values.
# Inline $i/$b pairs wrap human text; $m<number>/$ is a complete scale token.
# The patterns intentionally require a balanced inline close or a numeric scale
# so ordinary dollar prose is not generalized into markup.
_MALUM_INLINE_RE = re.compile(r"\$(?P<code>[ib])(?P<body>[^$\r\n]*?)/\$")
_MALUM_SCALE_RE = re.compile(r"\$m\d+(?:\.\d+)?/\$")


class MinecraftLangJsonAdapter(_StructuredMinecraftLangJsonAdapter):
    """Public locale adapter with live-modpack safety hardening.

    In addition to the reviewed runtime/structured locale layers, this accepts
    repeated ordinary string keys only when every repeated value is identical.
    One logical TranslationUnit is exposed and a changed translation is written
    back to every identical occurrence. Conflicting duplicates still fail
    closed.

    The adapter also protects corpus-proven FTB ampersand formatting, FTB image
    directives, BMP Private Use Area glyphs used by custom-font resources, and
    balanced Malum codex markup embedded in ordinary locale values.
    """

    name = "minecraft-lang-json"

    def _parse_entries(self, text: str) -> tuple[_JsonEntry, ...]:
        decoder = json.JSONDecoder()
        length = len(text)
        index = self._skip_ws(text, 0)
        if index >= length or text[index] != "{":
            raise ValidationError("Minecraft lang JSON must be a top-level object")
        index += 1

        entries: list[_JsonEntry] = []
        first_by_key: dict[str, _JsonEntry] = {}

        while True:
            index = self._skip_ws(text, index)
            if index >= length:
                raise ValidationError("Unexpected end of Minecraft lang JSON")
            if text[index] == "}":
                index += 1
                break
            if text[index] != '"':
                raise ValidationError("Minecraft lang JSON keys must be strings")

            key_end = self._scan_string_end(text, index)
            try:
                key = json.loads(text[index:key_end])
            except json.JSONDecodeError as exc:
                raise ValidationError("Invalid JSON key string") from exc
            index = self._skip_ws(text, key_end)
            if index >= length or text[index] != ":":
                raise ValidationError(f"Missing ':' after Minecraft lang key {key!r}")
            index = self._skip_ws(text, index + 1)
            if index >= length:
                raise ValidationError(f"Missing value for Minecraft lang key {key!r}")

            value_start = index
            if text[index] == '"':
                value_end = self._scan_string_end(text, index)
                try:
                    value = json.loads(text[value_start:value_end])
                except json.JSONDecodeError as exc:
                    raise ValidationError(f"Invalid JSON string for key {key!r}") from exc
                is_string = True
            else:
                try:
                    value, value_end = decoder.raw_decode(text, index)
                except json.JSONDecodeError as exc:
                    raise ValidationError(f"Invalid JSON value for key {key!r}") from exc
                is_string = False

            entry = _JsonEntry(
                key=key,
                value=value,
                value_start=value_start,
                value_end=value_end,
                is_string=is_string,
            )
            previous = first_by_key.get(key)
            if previous is not None and key not in _IGNORED_METADATA_KEYS:
                if not (
                    previous.is_string
                    and entry.is_string
                    and previous.value == entry.value
                ):
                    # Keep the historical failure wording for callers/tests.
                    raise ValidationError(f"Duplicate Minecraft lang key: {key}")
            else:
                first_by_key.setdefault(key, entry)
            entries.append(entry)

            index = self._skip_ws(text, value_end)
            if index >= length:
                raise ValidationError("Unexpected end of Minecraft lang JSON")
            if text[index] == ",":
                index += 1
                continue
            if text[index] == "}":
                index += 1
                break
            raise ValidationError(f"Expected ',' or '}}' after Minecraft lang key {key!r}")

        if self._skip_ws(text, index) != length:
            raise ValidationError("Trailing data after Minecraft lang JSON object")
        return tuple(entries)

    def prepare(self, path: str, source_text: str) -> TranslationPlan:
        raw_plan = super().prepare(path, source_text)
        unique_units = []
        first_units = {}
        alias_spans: dict[str, list[tuple[int, int]]] = {}

        for unit in raw_plan.units:
            previous = first_units.get(unit.id)
            if previous is None:
                first_units[unit.id] = unit
                unique_units.append(unit)
                continue
            if (
                previous.text != unit.text
                or previous.kind != unit.kind
                or previous.context != unit.context
                or previous.protected != unit.protected
            ):
                raise ValidationError(
                    f"Duplicate locale unit {unit.id!r} is not semantically identical"
                )
            alias_spans.setdefault(unit.id, []).append((unit.start, unit.end))

        metadata = dict(raw_plan.metadata)
        metadata["duplicate_alias_spans"] = {
            unit_id: tuple(spans) for unit_id, spans in alias_spans.items()
        }
        return TranslationPlan(
            path=raw_plan.path,
            source_text=raw_plan.source_text,
            units=tuple(unique_units),
            metadata=metadata,
        )

    def apply(self, plan: TranslationPlan, translations) -> str:
        return super().apply(self._expand_alias_units(plan), translations)

    def _expand_alias_units(self, plan: TranslationPlan) -> TranslationPlan:
        alias_spans = plan.metadata.get("duplicate_alias_spans", {})
        if not isinstance(alias_spans, dict) or not alias_spans:
            return plan

        expanded = []
        for unit in plan.units:
            expanded.append(unit)
            spans = alias_spans.get(unit.id, ())
            if not isinstance(spans, (tuple, list)):
                raise ValidationError("Invalid duplicate locale alias metadata")
            for span in spans:
                if not (
                    isinstance(span, (tuple, list))
                    and len(span) == 2
                    and all(isinstance(value, int) for value in span)
                ):
                    raise ValidationError("Invalid duplicate locale alias span")
                expanded.append(replace(unit, start=span[0], end=span[1]))
        return replace(plan, units=tuple(expanded))

    def _protect(self, text: str) -> tuple[str, tuple[ProtectedFragment, ...]]:
        masked, protected = super()._protect(text)
        spans: list[tuple[int, int]] = []
        for regex in (_FTB_FORMAT_RE, _FTB_IMAGE_RE, _PRIVATE_USE_RE):
            for match in regex.finditer(masked):
                # Never swallow a placeholder created by an earlier safety
                # layer into a new fragment; restoration validates every
                # placeholder independently.
                if _PLACEHOLDER_RE.search(masked[match.start() : match.end()]):
                    continue
                spans.append((match.start(), match.end()))

        # Preserve Malum's runtime markup but leave the wrapped human prose
        # visible to the translator.
        for match in _MALUM_INLINE_RE.finditer(masked):
            spans.append((match.start(), match.start() + 2))
            spans.append((match.end() - 2, match.end()))
        spans.extend((match.start(), match.end()) for match in _MALUM_SCALE_RE.finditer(masked))

        merged = self._merge_spans(spans)
        if not merged:
            return masked, protected

        placeholder_ids = [int(match.group(1)) for match in _PLACEHOLDER_RE.finditer(masked)]
        next_id = max(placeholder_ids) + 1 if placeholder_ids else 0
        out: list[str] = []
        extra: list[ProtectedFragment] = []
        cursor = 0
        for offset, (start, end) in enumerate(merged):
            out.append(masked[cursor:start])
            placeholder = f"[#{next_id + offset}#]"
            out.append(placeholder)
            extra.append(ProtectedFragment(placeholder, masked[start:end]))
            cursor = end
        out.append(masked[cursor:])
        return "".join(out), protected + tuple(extra)


class LocaleMergePlanner(_StructuredLocaleMergePlanner):
    """Structured locale planner aligned with the live-pack safety contract."""

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
        # Force mode is source-only by definition. Never let an optional broken
        # target block a complete rebuild from canonical English.
        if mode == "force":
            return super().plan(
                source_path,
                source_text,
                target_code,
                target_text=None,
                mode=mode,
            )

        if target_text is not None:
            try:
                self._catalog_extended(target_text)
            except ValidationError as exc:
                # Existing target wording is optional. If it is malformed,
                # discard it as a reuse source and keep planning from EN. The
                # error is retained for host diagnostics.
                fallback = super().plan(
                    source_path,
                    source_text,
                    target_code,
                    target_text=None,
                    mode=mode,
                )
                return replace(fallback, target_parse_error=str(exc))

        return super().plan(
            source_path,
            source_text,
            target_code,
            target_text=target_text,
            mode=mode,
        )

    @staticmethod
    def _critical_placeholders(text: str) -> Counter[str]:
        critical = _StructuredLocaleMergePlanner._critical_placeholders(text)
        # A target may intentionally choose another FTB colour/style code, but
        # losing or adding a formatting boundary is still suspicious. Compare
        # only the count, not the exact code values.
        format_count = len(_FTB_FORMAT_RE.findall(text))
        if format_count:
            critical[_FTB_FORMAT_COUNTER_TOKEN] += format_count
        critical.update(_FTB_IMAGE_RE.findall(text))
        critical.update(_PRIVATE_USE_RE.findall(text))

        # Malum inline markers are structural styling, while scale tokens also
        # carry a numeric rendering value. Compare both exactly when present.
        for match in _MALUM_INLINE_RE.finditer(text):
            critical[f"<mineai-malum-{match.group('code')}-pair>"] += 1
        critical.update(_MALUM_SCALE_RE.findall(text))
        return critical

    def build(self, plan: LocaleMergePlan, translations) -> str:
        expanded_source = self.adapter._expand_alias_units(plan.source_plan)
        if expanded_source is plan.source_plan:
            return super().build(plan, translations)
        return super().build(replace(plan, source_plan=expanded_source), translations)
