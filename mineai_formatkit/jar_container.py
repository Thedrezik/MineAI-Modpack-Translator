from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .registry import FormatRegistry


class JarSafetyError(ValueError):
    pass


class SignedJarError(JarSafetyError):
    pass


class DuplicateJarEntryError(JarSafetyError):
    pass


@dataclass(frozen=True)
class JarInspection:
    entries: int
    signed: bool
    signature_entries: tuple[str, ...]
    duplicate_entries: tuple[str, ...]
    candidate_entries: tuple[str, ...]
    nested_jar_entries: tuple[str, ...] = ()


@dataclass(frozen=True)
class NestedJarInspection:
    entry_path: str
    inspection: JarInspection


class JarContainer:
    SIGNATURE_SUFFIXES = (".SF", ".RSA", ".DSA", ".EC")

    def inspect(self, path: str | Path, registry: FormatRegistry | None = None) -> JarInspection:
        with zipfile.ZipFile(path, "r") as archive:
            return self._inspect_archive(archive, registry)

    def inspect_nested(
        self,
        path: str | Path,
        registry: FormatRegistry | None = None,
    ) -> tuple[NestedJarInspection, ...]:
        """Inspect one level of embedded JAR entries without mutating them.

        NeoForge/FML Jar-in-Jar bundles can hide real mod resources under
        ``META-INF/jarjar/*.jar`` or similar paths. Discovery is explicit and
        one-level only: the host still decides whether/how translated inner
        resources are emitted, and ``rebuild`` never rewrites nested archives.
        """

        found: list[NestedJarInspection] = []
        with zipfile.ZipFile(path, "r") as archive:
            for info in archive.infolist():
                if info.is_dir() or not info.filename.lower().endswith(".jar"):
                    continue
                data = archive.read(info)
                try:
                    with zipfile.ZipFile(io.BytesIO(data), "r") as nested:
                        inspection = self._inspect_archive(nested, registry)
                except zipfile.BadZipFile as exc:
                    raise JarSafetyError(
                        f"Nested JAR entry is not a valid ZIP archive: {info.filename}"
                    ) from exc
                found.append(NestedJarInspection(info.filename, inspection))
        return tuple(found)

    def _inspect_archive(
        self,
        archive: zipfile.ZipFile,
        registry: FormatRegistry | None,
    ) -> JarInspection:
        names = archive.namelist()
        counts: dict[str, int] = {}
        for name in names:
            counts[name] = counts.get(name, 0) + 1
        duplicates = tuple(sorted(name for name, count in counts.items() if count > 1))
        signatures = tuple(sorted(
            name for name in names
            if name.upper().startswith("META-INF/")
            and name.upper().endswith(self.SIGNATURE_SUFFIXES)
        ))
        translatable: list[str] = []
        if registry is not None:
            for name in names:
                if registry.detect(name) is not None:
                    translatable.append(name)
        nested_entries = tuple(sorted(
            name for name in names
            if not name.endswith("/") and name.lower().endswith(".jar")
        ))
        return JarInspection(
            entries=len(names),
            signed=bool(signatures),
            signature_entries=signatures,
            duplicate_entries=duplicates,
            candidate_entries=tuple(translatable),
            nested_jar_entries=nested_entries,
        )

    def rebuild(
        self,
        source: str | Path,
        destination: str | Path,
        replacements: Mapping[str, bytes],
    ) -> Path:
        inspection = self.inspect(source)
        if inspection.duplicate_entries:
            raise DuplicateJarEntryError(
                f"JAR contains duplicate entries: {inspection.duplicate_entries!r}"
            )
        if inspection.signed and replacements:
            raise SignedJarError(
                "Refusing to rebuild a signed JAR; emit a resource-pack overlay instead"
            )

        source = Path(source)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(source, "r") as zin, zipfile.ZipFile(destination, "w") as zout:
            existing = set()
            for info in zin.infolist():
                existing.add(info.filename)
                data = replacements.get(info.filename, zin.read(info))
                zout.writestr(info, data)
            for name, data in replacements.items():
                if name in existing:
                    continue
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                zout.writestr(info, data)

        with zipfile.ZipFile(destination, "r") as check:
            bad = check.testzip()
            if bad is not None:
                destination.unlink(missing_ok=True)
                raise zipfile.BadZipFile(f"CRC check failed for {bad}")
        return destination

    def copy_as_resource_pack(
        self,
        replacements: Mapping[str, bytes],
        root: str | Path,
    ) -> tuple[Path, ...]:
        root = Path(root)
        written: list[Path] = []
        for name, data in replacements.items():
            target = root / Path(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            written.append(target)
        return tuple(written)
