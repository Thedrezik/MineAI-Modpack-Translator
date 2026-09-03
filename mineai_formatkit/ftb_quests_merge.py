from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import re
from typing import Mapping

from .core import ProtectedFragment, TranslationPlan, TranslationUnit, ValidationError
from . import ftb_quests as _ftb
from .ftb_quests import FtbQuestsLangAdapter


_SAFE_COMMAND_RE = re.compile(
    r"(?:(?<![A-Za-z0-9_:/])|(?<=&[0-9A-FK-ORZa-fk-orz]))"
    r"/[A-Za-z][A-Za-z0-9_.:-]*"
)


class _MergeFtbQuestsLangAdapter(FtbQuestsLangAdapter):
    """FTB locale adapter with corpus-proven slash-command boundaries.

    The base adapter historically treated the second half of ordinary prose like
    ``on/off`` or ``item/fluid`` as a slash command. Merge/retranslation needs
    those words visible to the translator while still protecting real commands
    such as ``/home`` and formatted ``&a/ftbteams``.
    """

    def _protect(self, text: str) -> tuple[str, tuple[ProtectedFragment, ...]]:
        spans: list[tuple[int, int]] = []
        literal_ids = [int(match.group(1)) for match in _ftb._PLACEHOLDER_RE.finditer(text)]
        spans.extend(
            (match.start(), match.end())
            for match in _ftb._PLACEHOLDER_RE.finditer(text)
        )
        for regex in (
            _ftb._FTB_FORMAT_RE,
            _ftb._ESCAPED_AMP_RE,
            _ftb._FTB_DIRECTIVE_RE,
            _ftb._URL_RE,
            _SAFE_COMMAND_RE,
            _ftb._ANGLE_PLACEHOLDER_RE,
            _ftb._KEY_CHORD_RE,
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
            out.append(placeholder)
            protected.append(ProtectedFragment(placeholder, text[start:end]))
            cursor = end
        out.append(text[cursor:])
        return "".join(out), tuple(protected)


@dataclass(frozen=True)
class FtbQuestsLocaleMergePlan:
    """Safe EN-canonical merge plan for an FTB Quests locale catalog."""

    source_plan: TranslationPlan
    target_path: str
    mode: str
    pending_ids: tuple[str, ...]
    existing_values: Mapping[str, str]
    missing_keys: tuple[str, ...]
    orphan_target_keys: tuple[str, ...]
    shape_mismatch_keys: tuple[str, ...]
    unit_layout_mismatch_keys: tuple[str, ...]
    invalid_existing_ids: tuple[str, ...]
    untranslated_ids: tuple[str, ...]


class FtbQuestsLocaleMergePlanner:
    """Plan append/force/skip work without trusting target SNBT structure.

    ``en_us.snbt`` is always the structural source of truth. Existing target text
    is used only as optional visible wording when it can be mapped onto the exact
    canonical English TranslationUnit layout and passes protected-fragment
    validation. Output is always reconstructed from the English source plan.
    """

    VALID_MODES = {"append", "force", "skip"}

    def __init__(self, adapter: FtbQuestsLangAdapter | None = None) -> None:
        self.adapter = adapter or _MergeFtbQuestsLangAdapter()

    def plan(
        self,
        source_path: str,
        source_text: str,
        target_code: str,
        target_text: str | None = None,
        mode: str = "append",
    ) -> FtbQuestsLocaleMergePlan:
        if mode not in self.VALID_MODES:
            raise ValueError(f"Unsupported FTB Quests locale merge mode: {mode}")

        source_plan = self.adapter.prepare(source_path, source_text)
        target_path = self.adapter.target_path(source_path, target_code)

        if mode == "force" or target_text is None:
            source_keys = tuple(entry.key for entry in self.adapter._parse_entries(source_text))
            return FtbQuestsLocaleMergePlan(
                source_plan=source_plan,
                target_path=target_path,
                mode=mode,
                pending_ids=tuple(unit.id for unit in source_plan.units),
                existing_values={},
                missing_keys=source_keys if target_text is None and mode != "force" else (),
                orphan_target_keys=(),
                shape_mismatch_keys=(),
                unit_layout_mismatch_keys=(),
                invalid_existing_ids=(),
                untranslated_ids=(),
            )

        source_entries = {entry.key: entry for entry in self.adapter._parse_entries(source_text)}
        target_entries = {entry.key: entry for entry in self.adapter._parse_entries(target_text)}
        target_plan = self.adapter.prepare(source_path, target_text)

        source_keys = set(source_entries)
        target_keys = set(target_entries)
        missing_keys = tuple(sorted(source_keys - target_keys))
        orphan_target_keys = tuple(sorted(target_keys - source_keys))
        shape_mismatch_keys = tuple(
            sorted(
                key
                for key in source_keys & target_keys
                if self._entry_shape(source_entries[key]) != self._entry_shape(target_entries[key])
            )
        )
        shape_mismatch_set = set(shape_mismatch_keys)

        source_by_key = self._units_by_key(source_plan)
        target_by_key = self._units_by_key(target_plan)
        unit_layout_mismatch_keys = tuple(
            sorted(
                key
                for key in source_keys & target_keys
                if key not in shape_mismatch_set
                and set(source_by_key.get(key, {})) != set(target_by_key.get(key, {}))
                and source_by_key.get(key)
            )
        )
        unit_layout_mismatch_set = set(unit_layout_mismatch_keys)

        existing_values: dict[str, str] = {}
        pending_ids: list[str] = []
        invalid_existing_ids: list[str] = []
        untranslated_ids: list[str] = []

        for unit in source_plan.units:
            key = self._unit_key(unit)
            if (
                key not in target_entries
                or key in shape_mismatch_set
                or key in unit_layout_mismatch_set
            ):
                pending_ids.append(unit.id)
                continue

            target_unit = target_by_key[key].get(unit.id)
            if target_unit is None:
                pending_ids.append(unit.id)
                continue

            if mode == "append" and target_unit.text == unit.text:
                untranslated_ids.append(unit.id)
                pending_ids.append(unit.id)
                continue

            try:
                # Validate the target's masked visible wording against canonical
                # English placeholders. Reconstruction later restores the source
                # technical fragments, never target technical structure.
                self.adapter._restore(unit, target_unit.text)
            except ValidationError:
                invalid_existing_ids.append(unit.id)
                pending_ids.append(unit.id)
                continue

            existing_values[unit.id] = target_unit.text

        return FtbQuestsLocaleMergePlan(
            source_plan=source_plan,
            target_path=target_path,
            mode=mode,
            pending_ids=tuple(pending_ids),
            existing_values=existing_values,
            missing_keys=missing_keys,
            orphan_target_keys=orphan_target_keys,
            shape_mismatch_keys=shape_mismatch_keys,
            unit_layout_mismatch_keys=unit_layout_mismatch_keys,
            invalid_existing_ids=tuple(invalid_existing_ids),
            untranslated_ids=tuple(untranslated_ids),
        )

    def build(
        self,
        plan: FtbQuestsLocaleMergePlan,
        translations: Mapping[str, str],
    ) -> str:
        required = set(plan.pending_ids)
        unknown = set(translations) - required
        if unknown:
            raise ValidationError(
                f"Translations contain non-pending FTB Quests ids: {sorted(unknown)!r}"
            )
        missing = required - set(translations)
        if missing:
            raise ValidationError(
                f"Missing translations for pending FTB Quests ids: {sorted(missing)!r}"
            )

        merged = dict(plan.existing_values)
        merged.update(translations)
        return self.adapter.apply(plan.source_plan, merged)

    @staticmethod
    def _entry_shape(entry: object) -> tuple[str, int]:
        kind = getattr(entry, "kind", None)
        values = getattr(entry, "values", None)
        if not isinstance(kind, str) or not isinstance(values, tuple):
            raise ValidationError("Invalid FTB Quests locale entry shape")
        return kind, len(values)

    @staticmethod
    def _unit_key(unit: TranslationUnit) -> str:
        return unit.context.split(";", 1)[0]

    def _units_by_key(self, plan: TranslationPlan) -> dict[str, dict[str, TranslationUnit]]:
        grouped: dict[str, dict[str, TranslationUnit]] = defaultdict(dict)
        for unit in plan.units:
            grouped[self._unit_key(unit)][unit.id] = unit
        return dict(grouped)
