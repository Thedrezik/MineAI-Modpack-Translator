"""Adapter selection for decoded Minecraft text resources."""

from __future__ import annotations

from formatkit.adapters.base import FormatAdapter
from formatkit.adapters.guideme import GuideMeAdapter
from formatkit.adapters.heracles import (
    HeraclesGroupsAdapter,
    HeraclesQuestAdapter,
    HeraclesTutorialAdapter,
)
from formatkit.adapters.ie_manual import ImmersiveEngineeringManualAdapter
from formatkit.adapters.modonomicon import ModonomiconAdapter
from formatkit.adapters.markdown import MarkdownAdapter
from formatkit.adapters.properties import PropertiesAdapter
from formatkit.adapters.xml_text import XmlTextAdapter
from formatkit.contracts import TranslationPlan


class FormatRegistry:
    def __init__(self, adapters: tuple[FormatAdapter, ...]) -> None:
        self.adapters = adapters

    @classmethod
    def default(cls) -> "FormatRegistry":
        return cls(
            (
                HeraclesQuestAdapter(),
                HeraclesGroupsAdapter(),
                HeraclesTutorialAdapter(),
                ModonomiconAdapter(),
                ImmersiveEngineeringManualAdapter(),
                GuideMeAdapter(),
                PropertiesAdapter(),
                XmlTextAdapter(),
                MarkdownAdapter(),
            )
        )

    def adapter_for(self, logical_path: str, text: str) -> FormatAdapter:
        for adapter in self.adapters:
            if adapter.supports(logical_path, text):
                return adapter
        raise ValueError(f"No FormatKit adapter for {logical_path}")

    def plan(
        self,
        logical_path: str,
        text: str,
        target_locale: str,
        *,
        target_path_hint: str | None = None,
    ) -> TranslationPlan:
        adapter = self.adapter_for(logical_path, text)
        return adapter.plan(
            logical_path,
            text,
            target_locale,
            target_path_hint=target_path_hint,
        )

    def companion_lang_prefixes(
        self,
        logical_paths: list[str],
    ) -> tuple[str, ...]:
        prefixes: set[str] = set()
        for logical_path in logical_paths:
            for adapter in self.adapters:
                if adapter.supports(logical_path, ""):
                    dynamic = getattr(adapter, "companion_prefixes_for", None)
                    if callable(dynamic):
                        prefixes.update(dynamic(logical_path))
                    else:
                        prefixes.update(
                            getattr(adapter, "companion_lang_prefixes", ())
                        )
                    break
        return tuple(sorted(prefixes))

    def companion_lang_keys(
        self,
        documents: list[tuple[str, str]],
    ) -> set[str]:
        keys: set[str] = set()
        for logical_path, text in documents:
            try:
                adapter = self.adapter_for(logical_path, text)
            except ValueError:
                continue
            collect = getattr(adapter, "companion_lang_keys", None)
            if callable(collect):
                try:
                    keys.update(collect(text))
                except ValueError:
                    continue
        return keys
