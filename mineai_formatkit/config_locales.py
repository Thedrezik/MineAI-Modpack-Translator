from __future__ import annotations

import re

from .locale_safe import MinecraftLangJsonAdapter


_COLLAPSIBLE_CONFIG_RE = re.compile(
    r"(^|/)config/collapsiblegroups/lang/en_us\.json$",
    re.IGNORECASE,
)
_JAOPCA_CONFIG_RE = re.compile(
    r"(^|/)config/jaopca/lang/en_us\.json$",
    re.IGNORECASE,
)


class _ConfigLocaleAdapter(MinecraftLangJsonAdapter):
    source_pattern: re.Pattern[str]

    def matches(self, path: str) -> bool:
        slash = "/" + path.replace("\\", "/").lstrip("/")
        return bool(self.source_pattern.search(slash))

    def target_path(self, path: str, target_code: str) -> str:
        slash = path.replace("\\", "/")
        if not self.matches(slash):
            raise ValueError(f"Unsupported runtime config locale source path: {path}")
        return re.sub(r"en_us\.json$", f"{target_code}.json", slash, flags=re.IGNORECASE)


class CollapsibleGroupsConfigLangJsonAdapter(_ConfigLocaleAdapter):
    """Runtime locale deployed/read from ``config/collapsiblegroups/lang``."""

    name = "collapsible-groups-config-lang-json"
    source_pattern = _COLLAPSIBLE_CONFIG_RE


class JaopcaConfigLangJsonAdapter(_ConfigLocaleAdapter):
    """JAOPCA's runtime/downloader locale catalog under ``config/jaopca/lang``."""

    name = "jaopca-config-lang-json"
    source_pattern = _JAOPCA_CONFIG_RE
