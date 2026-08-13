"""Structure-safe format adapters for MineAI and other host applications."""

from .advancement import MinecraftAdvancementTextAdapter
from .config_locales import CollapsibleGroupsConfigLangJsonAdapter, JaopcaConfigLangJsonAdapter
from .core import TranslationPlan, TranslationUnit, ValidationError
from .guideme_safe import DataDrivenGuideMeMarkdownAdapter, GuideMeMarkdownAdapter
from .ftb_quests import FtbQuestsChapterAdapter, FtbQuestsLangAdapter
from .ftb_quests_merge import FtbQuestsLocaleMergePlan, FtbQuestsLocaleMergePlanner
from .ie_manual import ImmersiveEngineeringManualAdapter
from .locale_merge import LocaleMergePlan
from .locale_safe import (
    MinecraftLangJsonAdapter,
    LocaleMergePlanner,
)
from .jar_container import (
    JarContainer,
    JarInspection,
    JarSafetyError,
    NestedJarInspection,
    SignedJarError,
)
from .minecraft_text import MinecraftTextComponentAdapter
from .oracle_index import OracleIndexMdxAdapter, OracleIndexMetaJsonAdapter
from .patchouli_safe import PatchouliBookJsonAdapter
from .special_locales import CollapsibleGroupsLangJsonAdapter, CrashAssistantLocalizationAdapter
from .registry import AdapterCapabilities, DetectedFormat, FormatRegistry
from .sdk import Diagnostic, DiagnosticSeverity, FormatAnalysis, FormatKit

__all__ = [
    "AdapterCapabilities",
    "CollapsibleGroupsConfigLangJsonAdapter",
    "CollapsibleGroupsLangJsonAdapter",
    "CrashAssistantLocalizationAdapter",
    "DataDrivenGuideMeMarkdownAdapter",
    "DetectedFormat",
    "Diagnostic",
    "DiagnosticSeverity",
    "FormatAnalysis",
    "FormatKit",
    "FormatRegistry",
    "FtbQuestsChapterAdapter",
    "FtbQuestsLangAdapter",
    "FtbQuestsLocaleMergePlan",
    "FtbQuestsLocaleMergePlanner",
    "GuideMeMarkdownAdapter",
    "ImmersiveEngineeringManualAdapter",
    "JaopcaConfigLangJsonAdapter",
    "MinecraftAdvancementTextAdapter",
    "MinecraftLangJsonAdapter",
    "MinecraftTextComponentAdapter",
    "OracleIndexMdxAdapter",
    "OracleIndexMetaJsonAdapter",
    "PatchouliBookJsonAdapter",
    "LocaleMergePlan",
    "LocaleMergePlanner",
    "JarContainer",
    "JarInspection",
    "NestedJarInspection",
    "JarSafetyError",
    "SignedJarError",
    "TranslationPlan",
    "TranslationUnit",
    "ValidationError",
]
