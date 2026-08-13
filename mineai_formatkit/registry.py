from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

from .core import TranslationPlan


class FormatAdapter(Protocol):
    name: str

    def matches(self, path: str) -> bool: ...

    def prepare(self, path: str, source_text: str) -> TranslationPlan: ...

    def apply(self, plan: TranslationPlan, translations: Mapping[str, str]) -> str: ...


@dataclass(frozen=True)
class AdapterCapabilities:
    """Stable host-facing description of one content adapter."""

    name: str
    format_name: str
    canonical_source: str = "format-specific"
    supports_target_path: bool = False
    supports_existing_target_merge: bool = False
    structural_validation: bool = True
    container_independent: bool = True
    nested_formats: tuple[str, ...] = ()


@dataclass(frozen=True)
class DetectedFormat:
    adapter: FormatAdapter
    capabilities: AdapterCapabilities


class FormatRegistry:
    def __init__(self) -> None:
        self._adapters: list[FormatAdapter] = []
        self._capabilities: dict[int, AdapterCapabilities] = {}

    def register(
        self,
        adapter: FormatAdapter,
        capabilities: AdapterCapabilities | None = None,
    ) -> None:
        self._adapters.append(adapter)
        self._capabilities[id(adapter)] = capabilities or AdapterCapabilities(
            name=getattr(adapter, "name", adapter.__class__.__name__),
            format_name="custom",
        )

    @property
    def adapters(self) -> tuple[FormatAdapter, ...]:
        return tuple(self._adapters)

    def capabilities_for(self, adapter: FormatAdapter) -> AdapterCapabilities:
        return self._capabilities[id(adapter)]

    def detect(self, path: str) -> FormatAdapter | None:
        result = self.detect_result(path)
        return None if result is None else result.adapter

    def detect_result(self, path: str) -> DetectedFormat | None:
        for adapter in self._adapters:
            if adapter.matches(path):
                return DetectedFormat(adapter, self.capabilities_for(adapter))
        return None

    @classmethod
    def default(cls) -> "FormatRegistry":
        """Return the built-in adapter set with stable host-facing capabilities."""

        from .advancement import MinecraftAdvancementTextAdapter
        from .config_locales import (
            CollapsibleGroupsConfigLangJsonAdapter,
            JaopcaConfigLangJsonAdapter,
        )
        from .ftb_quests import FtbQuestsChapterAdapter, FtbQuestsLangAdapter
        from .guideme_safe import DataDrivenGuideMeMarkdownAdapter, GuideMeMarkdownAdapter
        from .ie_manual import ImmersiveEngineeringManualAdapter
        from .locale_safe import MinecraftLangJsonAdapter
        from .minecraft_text import MinecraftTextComponentAdapter
        from .oracle_index import OracleIndexMdxAdapter, OracleIndexMetaJsonAdapter
        from .patchouli_safe import PatchouliBookJsonAdapter
        from .special_locales import (
            CollapsibleGroupsLangJsonAdapter,
            CrashAssistantLocalizationAdapter,
        )

        registry = cls()
        builtins: tuple[tuple[FormatAdapter, AdapterCapabilities], ...] = (
            (
                GuideMeMarkdownAdapter(),
                AdapterCapabilities(
                    name="guideme-markdown",
                    format_name="Markdown / GuideME",
                    canonical_source="AE2 GuideME source tree",
                    supports_target_path=True,
                    nested_formats=("GuideME/HTML inline components",),
                ),
            ),
            (
                DataDrivenGuideMeMarkdownAdapter(),
                AdapterCapabilities(
                    name="guideme-data-driven-markdown",
                    format_name="Markdown / data-driven GuideME",
                    canonical_source="guides/<namespace>/<guide-id> source tree",
                    supports_target_path=True,
                    nested_formats=("GuideME/MDX inline components",),
                ),
            ),
            (
                CollapsibleGroupsLangJsonAdapter(),
                AdapterCapabilities(
                    name="collapsible-groups-lang-json",
                    format_name="Collapsible Groups bundled runtime locale JSON",
                    canonical_source="assets/collapsible_groups/group_lang/en_us.json",
                    supports_target_path=True,
                ),
            ),
            (
                CollapsibleGroupsConfigLangJsonAdapter(),
                AdapterCapabilities(
                    name="collapsible-groups-config-lang-json",
                    format_name="Collapsible Groups deployed config locale JSON",
                    canonical_source="config/collapsiblegroups/lang/en_us.json",
                    supports_target_path=True,
                    supports_existing_target_merge=True,
                    nested_formats=("strict structured locale Components",),
                ),
            ),
            (
                JaopcaConfigLangJsonAdapter(),
                AdapterCapabilities(
                    name="jaopca-config-lang-json",
                    format_name="JAOPCA runtime config locale JSON",
                    canonical_source="config/jaopca/lang/en_us.json",
                    supports_target_path=True,
                    supports_existing_target_merge=True,
                    nested_formats=("strict structured locale Components",),
                ),
            ),
            (
                CrashAssistantLocalizationAdapter(),
                AdapterCapabilities(
                    name="crash-assistant-localization",
                    format_name="CrashAssistant locale JSON",
                    canonical_source="crash_assistant_localization/en_us.json",
                    supports_target_path=True,
                    nested_formats=("CrashAssistant $...$ macros", "HTML"),
                ),
            ),
            (
                MinecraftLangJsonAdapter(),
                AdapterCapabilities(
                    name="minecraft-lang-json",
                    format_name="Minecraft locale JSON",
                    canonical_source="en_us.json",
                    supports_target_path=True,
                    supports_existing_target_merge=True,
                    nested_formats=("strict structured locale Components",),
                ),
            ),
            (
                MinecraftAdvancementTextAdapter(),
                AdapterCapabilities(
                    name="minecraft-advancement-text",
                    format_name="Minecraft advancement display text",
                    canonical_source="advancement display Components",
                    nested_formats=("JSON text components",),
                ),
            ),
            (
                MinecraftTextComponentAdapter(),
                AdapterCapabilities(
                    name="minecraft-text-components",
                    format_name="Minecraft structured JSON text",
                    canonical_source="recognized source JSON data",
                    nested_formats=("JSON text components",),
                ),
            ),
            (
                FtbQuestsLangAdapter(),
                AdapterCapabilities(
                    name="ftb-quests-lang",
                    format_name="FTB Quests locale SNBT",
                    canonical_source="quests/lang/en_us.snbt",
                    supports_target_path=True,
                    supports_existing_target_merge=True,
                    nested_formats=("JSON text components inside SNBT strings",),
                ),
            ),
            (
                FtbQuestsChapterAdapter(),
                AdapterCapabilities(
                    name="ftb-quests-chapter-text",
                    format_name="FTB Quests chapter SNBT",
                    canonical_source="allow-listed direct chapter text",
                    nested_formats=("JSON text components inside SNBT values",),
                ),
            ),
            (
                PatchouliBookJsonAdapter(),
                AdapterCapabilities(
                    name="patchouli-book-json",
                    format_name="Patchouli book JSON",
                    canonical_source="patchouli_books/<book>/en_us",
                    supports_target_path=True,
                    nested_formats=("Patchouli markup",),
                ),
            ),
            (
                OracleIndexMdxAdapter(),
                AdapterCapabilities(
                    name="oracle-index-mdx",
                    format_name="Oracle Index MDX",
                    canonical_source="oracle_index original .content tree",
                    supports_target_path=True,
                    nested_formats=("Markdown", "MDX/JSX"),
                ),
            ),
            (
                OracleIndexMetaJsonAdapter(),
                AdapterCapabilities(
                    name="oracle-index-meta-json",
                    format_name="Oracle Index navigation JSON",
                    canonical_source="oracle_index original _meta.json",
                    supports_target_path=True,
                ),
            ),
            (
                ImmersiveEngineeringManualAdapter(),
                AdapterCapabilities(
                    name="immersive-engineering-manual",
                    format_name="Immersive Engineering manual text",
                    canonical_source="manual/en_us/*.txt",
                    supports_target_path=True,
                    nested_formats=("IE manual directives",),
                ),
            ),
        )
        for adapter, capabilities in builtins:
            registry.register(adapter, capabilities)
        return registry
