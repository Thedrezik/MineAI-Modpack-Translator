"""Shared Markdown guide classification and locale-path helpers."""

from __future__ import annotations

import re

from mineai.constants import MD_PATH_MARKERS


_GUIDEME_ROOT = "/ae2guide/"
_GUIDEME_LOCALE_DIR_RE = re.compile(r"^_?[a-z]{2}_[a-z]{2}$", re.IGNORECASE)
_GUIDEME_ROOT_RE = re.compile(r"(^|/)(ae2guide/)", re.IGNORECASE)
_SOURCE_LOCALE_RE = re.compile(r"/en_us/", re.IGNORECASE)


def _slash_path(path: str) -> str:
    return path.replace("\\", "/")


def _normalized(path: str) -> str:
    return "/" + _slash_path(path).lstrip("/").lower()


def is_source_markdown_guide(path: str) -> bool:
    """Return whether a JAR entry is a supported source-language MD/TXT guide."""
    normalized = _normalized(path)
    if not normalized.endswith((".md", ".txt")):
        return False

    # Preserve the project's existing /en_us/ guide support exactly.
    if "/en_us/" in normalized:
        return any(marker in normalized for marker in MD_PATH_MARKERS)

    # GuideME/AE2 stores its default-language pages directly below ae2guide,
    # while translations live below _<language_code>/.
    if _GUIDEME_ROOT not in normalized:
        return False

    tail = normalized.split(_GUIDEME_ROOT, 1)[1]
    first_segment = tail.split("/", 1)[0]
    return not _GUIDEME_LOCALE_DIR_RE.fullmatch(first_segment)


def get_markdown_target_path(path: str, target_code: str) -> str:
    """Resolve a supported source guide path to its target-locale path."""
    slash_path = _slash_path(path)
    if _SOURCE_LOCALE_RE.search(slash_path):
        return _SOURCE_LOCALE_RE.sub(
            f"/{target_code}/",
            slash_path,
            count=1,
        )

    if _GUIDEME_ROOT_RE.search(slash_path):
        return _GUIDEME_ROOT_RE.sub(
            lambda match: f"{match.group(1)}{match.group(2)}_{target_code}/",
            slash_path,
            count=1,
        )

    raise ValueError(f"Unsupported Markdown guide source path: {path}")


def is_target_markdown_locale_path(path: str, target_code: str) -> bool:
    """Return whether a path belongs to a GuideME target-locale subtree."""
    normalized = _normalized(path)
    return f"/_{target_code.lower()}/" in normalized
