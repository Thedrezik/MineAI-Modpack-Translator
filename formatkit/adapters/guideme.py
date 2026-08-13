"""GuideME-specific Markdown adapter."""

from __future__ import annotations

import re

from formatkit.adapters.markdown import MarkdownAdapter
from formatkit.contracts import TranslationPlan


class GuideMeAdapter(MarkdownAdapter):
    adapter_id = "guideme-v2"

    def supports(self, logical_path: str, text: str) -> bool:
        del text
        normalized = logical_path.replace("\\", "/").lower()
        return "/ae2guide/" in normalized and normalized.endswith(".md")

    def plan(
        self,
        logical_path: str,
        text: str,
        target_locale: str,
        *,
        target_path_hint: str | None = None,
    ) -> TranslationPlan:
        normalized = logical_path.replace("\\", "/")
        root_match = re.search(r"/ae2guide/", normalized, re.IGNORECASE)
        if root_match:
            tail = normalized[root_match.end() :]
            if re.match(r"_?[a-z]{2}_[a-z]{2}/", tail, re.IGNORECASE):
                tail = re.sub(
                    r"^_?[a-z]{2}_[a-z]{2}/",
                    "",
                    tail,
                    count=1,
                    flags=re.IGNORECASE,
                )
            target_path_hint = (
                normalized[: root_match.end()]
                + f"_{target_locale}/"
                + tail
            )
        base = super().plan(
            logical_path,
            text,
            target_locale,
            target_path_hint=target_path_hint,
        )
        return TranslationPlan(
            adapter_id=self.adapter_id,
            logical_path=base.logical_path,
            source_text=base.source_text,
            target_path=base.target_path,
            units=tuple(
                unit.__class__(
                    id=unit.id.replace("markdown-v2:", "guideme-v2:"),
                    payload=unit.payload,
                    start=unit.start,
                    end=unit.end,
                    context=unit.context,
                    anchors=unit.anchors,
                    kind=unit.kind,
                )
                for unit in base.units
            ),
            validator=base.validator,
        )
