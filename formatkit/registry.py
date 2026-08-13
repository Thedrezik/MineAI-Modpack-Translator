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
from formatkit.upstream import DualValidatedPlan, UpstreamAdapter


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
                UpstreamAdapter(),
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
        plan = adapter.plan(
            logical_path,
            text,
            target_locale,
            target_path_hint=target_path_hint,
        )
        if isinstance(adapter, UpstreamAdapter):
            return plan

        upstream = next(
            (
                item
                for item in self.adapters
                if isinstance(item, UpstreamAdapter)
            ),
            None,
        )
        if upstream is None or not upstream.supports(logical_path, text):
            return plan
        sdk_plan = upstream.plan(
            logical_path,
            text,
            target_locale,
            target_path_hint=plan.target_path or target_path_hint,
        )
        if not sdk_plan.can_validate_legacy_plan(plan):
            return plan
        return DualValidatedPlan(plan, sdk_plan)

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

    def upstream_adapter_id(self, logical_path: str) -> str | None:
        upstream = next(
            (
                item
                for item in self.adapters
                if isinstance(item, UpstreamAdapter)
            ),
            None,
        )
        return None if upstream is None else upstream.adapter_id_for(logical_path)

    def upstream_target_path(
        self,
        logical_path: str,
        target_locale: str,
    ) -> str | None:
        upstream = next(
            (
                item
                for item in self.adapters
                if isinstance(item, UpstreamAdapter)
            ),
            None,
        )
        if upstream is None:
            return None
        return upstream.target_path_for(logical_path, target_locale)
