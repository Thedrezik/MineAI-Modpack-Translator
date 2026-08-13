"""Detect source and target paths for Markdown guide books inside mod JARs."""

from collections import Counter, defaultdict
import re
import zipfile


_LOCALE_DIRECTORY = re.compile(r"^_?[a-z]{2}_[a-z]{2}$", re.IGNORECASE)
_BOOK_DIRECTORY_HINTS = (
    "guide",
    "manual",
    "lexicon",
    "handbook",
    "codex",
    "wiki",
)
_BOOK_DIRECTORY_NAMES = frozenset({"book", "books", "patchouli_books"})
_NON_BOOK_ASSET_DIRECTORIES = frozenset({
    "blockstates",
    "lang",
    "models",
    "shaders",
    "sounds",
    "textures",
})
_MARKDOWN_EXTENSIONS = (".md", ".markdown", ".txt")
_EXPLICIT_LOCALE_EXTENSIONS = (".lang", ".xml")
_SOURCE_JSON_LOCALE = re.compile(r"/en_us/", re.IGNORECASE)
_LEGACY_LANG_SOURCE = re.compile(r"(?i)(?<=/)en_us(?=\.lang$)")
_SHORTHAND_LANG_JSON = re.compile(
    r"(?i)^(?:data/)?[a-z0-9_.-]+/lang/en_us\.json$"
)


def _normalized_parts(path: str) -> list[str]:
    return path.replace("\\", "/").strip("/").split("/")


def _book_root_index(parts: list[str]) -> int | None:
    if len(parts) < 4 or parts[0].casefold() != "assets":
        return None
    for index in range(2, len(parts) - 1):
        directory = parts[index].casefold()
        if directory in _NON_BOOK_ASSET_DIRECTORIES:
            return None
        if (
            any(hint in directory for hint in _BOOK_DIRECTORY_HINTS)
            or directory in _BOOK_DIRECTORY_NAMES
            or directory.endswith(("_book", "_books"))
        ):
            return index
    return None


def localized_json_target_path(
    source_path: str,
    target_code: str,
) -> str | None:
    """Map any explicitly localized JSON document without framework names."""
    normalized = source_path.replace("\\", "/").strip("/")
    lower_path = normalized.casefold()
    is_asset_locale = lower_path.startswith("assets/")
    is_patchouli_datapack = (
        lower_path.startswith("data/")
        and "/patchouli_books/" in "/" + lower_path
    )
    if not (is_asset_locale or is_patchouli_datapack):
        return None
    if not lower_path.endswith(".json"):
        return None
    if _SOURCE_JSON_LOCALE.search("/" + normalized) is None:
        return None
    return re.sub(
        r"(?i)(?<=/)en_us(?=/)",
        target_code.casefold(),
        normalized,
        count=1,
    )


def legacy_lang_target_path(source_path: str, target_code: str) -> str | None:
    """Map legacy Forge ``assets/<mod>/lang[s]/en_us.lang`` resources."""
    normalized = source_path.replace("\\", "/").strip("/")
    parts = normalized.split("/")
    if len(parts) < 4 or parts[0].casefold() != "assets":
        return None
    if parts[-2].casefold() not in {"lang", "langs"}:
        return None
    if _LEGACY_LANG_SOURCE.search("/" + normalized) is None:
        return None
    return _LEGACY_LANG_SOURCE.sub(target_code.casefold(), normalized, count=1)


def minecraft_lang_json_target_path(
    source_path: str,
    target_code: str,
) -> str | None:
    """Map JSON locales that can be represented by a Minecraft resource pack."""
    normalized = source_path.replace("\\", "/").strip("/")
    lower_path = normalized.casefold()
    has_asset_root = (
        lower_path.startswith("assets/")
        or "/assets/" in lower_path
    )
    if not has_asset_root and _SHORTHAND_LANG_JSON.fullmatch(normalized) is None:
        return None
    if not lower_path.endswith("/en_us.json"):
        return None
    return re.sub(
        r"(?i)en_us\.json$",
        f"{target_code.casefold()}.json",
        normalized,
        count=1,
    )


class MarkdownBookLocator:
    """Resolve localized paths without relying on a particular mod id."""

    def __init__(self, archive_paths: list[str], target_code: str) -> None:
        self.target_code = target_code.casefold()
        self._styles: dict[str, Counter[str]] = defaultdict(Counter)
        self._target_styles: dict[str, set[str]] = defaultdict(set)

        for path in archive_paths:
            parts = _normalized_parts(path)
            root_index = _book_root_index(parts)
            if root_index is None:
                continue
            root_key = "/".join(parts[: root_index + 1]).casefold()
            for directory in parts[root_index + 1 : -1]:
                locale = directory.casefold()
                if not _LOCALE_DIRECTORY.fullmatch(locale):
                    continue
                code = locale.removeprefix("_")
                if code == "en_us":
                    break
                style = "_" if locale.startswith("_") else ""
                self._styles[root_key][style] += 1
                if code == self.target_code:
                    self._target_styles[root_key].add(style)
                break

    @classmethod
    def from_archives(
        cls,
        archive_files: list[str],
        target_code: str,
    ) -> "MarkdownBookLocator":
        """Build one locale map for books extended by several mod JARs."""
        logical_paths: list[str] = []
        for archive_file in archive_files:
            try:
                with zipfile.ZipFile(archive_file, "r") as archive:
                    logical_paths.extend(archive.namelist())
            except (OSError, zipfile.BadZipFile):
                continue
        return cls(logical_paths, target_code)

    def target_path(self, source_path: str) -> str | None:
        normalized = source_path.replace("\\", "/").strip("/")
        if normalized.casefold().endswith(_EXPLICIT_LOCALE_EXTENSIONS):
            parts = normalized.split("/")
            if len(parts) < 4 or parts[0].casefold() != "assets":
                return None
            for index, directory in enumerate(parts[:-1]):
                if directory.casefold() == "en_us":
                    target_parts = parts.copy()
                    target_parts[index] = self.target_code
                    return "/".join(target_parts)
            return None
        if not normalized.casefold().endswith(_MARKDOWN_EXTENSIONS):
            return None

        parts = normalized.split("/")
        if len(parts) >= 4 and parts[0].casefold() == "assets":
            for index, directory in enumerate(parts[:-1]):
                if directory.casefold() == "en_us":
                    target_parts = parts.copy()
                    target_parts[index] = self.target_code
                    return "/".join(target_parts)

        root_index = _book_root_index(parts)
        if root_index is None:
            return None

        for index in range(root_index + 1, len(parts) - 1):
            locale = parts[index].casefold()
            if not _LOCALE_DIRECTORY.fullmatch(locale):
                continue
            if locale.removeprefix("_") != "en_us":
                return None
            prefix = "_" if locale.startswith("_") else ""
            target_parts = parts.copy()
            target_parts[index] = prefix + self.target_code
            return "/".join(target_parts)

        root_key = "/".join(parts[: root_index + 1]).casefold()
        style = self._preferred_style(root_key)
        target_parts = parts.copy()
        target_parts.insert(root_index + 1, style + self.target_code)
        return "/".join(target_parts)

    def _preferred_style(self, root_key: str) -> str:
        if root_key.rsplit("/", 1)[-1] == "ae2guide":
            return "_"
        target_styles = self._target_styles.get(root_key, set())
        if "_" in target_styles:
            return "_"
        if "" in target_styles:
            return ""
        styles = self._styles.get(root_key, Counter())
        return "_" if styles["_"] > styles[""] else ""
