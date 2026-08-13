import os
import re


_SOURCE_LOCALE_FILENAME = re.compile(r"en_us\.json$", re.IGNORECASE)


def target_locale_path(path: str, target_filename: str) -> str:
    """Replace only the trailing en_us.json locale filename, case-insensitively."""
    return _SOURCE_LOCALE_FILENAME.sub(target_filename, path, count=1)


def normalized_absolute_path(path: str) -> str:
    """Normalize an absolute path for safe source/target comparisons."""
    return os.path.normcase(os.path.abspath(path))


def ensure_distinct_paths(source_path: str, target_path: str) -> None:
    """Fail closed instead of ever allowing a target to overwrite its source."""
    if normalized_absolute_path(source_path) == normalized_absolute_path(target_path):
        raise RuntimeError(
            "Refusing to overwrite source locale file: "
            f"source and target resolve to the same path ({source_path})"
        )
