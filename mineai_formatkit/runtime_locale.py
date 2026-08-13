from __future__ import annotations

import re
from collections import Counter

from .core import ProtectedFragment
from .locale_merge import LocaleMergePlanner as _BaseLocaleMergePlanner
from .minecraft_lang import (
    MinecraftLangJsonAdapter as _BaseMinecraftLangJsonAdapter,
    _FORMAT_RE,
    _LINE_BREAK_RE,
    _MESSAGE_FORMAT_RE,
    _MINECRAFT_FORMAT_RE,
    _PLACEHOLDER_RE,
)


_DOUBLE_DOLLAR_VAR_RE = re.compile(r"\$\$[A-Za-z_][A-Za-z0-9_]*")
_KEYBIND_TOKEN_RE = re.compile(r"%kkey\.[A-Za-z0-9_.:-]+%")
_URL_RE = re.compile(
    r"https?://[A-Za-z0-9][^\s\"'<>()[\]{},;!?]*",
    re.IGNORECASE,
)
_SLASH_COMMAND_CORE_RE = re.compile(
    r"/[A-Za-z][A-Za-z0-9_:-]*(?=$|[\s,.;:!?)}\]>\"'])"
)
_MC_FORMAT_CODE_RE = re.compile(r"§[0-9A-FK-ORa-fk-or]")


def _slash_command_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for match in _SLASH_COMMAND_CORE_RE.finditer(text):
        start = match.start()
        if start == 0:
            spans.append((start, match.end()))
            continue
        previous = text[start - 1]
        # Commands are token-like. A slash immediately following any Unicode
        # alphanumeric character is part of prose/path-like text, not a command
        # boundary (real FancyMenu RU: ``стрелками/Tab`` and
        # ``Документация/Wiki``). Keep the historical resource/path guards too.
        if not (previous.isalnum() or previous in "_./:-"):
            spans.append((start, match.end()))
            continue
        # A real command may immediately follow a Minecraft formatting code,
        # e.g. ``§a/create``. The formatting code itself remains protected too.
        if start >= 2 and text[start - 2] == "§" and _MC_FORMAT_CODE_RE.fullmatch(text[start - 2 : start]):
            spans.append((start, match.end()))
    return spans


def _extra_runtime_spans(text: str, *, include_urls: bool) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for regex in (_DOUBLE_DOLLAR_VAR_RE, _KEYBIND_TOKEN_RE):
        spans.extend((match.start(), match.end()) for match in regex.finditer(text))
    spans.extend(_slash_command_spans(text))
    if include_urls:
        spans.extend((match.start(), match.end()) for match in _URL_RE.finditer(text))
    return spans


def _critical_runtime_tokens(text: str) -> list[str]:
    tokens = [match.group(0) for match in _DOUBLE_DOLLAR_VAR_RE.finditer(text)]
    tokens.extend(match.group(0) for match in _KEYBIND_TOKEN_RE.finditer(text))
    tokens.extend(text[start:end] for start, end in _slash_command_spans(text))
    return tokens


class MinecraftLangJsonAdapter(_BaseMinecraftLangJsonAdapter):
    """Minecraft locale adapter with corpus-proven runtime-token protection.

    This remains the public ``minecraft-lang-json`` adapter. The reviewed base
    parser/reconstructor is unchanged; only the protection contract is extended
    for syntax proven by real mods: FancyMenu ``$$variables``, Hexerei
    ``%kkey...%`` keybinds, URLs, and boundary-safe slash commands.
    """

    name = "minecraft-lang-json"

    def _protect(self, text: str) -> tuple[str, tuple[ProtectedFragment, ...]]:
        spans: list[tuple[int, int]] = []
        literal_ids = [int(match.group(1)) for match in _PLACEHOLDER_RE.finditer(text)]
        spans.extend((match.start(), match.end()) for match in _PLACEHOLDER_RE.finditer(text))
        for regex in (
            _FORMAT_RE,
            _MINECRAFT_FORMAT_RE,
            _MESSAGE_FORMAT_RE,
            _LINE_BREAK_RE,
        ):
            spans.extend((match.start(), match.end()) for match in regex.finditer(text))
        spans.extend(_extra_runtime_spans(text, include_urls=True))

        merged = self._merge_spans(spans)
        protected: list[ProtectedFragment] = []
        out: list[str] = []
        cursor = 0
        base_id = max(literal_ids) + 1 if literal_ids else 0
        for offset, (start, end) in enumerate(merged):
            out.append(text[cursor:start])
            placeholder = f"[#{base_id + offset}#]"
            out.append(placeholder)
            protected.append(ProtectedFragment(placeholder, text[start:end]))
            cursor = end
        out.append(text[cursor:])
        return "".join(out), tuple(protected)


class LocaleMergePlanner(_BaseLocaleMergePlanner):
    """Locale merge planner that rejects damaged runtime-critical target syntax."""

    def __init__(self, adapter: _BaseMinecraftLangJsonAdapter | None = None) -> None:
        super().__init__(adapter or MinecraftLangJsonAdapter())

    @staticmethod
    def _critical_placeholders(text: str) -> Counter[str]:
        critical = _BaseLocaleMergePlanner._critical_placeholders(text)
        critical.update(_critical_runtime_tokens(text))
        return critical
