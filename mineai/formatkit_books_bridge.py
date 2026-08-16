from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping

from mineai.text_processing import already_translated, is_technical_term, looks_like_source_language
from mineai.formatkit_profile import (
    MineAiModonomiconBookJsonAdapter,
    MineAiPatchouliBookJsonAdapter,
)
from mineai_formatkit import (
    FORMATKIT_PILOT_HARDENING,
    FORMATKIT_SOURCE_SHA,
    ImmersiveEngineeringManualAdapter,
    TranslationPlan,
    TranslationUnit,
    ValidationError,
)


_PLACEHOLDER_RE = re.compile(r"\[#\d+#\]")
_WORD_RE = re.compile(r"[^\W_]+(?:[’'][^\W_]+)?", re.UNICODE)


def _semantic_anchor_rows(plan: TranslationPlan, parent_id: str):
    anchors = plan.metadata.get("semantic_anchors")
    if not isinstance(anchors, dict):
        return ()
    rows = anchors.get(parent_id, ())
    return rows if isinstance(rows, tuple) else tuple(rows) if isinstance(rows, list) else ()


def _semantic_child_ids(plan: TranslationPlan) -> set[str]:
    anchors = plan.metadata.get("semantic_anchors")
    if not isinstance(anchors, dict):
        return set()
    out: set[str] = set()
    for rows in anchors.values():
        if not isinstance(rows, (tuple, list)):
            continue
        for anchor in rows:
            child_id = getattr(anchor, "child_id", None)
            if isinstance(child_id, str):
                out.add(child_id)
    return out


def split_semantic_book_pending(
    work: "FormatKitBookWork",
) -> tuple[dict[str, str], dict[str, str]]:
    """Translate semantic children before parents that depend on them.

    Non-semantic documents keep the original single-stage behavior. The split is
    host scheduling only; FormatKit remains the parser/reconstruction authority.
    """

    child_ids = _semantic_child_ids(work.source_plan)
    if not child_ids:
        return {}, dict(work.pending)
    children = {key: value for key, value in work.pending.items() if key in child_ids}
    remaining = {key: value for key, value in work.pending.items() if key not in child_ids}
    return children, remaining


def semantic_resolved_values(
    work: "FormatKitBookWork", translated: Mapping[str, str]
) -> dict[str, str]:
    """Return the effective child payloads available to parent validation."""

    out: dict[str, str] = {}
    for child_id in _semantic_child_ids(work.source_plan):
        unit = work.units_by_id.get(child_id)
        if unit is None:
            continue
        out[child_id] = translated.get(
            child_id,
            work.preserved.get(child_id, work.passthrough.get(child_id, unit.text)),
        )
    return out


def _lexical_words(text: str) -> tuple[str, ...]:
    visible = _PLACEHOLDER_RE.sub(" ", text)
    return tuple(match.group(0).casefold() for match in _WORD_RE.finditer(visible))


def _duplicate_sides_next_to_anchor(
    parent_text: str, placeholder: str, child_text: str
) -> frozenset[str]:
    if parent_text.count(placeholder) != 1:
        return frozenset()
    child_words = _lexical_words(child_text)
    if not child_words or sum(len(word) for word in child_words) < 3:
        return frozenset()
    left, right = parent_text.split(placeholder, 1)
    left_words = _lexical_words(left)
    right_words = _lexical_words(right)
    size = len(child_words)
    sides: set[str] = set()
    if len(left_words) >= size and left_words[-size:] == child_words:
        sides.add("left")
    if len(right_words) >= size and right_words[:size] == child_words:
        sides.add("right")
    return frozenset(sides)


_ANCHOR_PREFIX_FUNCTION_WORDS = frozenset(
    {
        # Articles/determiners used by the currently supported target languages.
        "a", "an", "the",
        "un", "una", "uno", "el", "la", "los", "las",
        "ein", "eine", "einer", "einem", "einen", "eines",
        "der", "die", "das", "den", "dem", "des",
        "le", "les", "une",
        "o", "os", "as", "um", "uma", "uns", "umas",
        "il", "lo", "gli", "i",
        "это", "этот", "эта", "эти",
        "данный", "данная", "данное", "данные",
    }
)


def _clause_words_before_anchor(text: str) -> tuple[str, ...]:
    """Return lexical words in the local clause immediately before an anchor."""

    starts = [0]
    for match in _PLACEHOLDER_RE.finditer(text):
        starts.append(match.end())
    for match in re.finditer(r"[.!?]\s+", text):
        starts.append(match.end())
    return _lexical_words(text[max(starts) :])


def _introduces_label_before_bare_anchor(
    source_parent: str, candidate: str, placeholder: str
) -> bool:
    """Detect an invented label before an article-led semantic anchor.

    A real runtime failure translated ``An [#0#] is ...`` as
    ``Аффикс [#0#] — ...`` while the semantic child itself was translated to a
    different synonym. Exact child-text comparison cannot catch that case.
    Only the narrow article-at-clause-start + copula shape is guarded here so
    ordinary reordering around anchors elsewhere in a sentence remains valid.
    """

    if source_parent.count(placeholder) != 1 or candidate.count(placeholder) != 1:
        return False

    source_left, source_right = source_parent.split(placeholder, 1)
    if _clause_words_before_anchor(source_left) not in {
        ("a",),
        ("an",),
        ("the",),
    }:
        return False
    if re.match(r"\s*(?:is|are|was|were)\b", source_right, re.IGNORECASE) is None:
        return False

    candidate_left, _ = candidate.split(placeholder, 1)
    candidate_words = _clause_words_before_anchor(candidate_left)
    if not candidate_words:
        return False
    last_word = candidate_words[-1]
    return len(last_word) > 2 and last_word not in _ANCHOR_PREFIX_FUNCTION_WORDS


def _semantic_duplication_reason(
    work: "FormatKitBookWork",
    parent_id: str,
    candidate: str,
    resolved_semantic: Mapping[str, str],
) -> str | None:
    parent = work.units_by_id.get(parent_id)
    if parent is None:
        return None
    for anchor in _semantic_anchor_rows(work.source_plan, parent_id):
        placeholder = getattr(anchor, "placeholder", None)
        child_id = getattr(anchor, "child_id", None)
        if not isinstance(placeholder, str) or not isinstance(child_id, str):
            continue
        child = work.units_by_id.get(child_id)
        child_candidate = resolved_semantic.get(child_id)
        if child is None or not isinstance(child_candidate, str):
            continue

        # Repetition already present in canonical source is author-owned and must
        # never be "fixed" by MineAI. Reject only a newly introduced adjacency.
        source_sides = _duplicate_sides_next_to_anchor(
            parent.text, placeholder, child.text
        )
        candidate_sides = _duplicate_sides_next_to_anchor(
            candidate, placeholder, child_candidate
        )
        introduced_sides = candidate_sides - source_sides
        if introduced_sides:
            side_label = ",".join(sorted(introduced_sides))
            return (
                f"semantic anchor {placeholder} duplicates translated child "
                f"{child_id} outside its source-owned wrapper ({side_label})"
            )
        if _introduces_label_before_bare_anchor(parent.text, candidate, placeholder):
            return (
                f"semantic anchor {placeholder} introduces an extra label before "
                f"translated child {child_id}"
            )
    return None


@dataclass(frozen=True)
class FormatKitBookWork:
    adapter_name: str
    adapter: object
    source_plan: TranslationPlan
    units_by_id: Mapping[str, TranslationUnit]
    pending: Mapping[str, str]
    preserved: Mapping[str, str]
    passthrough: Mapping[str, str]
    total_translatable: int
    target_path: str
    target_parse_error: str | None
    emit_structural_copy: bool = False
    target_reuse_disabled: bool = False


# Only formats already exercised by the MineAI pilots are enabled here. The
# current SDK registry supports more formats, but v3.4 is a synchronization and
# cleanup step, not a feature-expansion release.
_BOOK_ADAPTERS = (
    MineAiModonomiconBookJsonAdapter(),
    MineAiPatchouliBookJsonAdapter(),
    ImmersiveEngineeringManualAdapter(),
)


def book_adapter_for(path: str):
    normalized = path.replace("\\", "/")
    for adapter in _BOOK_ADAPTERS:
        if adapter.matches(normalized):
            return adapter
    return None


def is_formatkit_book_path(path: str) -> bool:
    return book_adapter_for(path) is not None


def _is_modonomicon_data(adapter) -> bool:
    return getattr(adapter, "name", "") == "modonomicon-book-json"


def target_path_for_book(path: str, target_code: str) -> str | None:
    """Return MineAI's output path for an SDK-owned book source.

    Modonomicon book JSON is locale-neutral datapack data. The SDK deliberately
    refuses to invent a locale target path for it; MineAI, as the output-policy
    owner, overlays the translated document at the same ``data/...`` path.
    """

    adapter = book_adapter_for(path)
    if adapter is None:
        return None
    normalized = path.replace("\\", "/")
    if _is_modonomicon_data(adapter):
        return normalized
    return adapter.target_path(normalized, target_code)


def _raw_source_value(plan: TranslationPlan, unit: TranslationUnit) -> str:
    # Prefer SDK-owned semantic payload metadata where available. This keeps
    # host filtering independent from parser internals while correctly handling
    # semantic child units whose source offsets intentionally cover an outer
    # field/line.
    for key in ("originals", "semantic_payloads", "original_values"):
        values = plan.metadata.get(key)
        if isinstance(values, dict):
            value = values.get(unit.id)
            if isinstance(value, str):
                return value
    return unit.text


def _protected_values(unit: TranslationUnit) -> tuple[str, ...]:
    return tuple(fragment.value for fragment in unit.protected)


def _has_semantic_anchors(plan: TranslationPlan) -> bool:
    anchors = plan.metadata.get("semantic_anchors")
    return isinstance(anchors, dict) and bool(anchors)


def _display_adapter_name(adapter, plan: TranslationPlan) -> str:
    # The current SDK intentionally folds Patchouli templates into the normal
    # Patchouli adapter and exposes zero translation units. Keep the historical
    # MineAI label only for UI/logging and structural-copy policy; there is no
    # second template parser anymore.
    if plan.metadata.get("patchouli_template_immutable") is True:
        return "patchouli-template-json"
    return getattr(adapter, "name", type(adapter).__name__)


def _validate_candidate(
    adapter,
    plan: TranslationPlan,
    units_by_id: Mapping[str, TranslationUnit],
    unit_id: str,
    candidate: str,
) -> tuple[bool, str | None]:
    """Validate one candidate through the unmodified SDK adapter.

    v3.2's per-unit fallback remains a MineAI transport policy. The former
    vendored SDK helper has been removed: reconstructing one candidate against
    the canonical plan invokes the adapter's own local and whole-document
    invariants and is therefore the authoritative fail-closed check.
    """

    if unit_id not in units_by_id:
        return False, f"unknown translation unit {unit_id}"
    try:
        adapter.apply(plan, {unit_id: candidate})
    except (ValidationError, ValueError) as exc:
        return False, str(exc)
    return True, None


def _existing_target_candidates(
    adapter,
    source_plan: TranslationPlan,
    target_text: str,
):
    """Yield public-SDK target candidates in source unit order.

    No private ``_parse``/``_protect``/JSON-pointer access is used. Re-planning
    the target through the same public adapter lets us compare unit topology and
    exact protected runtime fragments before considering reuse.
    """

    target_plan = adapter.prepare(source_plan.path, target_text)
    if len(source_plan.units) != len(target_plan.units):
        raise ValidationError("existing target translation unit topology changed")

    for source_unit, target_unit in zip(source_plan.units, target_plan.units):
        if source_unit.kind != target_unit.kind:
            raise ValidationError("existing target translation unit kind changed")
        if _protected_values(source_unit) != _protected_values(target_unit):
            raise ValidationError("existing target protected runtime fragments changed")
        yield source_unit, target_unit.text


def plan_book_work(
    path: str,
    source_text: str,
    target_code: str,
    target_regex: str,
    target_text: str | None,
    mode: str,
) -> FormatKitBookWork | None:
    adapter = book_adapter_for(path)
    if adapter is None:
        return None

    normalized = path.replace("\\", "/")
    source_plan = adapter.prepare(normalized, source_text)
    units_by_id = source_plan.by_id()
    display_name = _display_adapter_name(adapter, source_plan)
    emit_structural_copy = source_plan.metadata.get("patchouli_template_immutable") is True
    target_path = target_path_for_book(normalized, target_code)
    assert target_path is not None

    eligible_ids: set[str] = set()
    for unit in source_plan.units:
        original = _raw_source_value(source_plan, unit)
        if not isinstance(original, str) or not original.strip():
            continue
        if not looks_like_source_language(original) or is_technical_term(original):
            continue
        eligible_ids.add(unit.id)

    preserved: dict[str, str] = {}
    target_parse_error: str | None = None

    # A v3.3 target can preserve every flat marker yet attach a style/link to the
    # wrong words. Once the SDK reports semantic anchors, do not mine wording
    # from that old target. Rebuild from canonical English + newly validated
    # semantic units instead. This is intentionally conservative for the first
    # SDK-sync pilot and prevents a previously accepted bad pack from reviving
    # through Append mode.
    target_reuse_disabled = _has_semantic_anchors(source_plan)

    if (
        mode != "force"
        and target_text
        and not emit_structural_copy
        and not _is_modonomicon_data(adapter)
        and not target_reuse_disabled
    ):
        try:
            for source_unit, candidate in _existing_target_candidates(
                adapter, source_plan, target_text
            ):
                if source_unit.id not in eligible_ids:
                    continue
                if candidate == source_unit.text:
                    continue
                if not already_translated(candidate, target_regex):
                    continue
                ok, reason = _validate_candidate(
                    adapter, source_plan, units_by_id, source_unit.id, candidate
                )
                if not ok:
                    raise ValidationError(reason or "existing target candidate rejected")
                preserved[source_unit.id] = candidate
        except (ValidationError, ValueError, KeyError, IndexError, TypeError) as exc:
            target_parse_error = str(exc)
            preserved.clear()

    pending = {
        unit.id: unit.text
        for unit in source_plan.units
        if unit.id in eligible_ids and unit.id not in preserved
    }
    passthrough = {
        unit.id: unit.text
        for unit in source_plan.units
        if unit.id not in eligible_ids
    }

    return FormatKitBookWork(
        adapter_name=display_name,
        adapter=adapter,
        source_plan=source_plan,
        units_by_id=units_by_id,
        pending=pending,
        preserved=preserved,
        passthrough=passthrough,
        total_translatable=len(eligible_ids),
        target_path=target_path,
        target_parse_error=target_parse_error,
        emit_structural_copy=emit_structural_copy,
        target_reuse_disabled=target_reuse_disabled,
    )


def validate_book_candidate(
    work: FormatKitBookWork,
    unit_id: str,
    candidate: str,
    *,
    resolved_semantic: Mapping[str, str] | None = None,
) -> tuple[bool, str | None]:
    ok, reason = _validate_candidate(
        work.adapter,
        work.source_plan,
        work.units_by_id,
        unit_id,
        candidate,
    )
    if not ok:
        return False, f"FormatKit: {reason}"
    if resolved_semantic is not None:
        duplicate_reason = _semantic_duplication_reason(
            work, unit_id, candidate, resolved_semantic
        )
        if duplicate_reason is not None:
            return False, f"FormatKit: {duplicate_reason}"
    return True, None


def build_book_output(work: FormatKitBookWork, translated: Mapping[str, str]) -> str:
    values = dict(work.passthrough)
    values.update(work.preserved)
    for unit_id in work.pending:
        values[unit_id] = translated.get(unit_id, work.units_by_id[unit_id].text)

    # Semantic child units are reconstructed inside their parent prose. If a
    # parent translation was rejected and therefore falls back to canonical
    # source text, applying a translated child would create a mixed-language
    # fragment (for example ``An $(6)Прикрепление$() is ...``). Keep the whole
    # source-owned semantic fragment atomic on fallback: a child is reverted
    # only when every parent that owns it is also falling back.
    child_parents: dict[str, set[str]] = {}
    anchors = work.source_plan.metadata.get("semantic_anchors")
    if isinstance(anchors, dict):
        for parent_id, rows in anchors.items():
            if not isinstance(parent_id, str) or not isinstance(rows, (tuple, list)):
                continue
            for anchor in rows:
                child_id = getattr(anchor, "child_id", None)
                if isinstance(child_id, str):
                    child_parents.setdefault(child_id, set()).add(parent_id)

    for child_id, parent_ids in child_parents.items():
        if child_id not in values:
            continue
        if parent_ids and all(
            parent_id in work.pending
            and (
                parent_id not in translated
                or translated.get(parent_id) == work.units_by_id[parent_id].text
            )
            for parent_id in parent_ids
        ):
            child = work.units_by_id.get(child_id)
            if child is not None:
                values[child_id] = child.text

    return work.adapter.apply(work.source_plan, values)


__all__ = [
    "FORMATKIT_PILOT_HARDENING",
    "FORMATKIT_SOURCE_SHA",
    "FormatKitBookWork",
    "book_adapter_for",
    "build_book_output",
    "is_formatkit_book_path",
    "plan_book_work",
    "semantic_resolved_values",
    "split_semantic_book_pending",
    "target_path_for_book",
    "validate_book_candidate",
]
