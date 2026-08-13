"""Base protocol for FormatKit adapters."""

from __future__ import annotations

from typing import Protocol

from formatkit.contracts import TranslationPlan


class FormatAdapter(Protocol):
    adapter_id: str

    def supports(self, logical_path: str, text: str) -> bool:
        ...

    def plan(
        self,
        logical_path: str,
        text: str,
        target_locale: str,
        *,
        target_path_hint: str | None = None,
    ) -> TranslationPlan:
        ...

