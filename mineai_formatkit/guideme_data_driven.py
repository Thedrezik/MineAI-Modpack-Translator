from __future__ import annotations

import re

from .guideme import GuideMeMarkdownAdapter


# GuideME's documented default content root is:
#   guides/<guide-id-namespace>/<guide-id-path>
# This adapter intentionally supports the corpus-proven one-segment guide-id path
# shape. More complex roots need host configuration rather than path guessing.
_DATA_DRIVEN_GUIDE_RE = re.compile(
    r"(^|/)(assets/[^/]+/guides/[^/]+/[^/]+/)(.+\.md)$",
    re.IGNORECASE,
)
_LOCALE_PREFIX_RE = re.compile(r"^_[a-z]{2}_[a-z]{2}/", re.IGNORECASE)


class DataDrivenGuideMeMarkdownAdapter(GuideMeMarkdownAdapter):
    """GuideME Markdown for documented data-driven ``guides/<id>/`` trees."""

    name = "guideme-data-driven-markdown"

    def matches(self, path: str) -> bool:
        slash = "/" + path.replace("\\", "/").lstrip("/")
        match = _DATA_DRIVEN_GUIDE_RE.search(slash)
        if not match:
            return False
        return not _LOCALE_PREFIX_RE.match(match.group(3))

    def target_path(self, path: str, target_code: str) -> str:
        slash = path.replace("\\", "/")
        normalized = "/" + slash.lstrip("/")
        match = _DATA_DRIVEN_GUIDE_RE.search(normalized)
        if not match or _LOCALE_PREFIX_RE.match(match.group(3)):
            raise ValueError(f"Unsupported data-driven GuideME source path: {path}")

        root = match.group(2)
        suffix = match.group(3)
        prefix_len = 1 if not slash.startswith("/") else 0
        root_start = match.start(2) - prefix_len
        return slash[:root_start] + root + f"_{target_code}/" + suffix
