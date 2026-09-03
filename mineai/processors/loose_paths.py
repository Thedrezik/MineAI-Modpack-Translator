"""Path rules for unpacked Minecraft localization resources."""

from __future__ import annotations

import os
import re


_LOCALE_SEGMENT = re.compile(r"(?i)(?<=[\\/])en_us(?=[\\/])")
_SOURCE_LOCALE_FILE = re.compile(r"(?i)en_us\.(json|lang)$")
_BOOK_MARKERS = (
    "/book/",
    "/books/",
    "/guide/",
    "/guides/",
    "/manual/",
    "/manuals/",
    "/lexicon/",
    "/patchouli_books/",
    "/moddocs/",
    "/projectintelligence/",
)
_DOCUMENT_EXTENSIONS = (".json", ".md", ".markdown", ".txt", ".xml", ".lang")


def normalized_loose_path(path: str) -> str:
    return os.path.abspath(path).replace("\\", "/")


def is_loose_book_source(path: str) -> bool:
    normalized = "/" + normalized_loose_path(path).strip("/")
    lower = normalized.casefold()
    has_locale = (
        "/en_us/" in lower
        or (
            "/projectintelligence/" in lower
            and lower.endswith("/lang/en_us.json")
        )
    )
    return (
        has_locale
        and lower.endswith(_DOCUMENT_EXTENSIONS)
        and any(marker in lower for marker in _BOOK_MARKERS)
    )


def loose_target_disk_path(path: str, target_code: str) -> str:
    if _LOCALE_SEGMENT.search(path):
        return _LOCALE_SEGMENT.sub(target_code.casefold(), path, count=1)
    match = _SOURCE_LOCALE_FILE.search(path)
    if match is None:
        return path
    return (
        path[: match.start()]
        + f"{target_code.casefold()}.{match.group(1).casefold()}"
    )


def loose_pack_target_path(
    path: str,
    mc_dir: str,
    target_code: str,
) -> str | None:
    """Return a resource/data-pack path, or ``None`` for direct config files."""
    rel = os.path.relpath(path, mc_dir).replace("\\", "/").strip("/")
    target_rel = loose_target_disk_path(rel, target_code).replace("\\", "/")
    parts = target_rel.split("/")
    folded = [part.casefold() for part in parts]

    for root_name in ("assets", "data"):
        if root_name in folded:
            return "/".join(parts[folded.index(root_name) :])

    # Some config mods expose namespace/lang directly. Deeper config trees are
    # private on-disk formats and must stay beside their source document.
    if (
        len(parts) == 4
        and folded[0] == "config"
        and folded[2] in {"lang", "langs"}
    ):
        return f"assets/{parts[1]}/{parts[2]}/{parts[3]}"
    return None
