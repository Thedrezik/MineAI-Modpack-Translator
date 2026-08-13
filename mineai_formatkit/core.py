from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


class ValidationError(ValueError):
    """Raised when translated content violates a format invariant."""


@dataclass(frozen=True)
class ProtectedFragment:
    placeholder: str
    value: str


@dataclass(frozen=True)
class TranslationUnit:
    """One replaceable semantic span inside an immutable source document.

    ``text`` is what the translator receives. Technical fragments may be
    represented by placeholders. ``start``/``end`` address the original source
    text, so serialization never has to reformat the rest of the document.
    """

    id: str
    text: str
    start: int
    end: int
    kind: str
    context: str = ""
    protected: tuple[ProtectedFragment, ...] = ()

    @property
    def source_span_length(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class TranslationPlan:
    path: str
    source_text: str
    units: tuple[TranslationUnit, ...]
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for unit in self.units:
            if unit.start < 0 or unit.end <= unit.start:
                raise ValueError(f"Invalid translation unit range: {unit.id}")
            if unit.end > len(self.source_text):
                raise ValueError(f"Translation unit exceeds source: {unit.id}")

    def by_id(self) -> dict[str, TranslationUnit]:
        return {unit.id: unit for unit in self.units}
