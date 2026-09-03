"""Safe, embeddable format adapters for Minecraft translation tools."""

from formatkit.contracts import (
    ApplyResult,
    FormatValidationError,
    ProtectedAnchor,
    TranslationPlan,
    TranslationUnit,
    ValidationReport,
)
from formatkit.adapters.base import FormatAdapter
from formatkit.registry import FormatRegistry
from formatkit.references import relocated_dependencies

__all__ = [
    "ApplyResult",
    "FormatRegistry",
    "relocated_dependencies",
    "FormatAdapter",
    "FormatValidationError",
    "ProtectedAnchor",
    "TranslationPlan",
    "TranslationUnit",
    "ValidationReport",
]

__version__ = "1.0.0-beta34"
