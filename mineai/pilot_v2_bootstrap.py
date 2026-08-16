"""Install pilot-v2 host safety without changing the certified FormatKit snapshot."""

from __future__ import annotations

from dataclasses import replace
import re

from mineai import formatkit_bridge as _bridge
from mineai.text_processing import (
    PLACEHOLDER_PATTERN,
    is_technical_term,
    looks_like_source_language,
)


_PILOT_KEY_LABELS = frozenset(
    {"[shift]", "[ctrl]", "[alt]", "[tab]", "[enter]", "[esc]", "[escape]"}
)
_COORDINATE_PAIR_RE = re.compile(
    r"^[+-]?\d+(?:\.\d+)?\s*[NS]\s+[+-]?\d+(?:\.\d+)?\s*[EW]$",
    re.IGNORECASE,
)
_UUID_VALUE_RE = re.compile(
    r"^UUID\s*:\s*(?:\[\s*#\s*\d+\s*#\s*\]\s*)+$",
    re.IGNORECASE,
)
_PURE_PLACEHOLDER_FORMAT_RE = re.compile(r"^[\s+\-*/=():.,%$]*$")
_ORIGINAL_PLAN_LOCALE_WORK = _bridge.plan_locale_work
_INSTALLED = False


def _is_obvious_runtime_technical(text: str, masked_text: str) -> bool:
    stripped = text.strip()
    masked = masked_text.strip()
    if stripped in {"true", "false"}:
        return True
    if stripped.casefold() in _PILOT_KEY_LABELS:
        return True
    if _COORDINATE_PAIR_RE.fullmatch(stripped):
        return True
    if _UUID_VALUE_RE.fullmatch(masked):
        return True
    if PLACEHOLDER_PATTERN.search(masked):
        visible = PLACEHOLDER_PATTERN.sub("", masked)
        if _PURE_PLACEHOLDER_FORMAT_RE.fullmatch(visible):
            return True
    return False


def _plan_locale_work_v2(*args, **kwargs):
    work = _ORIGINAL_PLAN_LOCALE_WORK(*args, **kwargs)
    if work is None:
        return None

    units = work.plan.source_plan.by_id()
    originals = work.plan.source_plan.metadata.get("original_values", {})
    filtered_ids: set[str] = set()
    for unit_id, unit in units.items():
        original = originals.get(unit_id, unit.text) if isinstance(originals, dict) else unit.text
        if not isinstance(original, str) or not original.strip():
            continue
        was_eligible = looks_like_source_language(original) and not is_technical_term(original)
        if was_eligible and _is_obvious_runtime_technical(original, unit.text):
            filtered_ids.add(unit_id)

    if not filtered_ids:
        return work

    pending = dict(work.pending)
    passthrough = dict(work.passthrough)
    for unit_id in filtered_ids:
        if unit_id in pending:
            pending.pop(unit_id)
            passthrough[unit_id] = units[unit_id].text

    return replace(
        work,
        pending=pending,
        passthrough=passthrough,
        total_translatable=max(0, work.total_translatable - len(filtered_ids)),
    )


def install_pilot_v2() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _bridge.plan_locale_work = _plan_locale_work_v2
    _INSTALLED = True


__all__ = ["install_pilot_v2"]
