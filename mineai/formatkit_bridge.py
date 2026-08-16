from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Mapping

from mineai.text_processing import is_technical_term, looks_like_source_language
from mineai.formatkit_profile import MineAiLocaleMergePlanner, MineAiMinecraftLangJsonAdapter
from mineai_formatkit import (
    FORMATKIT_SOURCE_SHA,
    CollapsibleGroupsConfigLangJsonAdapter,
    JaopcaConfigLangJsonAdapter,
    LocaleMergePlan,
    TranslationUnit,
    ValidationError,
)


@dataclass(frozen=True)
class FormatKitLocaleWork:
    """One MineAI locale job planned by the pinned FormatKit SDK slice."""

    adapter_name: str
    planner: MineAiLocaleMergePlanner
    plan: LocaleMergePlan
    units_by_id: Mapping[str, TranslationUnit]
    pending: Mapping[str, str]
    passthrough: Mapping[str, str]
    total_translatable: int
    target_path: str
    target_parse_error: str | None
    target_text: str | None


_LOCALE_ADAPTERS = (
    CollapsibleGroupsConfigLangJsonAdapter(),
    JaopcaConfigLangJsonAdapter(),
    MineAiMinecraftLangJsonAdapter(),
)


def locale_adapter_for(path: str):
    """Return the first SDK locale adapter enabled by the MineAI pilot."""

    normalized = path.replace("\\", "/")
    for adapter in _LOCALE_ADAPTERS:
        if adapter.matches(normalized):
            return adapter
    return None


def modonomicon_locale_adapter():
    """Compatibility accessor for the books-only Modonomicon path.

    The current SDK profile's public Minecraft locale adapter is already
    Modonomicon-aware, so there is no longer a second patched adapter or
    archive-dependent behavior.
    """

    return _LOCALE_ADAPTERS[-1]


def is_formatkit_locale_path(path: str) -> bool:
    return locale_adapter_for(path) is not None


def target_path_for_locale(path: str, target_code: str) -> str | None:
    adapter = locale_adapter_for(path)
    if adapter is None:
        return None
    return adapter.target_path(path.replace("\\", "/"), target_code)


def plan_locale_work(
    path: str,
    source_text: str,
    target_code: str,
    target_text: str | None,
    mode: str,
    *,
    adapter=None,
    key_filter: Callable[[str], bool] | None = None,
) -> FormatKitLocaleWork | None:
    """Plan a locale while MineAI keeps product-level translation filtering.

    FormatKit owns parsing, protected syntax, target reuse and reconstruction.
    MineAI still decides which structurally-safe units are useful to translate.

    ``adapter`` remains only as a compatibility argument for the v3.3 books-only
    call site. The compatibility accessor now returns the same official SDK
    Minecraft locale adapter used by the normal path.
    """

    adapter = adapter or locale_adapter_for(path)
    if adapter is None:
        return None

    planner = MineAiLocaleMergePlanner(adapter=adapter)
    planner_mode = "append" if mode == "skip" else mode
    merge_plan = planner.plan(
        path.replace("\\", "/"),
        source_text,
        target_code,
        target_text=target_text,
        mode=planner_mode,
    )

    units = merge_plan.source_plan.by_id()
    originals = merge_plan.source_plan.metadata.get("original_values", {})
    eligible_ids: set[str] = set()
    for unit_id, unit in units.items():
        original = originals.get(unit_id, unit.text) if isinstance(originals, dict) else unit.text
        if not isinstance(original, str) or not original.strip():
            continue
        if not looks_like_source_language(original) or is_technical_term(original):
            continue
        if key_filter is not None and not key_filter(unit.context):
            continue
        eligible_ids.add(unit_id)

    pending_ids = set(merge_plan.pending_ids)
    pending = {
        unit_id: units[unit_id].text
        for unit_id in merge_plan.pending_ids
        if unit_id in eligible_ids
    }
    passthrough = {
        unit_id: units[unit_id].text
        for unit_id in merge_plan.pending_ids
        if unit_id not in eligible_ids
    }
    if set(pending) | set(passthrough) != pending_ids:
        raise ValueError("FormatKit locale bridge failed to classify pending units")

    return FormatKitLocaleWork(
        adapter_name=adapter.name,
        planner=planner,
        plan=merge_plan,
        units_by_id=units,
        pending=pending,
        passthrough=passthrough,
        total_translatable=len(eligible_ids),
        target_path=merge_plan.target_path,
        target_parse_error=merge_plan.target_parse_error,
        target_text=target_text,
    )


def validate_locale_candidate(
    work: FormatKitLocaleWork,
    unit_id: str,
    candidate: str,
) -> tuple[bool, str | None]:
    """Validate one candidate through the unmodified SDK adapter.

    Pilot v3.3 carried a custom validation helper inside the vendored SDK. v3.4
    removes that fork: MineAI asks the owning adapter to reconstruct one changed
    unit against the canonical source plan. Adapter validation remains the only
    authority and rejected candidates never reach cache/write.
    """

    if unit_id not in work.units_by_id:
        return False, f"FormatKit: unknown translation unit {unit_id}"
    try:
        work.planner.adapter.apply(work.plan.source_plan, {unit_id: candidate})
    except (ValidationError, ValueError) as exc:
        return False, f"FormatKit: {exc}"
    return True, None


def build_locale_output(
    work: FormatKitLocaleWork,
    translated: Mapping[str, str],
) -> str:
    """Build a complete validated locale, retaining source for rejected items.

    The SDK merge planner deliberately performs cheap target-reuse checks while
    planning and the owning adapter performs the full semantic validation when
    reconstructing.  A real Modonomicon RU locale can therefore contain one
    legacy value whose markup is no longer safe to reuse even though the rest of
    the target file is valid.  Never let that single reused value drop the whole
    generated locale: retry once with every existing target value converted to a
    public ``prepare()`` candidate.  Values that still violate the canonical
    source contract fall back to the source unit only; safe translated prose is
    retained and source-owned runtime syntax is restored by the SDK adapter.
    """

    values = dict(work.passthrough)
    for unit_id in work.pending:
        values[unit_id] = translated.get(unit_id, work.units_by_id[unit_id].text)
    try:
        return work.planner.build(work.plan, values)
    except (ValidationError, ValueError):
        if work.target_text is None or not work.plan.existing_values:
            raise

    adapter = work.planner.adapter
    try:
        target_plan = adapter.prepare(work.plan.source_plan.path, work.target_text)
        target_units = target_plan.by_id()
    except (ValidationError, ValueError):
        target_units = {}

    recovery_ids = set(work.plan.pending_ids) | set(work.plan.existing_values)
    recovery_values = dict(values)
    for unit_id in work.plan.existing_values:
        source_unit = work.units_by_id.get(unit_id)
        if source_unit is None:
            continue
        target_unit = target_units.get(unit_id)
        candidate = target_unit.text if target_unit is not None else source_unit.text
        try:
            adapter.apply(work.plan.source_plan, {unit_id: candidate})
        except (ValidationError, ValueError):
            candidate = source_unit.text
        recovery_values[unit_id] = candidate

    ordered_recovery_ids = tuple(
        unit.id for unit in work.plan.source_plan.units if unit.id in recovery_ids
    )
    recovery_plan = replace(
        work.plan,
        pending_ids=ordered_recovery_ids,
        existing_values={},
    )
    return work.planner.build(recovery_plan, recovery_values)


__all__ = [
    "FORMATKIT_SOURCE_SHA",
    "FormatKitLocaleWork",
    "build_locale_output",
    "is_formatkit_locale_path",
    "locale_adapter_for",
    "modonomicon_locale_adapter",
    "plan_locale_work",
    "validate_locale_candidate",
    "target_path_for_locale",
]
