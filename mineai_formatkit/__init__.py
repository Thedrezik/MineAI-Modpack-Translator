"""Pinned public MineAI-FormatKit slice used by MineAI Pilot v3.4.1.

Every functional parser/reconstruction module vendored in this directory is a
byte-for-byte copy from LifeViwer/MineAI-FormatKit at ``FORMATKIT_SOURCE_SHA``.
This small facade intentionally exports only the already-certified MineAI pilot
slice so syncing the SDK cannot silently enable unrelated formats.
"""

from .config_locales import CollapsibleGroupsConfigLangJsonAdapter, JaopcaConfigLangJsonAdapter
from .core import ProtectedFragment, TranslationPlan, TranslationUnit, ValidationError
from .ie_manual import ImmersiveEngineeringManualAdapter
from .locale_merge import LocaleMergePlan
from .modonomicon import (
    ModonomiconAwareLocaleMergePlanner,
    ModonomiconAwareMinecraftLangJsonAdapter,
    ModonomiconBookJsonAdapter as _SdkModonomiconBookJsonAdapter,
)
from .patchouli_safe import PatchouliBookJsonAdapter

FORMATKIT_SOURCE_SHA = "5cfd1e28c1581caf144f9a5ef767c631d9199f8c"
FORMATKIT_PILOT_HARDENING = (
    "sdk-sync-v3.4.1:current-main+host-exact-order+per-unit-fallback+semantic-anchor+description-id-guards"
)

# Exact upstream Git blob ids for every SDK-owned functional module in the
# active v3.4.1 slice.  MineAI host policy is deliberately not stored here.
FORMATKIT_VENDOR_BLOBS = {
    "config_locales.py": "02336a92a06a47514501f76697526c547462031c",
    "core.py": "955b772b6d36dc07b07bb1a56fee550979d60d18",
    "ie_manual.py": "acdc6c671d0dacad3f7c0564f3a9aa459dd430af",
    "ie_manual_base.py": "f5875644a566718bef43e4c40bfd9befe4f29151",
    "locale_merge.py": "d7548d20e6c8a989ef09d4255ed50c0dfae9623d",
    "locale_safe.py": "bf85f81a6de7f4cc8b02f6693abdb1884ada5eaf",
    "minecraft_lang.py": "e2f882e72f7c35bb70a85debd9df054aa84540d4",
    "minecraft_text.py": "82961fffbb2b18fe6ffb55ede96e6bfa313de42b",
    "modonomicon.py": "2142c96bcba04b515440b4128bc12c98f7213189",
    "modonomicon_gson.py": "3271700b375bc446313fe3ea75fee1aab4f6c663",
    "patchouli.py": "18bf54c9e4128d68f8dd62dc8dd150192fbe0bd0",
    "patchouli_base.py": "27417f507cba377b846972d7b8ea3a87334e1675",
    "patchouli_safe.py": "9d9f4e94103c45a96e41a668bce5680019bc2c7b",
    "runtime_locale.py": "aed4cdf34207c6b7cd38f9bf209c3f8113ba441f",
    "structured_locale.py": "37d80a343db24d28adc8c711143813bb384ad3de",
}

# Long-standing public aliases remain the current upstream public adapters.
MinecraftLangJsonAdapter = ModonomiconAwareMinecraftLangJsonAdapter
LocaleMergePlanner = ModonomiconAwareLocaleMergePlanner


class ModonomiconBookJsonAdapter(_SdkModonomiconBookJsonAdapter):
    """Backward-compatible facade with MineAI's historical candidate hook."""

    def validate_candidate(self, plan, unit_id: str, candidate: str) -> None:
        if unit_id not in plan.by_id():
            raise ValidationError(f"Unknown translation unit id: {unit_id}")
        self.apply(plan, {unit_id: candidate})

__all__ = [
    "CollapsibleGroupsConfigLangJsonAdapter",
    "FORMATKIT_PILOT_HARDENING",
    "FORMATKIT_SOURCE_SHA",
    "FORMATKIT_VENDOR_BLOBS",
    "ImmersiveEngineeringManualAdapter",
    "JaopcaConfigLangJsonAdapter",
    "LocaleMergePlan",
    "LocaleMergePlanner",
    "MinecraftLangJsonAdapter",
    "ModonomiconBookJsonAdapter",
    "PatchouliBookJsonAdapter",
    "ProtectedFragment",
    "TranslationPlan",
    "TranslationUnit",
    "ValidationError",
]
