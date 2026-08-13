from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from .core import TranslationPlan, TranslationUnit, ValidationError
from .registry import AdapterCapabilities, DetectedFormat, FormatRegistry


class DiagnosticSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class Diagnostic:
    severity: DiagnosticSeverity
    code: str
    message: str
    path: str = ""
    adapter: str = ""


@dataclass(frozen=True)
class FormatAnalysis:
    """Result of analyzing one already-discovered text file.

    FormatKit intentionally does not decide where this file came from. The caller may
    have read it from a JAR, resource pack, modpack, Minecraft directory or a loose
    file. ``plan`` is present only when the matched adapter prepared the file safely.
    """

    path: str
    detection: DetectedFormat | None
    plan: TranslationPlan | None
    target_path: str | None = None
    diagnostics: tuple[Diagnostic, ...] = ()

    @property
    def supported(self) -> bool:
        return self.detection is not None

    @property
    def ready(self) -> bool:
        return self.detection is not None and self.plan is not None and not self.has_errors

    @property
    def adapter_name(self) -> str | None:
        return None if self.detection is None else self.detection.capabilities.name

    @property
    def capabilities(self) -> AdapterCapabilities | None:
        return None if self.detection is None else self.detection.capabilities

    @property
    def units(self) -> tuple[TranslationUnit, ...]:
        return () if self.plan is None else self.plan.units

    @property
    def unit_count(self) -> int:
        return len(self.units)

    @property
    def has_errors(self) -> bool:
        return any(item.severity == DiagnosticSeverity.ERROR for item in self.diagnostics)


class FormatKit:
    """Small embedding facade over format adapters.

    This class operates on a single path + decoded text payload. It deliberately does
    not scan directories, choose Minecraft installations, call translation providers or
    decide output packaging policy for the host application.
    """

    def __init__(self, registry: FormatRegistry | None = None) -> None:
        self.registry = registry or FormatRegistry.default()

    @classmethod
    def default(cls) -> "FormatKit":
        return cls(FormatRegistry.default())

    def detect(self, path: str) -> DetectedFormat | None:
        return self.registry.detect_result(path)

    def analyze(
        self,
        path: str,
        source_text: str,
        *,
        target_locale: str | None = None,
    ) -> FormatAnalysis:
        detection = self.detect(path)
        if detection is None:
            return FormatAnalysis(
                path=path,
                detection=None,
                plan=None,
                diagnostics=(
                    Diagnostic(
                        DiagnosticSeverity.INFO,
                        "unsupported_format",
                        "No registered FormatKit adapter matches this path.",
                        path=path,
                    ),
                ),
            )

        adapter = detection.adapter
        try:
            plan = adapter.prepare(path, source_text)
        except ValueError as exc:
            return FormatAnalysis(
                path=path,
                detection=detection,
                plan=None,
                diagnostics=(
                    Diagnostic(
                        DiagnosticSeverity.ERROR,
                        "prepare_failed",
                        str(exc),
                        path=path,
                        adapter=detection.capabilities.name,
                    ),
                ),
            )

        diagnostics = list(self._plan_diagnostics(plan, detection))
        target_path = None
        if target_locale and detection.capabilities.supports_target_path:
            target_path_fn = getattr(adapter, "target_path", None)
            if callable(target_path_fn):
                try:
                    target_path = target_path_fn(path, target_locale)
                except ValueError as exc:
                    diagnostics.append(
                        Diagnostic(
                            DiagnosticSeverity.ERROR,
                            "target_path_failed",
                            str(exc),
                            path=path,
                            adapter=detection.capabilities.name,
                        )
                    )

        return FormatAnalysis(
            path=path,
            detection=detection,
            plan=plan,
            target_path=target_path,
            diagnostics=tuple(diagnostics),
        )

    @staticmethod
    def apply(analysis: FormatAnalysis, translations: Mapping[str, str]) -> str:
        if not analysis.ready:
            raise ValidationError("Cannot apply translations to an analysis that is not ready")
        assert analysis.detection is not None and analysis.plan is not None
        return analysis.detection.adapter.apply(analysis.plan, translations)

    @staticmethod
    def _plan_diagnostics(
        plan: TranslationPlan,
        detection: DetectedFormat,
    ) -> tuple[Diagnostic, ...]:
        out: list[Diagnostic] = []

        embedded = plan.metadata.get("diagnostics")
        if isinstance(embedded, (list, tuple)):
            out.extend(item for item in embedded if isinstance(item, Diagnostic))

        unsupported = plan.metadata.get("unsupported_non_string_keys")
        if isinstance(unsupported, (list, tuple)) and unsupported:
            preview = ", ".join(str(value) for value in unsupported[:5])
            suffix = "" if len(unsupported) <= 5 else f" (+{len(unsupported) - 5} more)"
            out.append(
                Diagnostic(
                    DiagnosticSeverity.WARNING,
                    "structured_locale_values_unsupported",
                    f"Structured locale values were left untouched: {preview}{suffix}",
                    path=plan.path,
                    adapter=detection.capabilities.name,
                )
            )

        return tuple(out)
