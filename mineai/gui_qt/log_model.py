"""Pure semantic model for the high-volume Qt journal."""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class LogSegment:
    text: str
    color: str


@dataclass(frozen=True)
class LogEntry:
    plain_text: str
    level: str
    category: str
    segments: tuple[LogSegment, ...]


@dataclass(frozen=True)
class TranslationParts:
    left: str
    separator: str
    right: str
    suffix: str = ""


def split_translation_message(message: str) -> TranslationParts | None:
    """Split the standard successful translation log without altering raw text."""
    separator = " -> " if " -> " in message else (" → " if " → " in message else "")
    if not separator:
        return None
    left, _sep, right = message.partition(separator)
    if not left or not right:
        return None
    suffix = ""
    match = re.search(r"( ×\d+)$", right)
    if match:
        suffix = match.group(1)
        right = right[: -len(suffix)]
    return TranslationParts(left=left, separator=separator, right=right, suffix=suffix)


def level_from_tag(tag: str) -> str:
    if tag == "red":
        return "error"
    if tag in {"yellow", "gold", "orange"}:
        return "warning"
    if tag in {"green", "lime"}:
        return "success"
    return "info"


def classify_message(level: str, message: str) -> str:
    """Map runtime log text to user-facing journal categories.

    Runtime colors are intentionally not treated as semantic categories: yellow is
    also used for normal progress messages, so only an actual error level or
    issue-specific wording belongs in the errors/skips filter.
    """
    text = message.casefold()

    translated_markers = (" -> ", " → ")
    if any(marker in message for marker in translated_markers) or message.lstrip().startswith(">"):
        return "translated"

    issue_markers = (
        "ошиб",
        "пропуск",
        "пропущ",
        "не удалось",
        "отклон",
        "failed",
        "failure",
        "error",
        "skip",
        "skipped",
        "rejected",
        "timeout",
    )
    if level == "error" or any(marker in text for marker in issue_markers):
        return "issues"

    analysis_markers = (
        "анализ",
        "сканир",
        "найдено",
        "найден ",
        "modpack",
        "scan ",
        "scanning",
        "analysis",
        "found ",
    )
    if any(marker in text for marker in analysis_markers):
        return "analysis"

    return "other"


def entry_from_message(tag: str, message: str, color: str) -> LogEntry:
    level = level_from_tag(tag)
    return LogEntry(
        plain_text=message,
        level=level,
        category=classify_message(level, message),
        segments=(LogSegment(message, color),),
    )


def matches_entry(entry: LogEntry, filter_key: str, query: str = "") -> bool:
    if filter_key != "all" and entry.category != filter_key:
        return False
    needle = query.strip().casefold()
    return not needle or needle in entry.plain_text.casefold()
