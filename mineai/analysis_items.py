"""Stable identities for user-selectable analysis results."""

from __future__ import annotations

import os
from dataclasses import dataclass

from mineai.processors.loose_paths import is_loose_book_source


@dataclass(frozen=True)
class AnalysisItem:
    key: str
    path: str
    scope: str
    icon: str
    name: str
    kind: str
    translated: int
    total: int
    percent: int
    parent_key: str | None = None
    is_group: bool = False


SEGMENT_SEPARATOR = "|segment|"


def analysis_target_key(path: str, scope: str) -> str:
    normalized = os.path.normcase(os.path.abspath(path))
    return f"{scope}:{normalized}"


def analysis_segment_key(path: str, scope: str, segment: str) -> str:
    return f"{analysis_target_key(path, scope)}{SEGMENT_SEPARATOR}{segment}"


def build_analysis_item(
    path: str,
    scope: str,
    icon: str,
    name: str,
    kind: str,
    translated: int,
    total: int,
    percent: int,
    *,
    parent_key: str | None = None,
    is_group: bool = False,
    segment: str | None = None,
) -> AnalysisItem:
    return AnalysisItem(
        key=(
            analysis_segment_key(path, scope, segment)
            if segment is not None
            else analysis_target_key(path, scope)
        ),
        path=path,
        scope=scope,
        icon=icon,
        name=name,
        kind=kind,
        translated=translated,
        total=total,
        percent=percent,
        parent_key=parent_key,
        is_group=is_group,
    )


def target_is_selected(
    selected_items: frozenset[str] | None,
    path: str,
    scope: str,
) -> bool:
    if selected_items is None:
        return True
    target_key = analysis_target_key(path, scope)
    segment_prefix = f"{target_key}{SEGMENT_SEPARATOR}"
    return target_key in selected_items or any(
        key.startswith(segment_prefix) for key in selected_items
    )


def selected_segments_for_target(
    selected_items: frozenset[str] | None,
    path: str,
    scope: str,
) -> frozenset[str] | None:
    """Return selected child segments, or ``None`` when the whole target applies."""
    if selected_items is None:
        return None
    target_key = analysis_target_key(path, scope)
    if target_key in selected_items:
        return None
    prefix = f"{target_key}{SEGMENT_SEPARATOR}"
    return frozenset(
        key[len(prefix):]
        for key in selected_items
        if key.startswith(prefix)
    )


def loose_file_scope(path: str) -> str:
    normalized = os.path.abspath(path).replace("\\", "/").casefold()
    if is_loose_book_source(path):
        return "books"
    return "quests" if "/config/ftbquests/" in normalized else "mods"
