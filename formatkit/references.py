"""Resolve non-text resources after a localized document is relocated."""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable


_ATTRIBUTE_REFERENCE = re.compile(
    r"\b(?:src|href)\s*=\s*(['\"])(?P<value>.*?)\1",
    re.IGNORECASE,
)
_MARKDOWN_REFERENCE = re.compile(
    r"!?\[[^\]]*\]\(\s*(?P<value><[^>]+>|[^\s)]+)",
)
_NON_COPYABLE_EXTENSIONS = frozenset({".json", ".md", ".markdown", ".txt"})
_NAMESPACED_REFERENCE = re.compile(r"^[a-z0-9_.-]+:", re.IGNORECASE)


def _reference_values(text: str) -> Iterable[str]:
    for pattern in (_ATTRIBUTE_REFERENCE, _MARKDOWN_REFERENCE):
        for match in pattern.finditer(text):
            yield match.group("value")


def _clean_reference(value: str) -> str | None:
    candidate = value.strip().strip("<>").replace("\\", "/")
    if not candidate or candidate.startswith(("#", "/")):
        return None
    if "://" in candidate or _NAMESPACED_REFERENCE.match(candidate):
        return None
    candidate = candidate.split("#", 1)[0].split("?", 1)[0]
    return candidate or None


def relocated_dependencies(
    source_path: str,
    target_path: str,
    text: str,
    available_paths: Iterable[str],
) -> tuple[tuple[str, str], ...]:
    """Return existing source assets that must follow a relocated document."""
    source_normalized = source_path.replace("\\", "/")
    target_normalized = target_path.replace("\\", "/")
    available = {
        path.replace("\\", "/").casefold(): path.replace("\\", "/")
        for path in available_paths
    }
    resolved: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for raw_reference in _reference_values(text):
        reference = _clean_reference(raw_reference)
        if reference is None:
            continue
        extension = posixpath.splitext(reference)[1].casefold()
        if extension in _NON_COPYABLE_EXTENSIONS:
            continue
        source_dependency = posixpath.normpath(
            posixpath.join(posixpath.dirname(source_normalized), reference)
        )
        target_dependency = posixpath.normpath(
            posixpath.join(posixpath.dirname(target_normalized), reference)
        )
        actual_source = available.get(source_dependency.casefold())
        pair = (actual_source or "", target_dependency)
        if actual_source is None or source_dependency == target_dependency:
            continue
        if pair in seen:
            continue
        seen.add(pair)
        resolved.append(pair)
    return tuple(resolved)
