"""Read-only FormatKit validation across PrismLauncher instances."""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
import re
import sys
from zipfile import BadZipFile, ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from formatkit import FormatRegistry
from formatkit import relocated_dependencies
from formatkit.contracts import ANCHOR_PATTERN
from mineai.json_utils import load_lenient_json
from mineai.processors.book_paths import (
    MarkdownBookLocator,
    localized_json_target_path,
)
from mineai.processors.selection import collect_book_json_selection


@dataclass
class Result:
    jars: int = 0
    files: int = 0
    units: int = 0
    json_files: int = 0
    json_units: int = 0
    dependencies: set[tuple[str, str, str]] = field(default_factory=set)
    adapters: Counter[str] = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)

    def add(self, other: "Result") -> None:
        self.jars += other.jars
        self.files += other.files
        self.units += other.units
        self.json_files += other.json_files
        self.json_units += other.json_units
        self.dependencies.update(other.dependencies)
        self.adapters.update(other.adapters)
        self.errors.extend(other.errors)


def synthetic_translation(payload: str) -> str:
    return "".join(
        part
        if ANCHOR_PATTERN.fullmatch(part)
        else re.sub(r"[A-Za-z]+", "текст", part)
        for part in re.split(f"({ANCHOR_PATTERN.pattern})", payload)
    )


def validate_jar(path: Path, target_locale: str) -> Result:
    result = Result(jars=1)
    registry = FormatRegistry.default()
    try:
        with ZipFile(path) as archive:
            names = archive.namelist()
            locator = MarkdownBookLocator(names, target_locale)
            for logical_path in names:
                json_target = localized_json_target_path(
                    logical_path,
                    target_locale,
                )
                if json_target is not None:
                    result.json_files += 1
                    try:
                        data = load_lenient_json(archive.read(logical_path))
                        source_map, _preserved, _pending = (
                            collect_book_json_selection(data, {}, "force")
                        )
                        result.json_units += len(source_map)
                    except Exception as exc:  # noqa: BLE001 - corpus audit
                        result.errors.append(
                            f"{path} :: {logical_path} :: "
                            f"{type(exc).__name__}: {exc}"
                        )
                target_hint = locator.target_path(logical_path)
                if target_hint is None:
                    continue
                result.files += 1
                try:
                    source = archive.read(logical_path).decode(
                        "utf-8-sig",
                        errors="ignore",
                    )
                    plan = registry.plan(
                        logical_path,
                        source,
                        target_locale,
                        target_path_hint=target_hint,
                    )
                    if plan.apply({}).text != source:
                        raise AssertionError("no-op round trip changed bytes")
                    translations = {
                        unit.id: synthetic_translation(unit.payload)
                        for unit in plan.units
                    }
                    plan.apply(translations)
                    result.units += len(plan.units)
                    result.adapters[plan.adapter_id] += 1
                    if logical_path.casefold().endswith(
                        (".md", ".markdown", ".txt")
                    ):
                        result.dependencies.update(
                            (str(path), dependency_source, dependency_target)
                            for dependency_source, dependency_target in (
                                relocated_dependencies(
                                    logical_path,
                                    plan.target_path or target_hint,
                                    source,
                                    names,
                                )
                            )
                        )
                except Exception as exc:  # noqa: BLE001 - audit must collect all files
                    result.errors.append(
                        f"{path} :: {logical_path} :: "
                        f"{type(exc).__name__}: {exc}"
                    )
    except (BadZipFile, OSError) as exc:
        result.errors.append(f"{path} :: {type(exc).__name__}: {exc}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("instances", type=Path)
    parser.add_argument("--target-locale", default="ru_ru")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    if (args.instances / "mods").is_dir():
        jars = sorted((args.instances / "mods").glob("*.jar"))
    elif (args.instances / "minecraft" / "mods").is_dir():
        jars = sorted((args.instances / "minecraft" / "mods").glob("*.jar"))
    else:
        jars = sorted(args.instances.glob("*/minecraft/mods/*.jar"))
    total = Result()
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        for result in executor.map(
            lambda jar: validate_jar(jar, args.target_locale),
            jars,
        ):
            total.add(result)

    print(f"JAR: {total.jars}")
    print(f"Format files: {total.files}")
    print(f"Translation units: {total.units}")
    print(f"Localized JSON files: {total.json_files}")
    print(f"Localized JSON units: {total.json_units}")
    print(f"Relocated dependencies: {len(total.dependencies)}")
    print(f"Adapters: {dict(sorted(total.adapters.items()))}")
    print(f"Errors: {len(total.errors)}")
    for error in total.errors:
        print(error)
    return 1 if total.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
