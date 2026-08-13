"""Compatibility bridge from the standalone FormatKit SDK to MineAI plans."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Mapping

from mineai_formatkit import FormatKit as SdkFormatKit
from mineai_formatkit import ValidationError as SdkValidationError

from formatkit.contracts import (
    ANCHOR_PATTERN,
    ApplyResult,
    FormatValidationError,
    TranslationUnit,
    ValidationReport,
)


_LOCALE_SEGMENT_RE = re.compile(r"^_?[a-z]{2}_[a-z]{2}$", re.IGNORECASE)
_PLACEHOLDER_RE = re.compile(r"\[#\d+#\]")


def _canonical_source_path(logical_path: str) -> str:
    """Map a localized target path back to a path detectable by the SDK."""
    slash = logical_path.replace("\\", "/")
    parts = slash.split("/")

    if ".translated" in parts:
        index = parts.index(".translated")
        if index + 1 < len(parts) and _LOCALE_SEGMENT_RE.fullmatch(parts[index + 1]):
            del parts[index : index + 2]

    lowered = [part.casefold() for part in parts]
    for index, part in enumerate(tuple(parts)):
        if not _LOCALE_SEGMENT_RE.fullmatch(part):
            continue
        previous = lowered[index - 1] if index else ""
        if previous == "manual":
            parts[index] = "en_us"
        elif previous == "lang" and index == len(parts) - 1:
            parts[index] = "en_us"
        elif part.startswith("_"):
            del parts[index]
            break
        elif "patchouli_books" in lowered[:index]:
            parts[index] = "en_us"

    if parts:
        filename = parts[-1]
        stem, dot, suffix = filename.partition(".")
        if dot and _LOCALE_SEGMENT_RE.fullmatch(stem):
            parts[-1] = f"en_us.{suffix}"
    canonical = "/".join(parts)
    if re.fullmatch(
        r"[a-z0-9_.-]+/lang/en_us\.json",
        canonical,
        re.IGNORECASE,
    ):
        canonical = f"assets/{canonical}"
    return canonical


def _visible(payload: str) -> str:
    return _PLACEHOLDER_RE.sub("", payload)


@dataclass(frozen=True)
class UpstreamTranslationPlan:
    """Expose the SDK plan through the stable Beta36 plan interface."""

    adapter_id: str
    logical_path: str
    source_text: str
    target_path: str | None
    units: tuple[TranslationUnit, ...]
    _adapter: object = field(repr=False, compare=False)
    _sdk_plan: object = field(repr=False, compare=False)
    _base_translations: Mapping[str, str] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )

    def _combined(self, translations: Mapping[str, str]) -> dict[str, str]:
        combined = dict(self._base_translations)
        combined.update(translations)
        return combined

    def apply(self, translations: Mapping[str, str]) -> ApplyResult:
        try:
            output = self._adapter.apply(
                self._sdk_plan,
                self._combined(translations),
            )
        except (SdkValidationError, ValueError, KeyError) as exc:
            raise FormatValidationError(str(exc)) from exc
        return ApplyResult(
            text=output,
            target_path=self.target_path,
            validation=ValidationReport(ok=True),
        )

    def apply_resilient(
        self,
        translations: Mapping[str, str],
    ) -> tuple[ApplyResult, dict[str, str]]:
        try:
            return self.apply(translations), {}
        except FormatValidationError:
            pass

        accepted: dict[str, str] = {}
        rejected: dict[str, str] = {}
        for unit in self.units:
            if unit.id not in translations:
                continue
            candidate = {**accepted, unit.id: translations[unit.id]}
            try:
                self.apply(candidate)
            except FormatValidationError as exc:
                rejected[unit.id] = str(exc)
            else:
                accepted[unit.id] = translations[unit.id]
        return self.apply(accepted), rejected

    def candidate_error(
        self,
        unit_id: str,
        candidate: str,
        visible_text_validator=None,
    ) -> str | None:
        unit = next((item for item in self.units if item.id == unit_id), None)
        if unit is None:
            return f"Unknown translation unit id: {unit_id}"
        try:
            self.apply({unit_id: candidate})
        except FormatValidationError as exc:
            return str(exc)
        if visible_text_validator is None:
            return None
        return visible_text_validator(_visible(unit.payload), _visible(candidate))

    def merge_existing(
        self,
        target: "UpstreamTranslationPlan",
        target_regex: str,
    ) -> tuple["UpstreamTranslationPlan", frozenset[str]]:
        if not isinstance(target, UpstreamTranslationPlan):
            raise FormatValidationError("Existing target uses another adapter family")
        if self.adapter_id != target.adapter_id:
            raise FormatValidationError("Existing target uses another SDK adapter")

        source_sdk_units = tuple(self._sdk_plan.units)
        target_sdk_units = tuple(target._sdk_plan.units)
        if len(source_sdk_units) != len(target_sdk_units):
            raise FormatValidationError("Existing target has a different unit layout")

        base: dict[str, str] = {}
        pending: set[str] = set()
        for source_unit, target_unit in zip(source_sdk_units, target_sdk_units):
            source_fragments = tuple(
                (item.placeholder, item.value) for item in source_unit.protected
            )
            target_fragments = tuple(
                (item.placeholder, item.value) for item in target_unit.protected
            )
            if (
                source_unit.kind != target_unit.kind
                or source_fragments != target_fragments
            ):
                raise FormatValidationError(
                    "Existing target changed protected structure"
                )

            candidate = target_unit.text
            try:
                self._adapter.apply(self._sdk_plan, {source_unit.id: candidate})
            except (SdkValidationError, ValueError, KeyError) as exc:
                raise FormatValidationError(str(exc)) from exc
            if re.search(target_regex, _visible(candidate)) and candidate != source_unit.text:
                base[source_unit.id] = candidate
            else:
                pending.add(source_unit.id)

        return (
            UpstreamTranslationPlan(
                adapter_id=self.adapter_id,
                logical_path=self.logical_path,
                source_text=self.source_text,
                target_path=self.target_path,
                units=self.units,
                _adapter=self._adapter,
                _sdk_plan=self._sdk_plan,
                _base_translations=base,
            ),
            frozenset(pending),
        )

    def validate_output(self, output_text: str) -> None:
        try:
            target_plan = self._adapter.prepare(
                _canonical_source_path(self.logical_path),
                output_text,
            )
        except (SdkValidationError, ValueError, KeyError) as exc:
            raise FormatValidationError(str(exc)) from exc

        source_units = tuple(self._sdk_plan.units)
        target_units = tuple(target_plan.units)
        if len(source_units) != len(target_units):
            raise FormatValidationError(
                "MineAI-FormatKit target has a different unit layout"
            )

        translations: dict[str, str] = {}
        for source_unit, target_unit in zip(source_units, target_units):
            source_fragments = tuple(
                (item.placeholder, item.value) for item in source_unit.protected
            )
            target_fragments = tuple(
                (item.placeholder, item.value) for item in target_unit.protected
            )
            if (
                source_unit.kind != target_unit.kind
                or source_fragments != target_fragments
            ):
                raise FormatValidationError(
                    "MineAI-FormatKit detected changed protected structure"
                )
            translations[source_unit.id] = target_unit.text

        try:
            rebuilt = self._adapter.apply(self._sdk_plan, translations)
        except (SdkValidationError, ValueError, KeyError) as exc:
            raise FormatValidationError(str(exc)) from exc
        if rebuilt != output_text:
            raise FormatValidationError(
                "MineAI-FormatKit reconstruction is not byte-exact"
            )

    def can_validate_legacy_plan(self, legacy_plan) -> bool:
        """Return whether SDK coverage accepts every legacy-visible span."""
        translations = {
            unit.id: "".join(
                part
                if ANCHOR_PATTERN.fullmatch(part)
                else re.sub(r"[A-Za-z]+", "Текст", part)
                for part in re.split(
                    f"({ANCHOR_PATTERN.pattern})",
                    unit.payload,
                )
            )
            for unit in legacy_plan.units
        }
        if not translations or all(
            translations[unit.id] == unit.payload
            for unit in legacy_plan.units
        ):
            return True
        try:
            output = legacy_plan.apply(translations).text
            self.validate_output(output)
        except (FormatValidationError, ValueError, KeyError):
            return False
        return True


class DualValidatedPlan:
    """Keep Beta36 extraction while requiring the standalone SDK to agree."""

    validation_layers = ("formatkit-beta36", "mineai-formatkit")

    def __init__(self, legacy_plan, sdk_plan: UpstreamTranslationPlan) -> None:
        self._legacy_plan = legacy_plan
        self._sdk_plan = sdk_plan

    def __getattr__(self, name):
        return getattr(self._legacy_plan, name)

    def apply(self, translations: Mapping[str, str]) -> ApplyResult:
        result = self._legacy_plan.apply(translations)
        self._sdk_plan.validate_output(result.text)
        return result

    def apply_resilient(
        self,
        translations: Mapping[str, str],
    ) -> tuple[ApplyResult, dict[str, str]]:
        try:
            return self.apply(translations), {}
        except FormatValidationError:
            pass

        accepted: dict[str, str] = {}
        rejected: dict[str, str] = {}
        for unit in self.units:
            if unit.id not in translations:
                continue
            candidate = {**accepted, unit.id: translations[unit.id]}
            try:
                self.apply(candidate)
            except FormatValidationError as exc:
                rejected[unit.id] = str(exc)
            else:
                accepted[unit.id] = translations[unit.id]
        return self.apply(accepted), rejected

    def candidate_error(
        self,
        unit_id: str,
        candidate: str,
        visible_text_validator=None,
    ) -> str | None:
        reason = self._legacy_plan.candidate_error(
            unit_id,
            candidate,
            visible_text_validator,
        )
        if reason:
            return reason
        try:
            self.apply({unit_id: candidate})
        except FormatValidationError as exc:
            return str(exc)
        return None

    def merge_existing(self, target, target_regex: str):
        target_legacy = (
            target._legacy_plan
            if isinstance(target, DualValidatedPlan)
            else target
        )
        merged, pending = self._legacy_plan.merge_existing(
            target_legacy,
            target_regex,
        )
        wrapped = DualValidatedPlan(merged, self._sdk_plan)
        wrapped.apply({})
        return wrapped, pending


class UpstreamAdapter:
    """One registry entry that delegates detection to the standalone SDK."""

    def __init__(self) -> None:
        self.kit = SdkFormatKit.default()

    def supports(self, logical_path: str, text: str) -> bool:
        return self.kit.detect(_canonical_source_path(logical_path)) is not None

    def adapter_id_for(self, logical_path: str) -> str | None:
        detection = self.kit.detect(logical_path.replace("\\", "/"))
        if detection is None:
            return None
        return detection.capabilities.name

    def target_path_for(
        self,
        logical_path: str,
        target_locale: str,
    ) -> str | None:
        source_path = logical_path.replace("\\", "/")
        detection = self.kit.detect(source_path)
        if detection is None or not detection.capabilities.supports_target_path:
            return None
        target_path = getattr(detection.adapter, "target_path", None)
        if not callable(target_path):
            return None
        return target_path(source_path, target_locale)

    def plan(
        self,
        logical_path: str,
        text: str,
        target_locale: str,
        *,
        target_path_hint: str | None = None,
    ) -> UpstreamTranslationPlan:
        source_path = _canonical_source_path(logical_path)
        analysis = self.kit.analyze(
            source_path,
            text,
            target_locale=target_locale,
        )
        if not analysis.ready or analysis.plan is None or analysis.detection is None:
            details = "; ".join(item.message for item in analysis.diagnostics)
            raise ValueError(details or f"FormatKit cannot prepare {logical_path}")

        sdk_plan = analysis.plan
        units = tuple(
            TranslationUnit(
                id=unit.id,
                payload=unit.text,
                start=unit.start,
                end=unit.end,
                context=unit.context,
                kind=unit.kind,
            )
            for unit in sdk_plan.units
        )
        return UpstreamTranslationPlan(
            adapter_id=analysis.adapter_name or analysis.detection.adapter.name,
            logical_path=logical_path,
            source_text=text,
            target_path=target_path_hint or analysis.target_path,
            units=units,
            _adapter=analysis.detection.adapter,
            _sdk_plan=sdk_plan,
        )
