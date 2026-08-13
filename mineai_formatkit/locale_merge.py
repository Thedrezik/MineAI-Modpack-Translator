from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Mapping

from .core import TranslationPlan, ValidationError
from .minecraft_lang import MinecraftLangJsonAdapter


_PRINTF_RE = re.compile(
    r"%(?!\s)(?:(?:\d+\$)?[-+#0 ,(<]*\d*(?:\.\d+)?"
    r"(?:[bBhHsScCdoxXeEfgGaA]|[tT][HIklMSLNpzZsQBbhAaCYyjmdeRTrDFc])|[%n])"
)
_MESSAGE_FORMAT_RE = re.compile(r"\{\d+(?:,[^{}]+)?\}")


@dataclass(frozen=True)
class LocaleMergePlan:
    source_plan: TranslationPlan
    target_path: str
    mode: str
    pending_ids: tuple[str, ...]
    existing_values: Mapping[str, str]
    missing_keys: tuple[str, ...]
    orphan_target_keys: tuple[str, ...]
    invalid_existing_keys: tuple[str, ...]
    target_parse_error: str | None = None


class LocaleMergePlanner:
    """Plan locale work with ``en_us`` as the canonical key/structure source.

    Existing target locale text is optional reusable content. Its key order,
    stale keys and formatting never define output structure.
    """

    VALID_MODES = {"append", "force", "skip"}

    def __init__(self, adapter: MinecraftLangJsonAdapter | None = None) -> None:
        self.adapter = adapter or MinecraftLangJsonAdapter()

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
        source_units = {u.context: u for u in source_plan.units}
        source_values_meta = source_plan.metadata.get("original_values")
        if not isinstance(source_values_meta, dict):
            raise ValidationError("Source locale plan is missing original values")
        source_values = {u.context: source_values_meta[u.id] for u in source_plan.units}
        target_values = self._catalog(target_text) if target_text is not None else {}

        source_keys = set(source_units)
        target_keys = set(target_values)
        missing = tuple(sorted(source_keys - target_keys))
        orphan = tuple(sorted(target_keys - source_keys))
        invalid: list[str] = []
        existing: dict[str, str] = {}
        pending: list[str] = []

        for key, unit in source_units.items():
            current = target_values.get(key)
            if mode == "force":
                pending.append(unit.id)
                continue
            if current is None or current == "":
                pending.append(unit.id)
                continue
            if not self._critical_placeholders_match(source_values[key], current):
                invalid.append(key)
                pending.append(unit.id)
                continue
            if mode == "append" and current == source_values[key]:
                pending.append(unit.id)
                continue
            existing[unit.id] = current

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

        replacements: list[tuple[int, int, str]] = []
        for unit in plan.source_plan.units:
            if unit.id in plan.existing_values:
                value = plan.existing_values[unit.id]
            elif unit.id in translations:
                value = self.adapter._restore_protected(unit, translations[unit.id])
            else:
                continue
            replacements.append((unit.start, unit.end, json.dumps(value, ensure_ascii=False)))

        output = plan.source_plan.source_text
        for start, end, token in sorted(replacements, reverse=True):
            output = output[:start] + token + output[end:]
        self.adapter.validate(plan.source_plan.source_text, output)
        return output

    def _catalog(self, text: str) -> dict[str, str]:
        entries = self.adapter._parse_entries(text)
        out: dict[str, str] = {}
        for entry in entries:
            if entry.is_string:
                assert isinstance(entry.value, str)
                out[entry.key] = entry.value
        return out

    @staticmethod
    def _critical_placeholders(text: str) -> Counter[str]:
        return Counter(_PRINTF_RE.findall(text) + _MESSAGE_FORMAT_RE.findall(text))

    def _critical_placeholders_match(self, source: str, target: str) -> bool:
        return self._critical_placeholders(source) == self._critical_placeholders(target)
