"""Shared lexical patterns for immutable Minecraft formatting tokens."""

from __future__ import annotations

import re


MODONOMICON_STYLE_SOURCE = (
    r"\[\s*#\s*\]\s*\(\s*(?:[0-9a-f]{6})?\s*\)"
)

STYLE_RESET_BOUNDARY_SOURCE = (
    r"[§&]r(?:[^\w\r\n<§&]*[ \t]+|[^\w\s\r\n<§&]+)"
)

GAME_TOKEN_SOURCE = (
    r"(?:"
    + MODONOMICON_STYLE_SOURCE
    + r"|"
    + STYLE_RESET_BOUNDARY_SOURCE
    + r"|"
    r"\$\([^\r\n)]*\)|"
    r"[§&]x(?:[§&][0-9a-f]){6}|"
    r"&#[0-9a-f]{6}|"
    r"[§&][0-9a-fk-or]|"
    r"%[0-9.,]*\$?[a-zA-Z%]|"
    r"\{[A-Za-z_][A-Za-z0-9_.:-]*\}|"
    r"#[A-Za-z_][A-Za-z0-9_.:/-]*#|"
    r"\\[nrt]"
    r")"
)

GAME_TOKEN_PATTERN = re.compile(GAME_TOKEN_SOURCE, re.IGNORECASE)


def game_tokens(text: str) -> tuple[str, ...]:
    return tuple(match.group(0) for match in GAME_TOKEN_PATTERN.finditer(text))
