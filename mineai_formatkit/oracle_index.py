from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Mapping

from .core import TranslationPlan, TranslationUnit, ValidationError
from .guideme_safe import GuideMeMarkdownAdapter
from .minecraft_text import MinecraftTextComponentAdapter


_BOOK_ROOT_RE = re.compile(r"(^|/)(oracle_index/books/[^/]+)(/.*)$", re.IGNORECASE)
_MDX_RE = re.compile(r"(^|/)oracle_index/books/[^/]+/.+\.mdx$", re.IGNORECASE)
_META_RE = re.compile(r"(^|/)oracle_index/books/[^/]+/.*/?_meta\.json$", re.IGNORECASE)
_LOCALIZED_TREE_RE = re.compile(
    r"(^|/)oracle_index/books/[^/]+/(?:\.translated|translated)/",
    re.IGNORECASE,
)


def _target_path(path: str, target_code: str) -> str:
    slash = path.replace("\\", "/")
    normalized = "/" + slash.lstrip("/")
    match = _BOOK_ROOT_RE.search(normalized)
    if not match:
        raise ValueError(f"Unsupported Oracle Index path: {path}")
    root = match.group(2)
    suffix = match.group(3)
    prefix = slash[: slash.lower().find(root.lower())]
    return f"{prefix}{root}/.translated/{target_code}{suffix}"


class OracleIndexMdxAdapter(GuideMeMarkdownAdapter):
    """Span-preserving ModdedMC.wiki/Oracle Index MDX adapter."""

    name = "oracle-index-mdx"

    def matches(self, path: str) -> bool:
        slash = "/" + path.replace("\\", "/").lstrip("/")
        return not _LOCALIZED_TREE_RE.search(slash) and bool(_MDX_RE.search(slash))

    def target_path(self, path: str, target_code: str) -> str:
        if not self.matches(path):
            raise ValueError(f"Unsupported Oracle Index MDX source path: {path}")
        return _target_path(path, target_code)


@dataclass(frozen=True)
class OracleMetaFingerprint:
    locators: tuple[str, ...]
    skeleton: str


class OracleIndexMetaJsonAdapter:
    """Translate Oracle Index navigation labels while keeping metadata immutable.

    Oracle Index corpora use both the legacy ``"path": "Label"`` shape and a
    newer ``"path": {"name": "Label", ...}`` shape. Only the top-level
    string value or nested ``name`` string is translatable. File keys, icons and
    any other metadata remain byte-for-byte structural data.
    """

    name = "oracle-index-meta-json"

    def matches(self, path: str) -> bool:
        slash = "/" + path.replace("\\", "/").lstrip("/")
        return not _LOCALIZED_TREE_RE.search(slash) and bool(_META_RE.search(slash))

    def target_path(self, path: str, target_code: str) -> str:
        if not self.matches(path):
            raise ValueError(f"Unsupported Oracle Index metadata source path: {path}")
        return _target_path(path, target_code)

    def prepare(self, path: str, source_text: str) -> TranslationPlan:
        parser, targets = self._targets(source_text)
        units: list[TranslationUnit] = []
        originals: dict[str, str] = {}
        for locator, node, value in targets:
            if not parser._has_prose(value):
                continue
            masked, protected = parser._protect(value)
            unit_id = f"oracle-meta:{locator}"
            units.append(
                TranslationUnit(
                    id=unit_id,
                    text=masked,
                    start=node.start,
                    end=node.end,
                    kind="oracle-index-meta-label",
                    context=locator,
                    protected=protected,
                )
            )
            originals[unit_id] = value
        return TranslationPlan(
            path=path,
            source_text=source_text,
            units=tuple(units),
            metadata={
                "fingerprint": self.fingerprint(source_text),
                "originals": originals,
            },
        )

    def apply(self, plan: TranslationPlan, translations: Mapping[str, str]) -> str:
        known = {unit.id for unit in plan.units}
        unknown = set(translations) - known
        if unknown:
            raise ValidationError(f"Unknown translation unit ids: {sorted(unknown)!r}")
        originals = plan.metadata.get("originals")
        if not isinstance(originals, dict):
            raise ValidationError("Oracle metadata plan is missing original labels")

        replacements: list[tuple[int, int, str]] = []
        parser = MinecraftTextComponentAdapter()
        for unit in plan.units:
            if unit.id not in translations:
                continue
            restored = parser._restore(unit, translations[unit.id])
            if restored == originals.get(unit.id):
                token = plan.source_text[unit.start : unit.end]
            else:
                token = json.dumps(restored, ensure_ascii=False)
            replacements.append((unit.start, unit.end, token))

        output = plan.source_text
        for start, end, token in sorted(replacements, reverse=True):
            output = output[:start] + token + output[end:]
        self.validate(plan.source_text, output)
        return output

    def validate(self, source_text: str, output_text: str) -> None:
        if self.fingerprint(source_text) != self.fingerprint(output_text):
            raise ValidationError("Oracle Index metadata structure changed")

    def fingerprint(self, text: str) -> OracleMetaFingerprint:
        parser, targets = self._targets(text)
        prose_targets = [
            (locator, node)
            for locator, node, value in targets
            if parser._has_prose(value)
        ]
        out: list[str] = []
        cursor = 0
        for locator, node in sorted(prose_targets, key=lambda item: item[1].start):
            out.append(text[cursor : node.start])
            out.append('"<mineai-oracle-meta-label>"')
            cursor = node.end
        out.append(text[cursor:])
        return OracleMetaFingerprint(
            locators=tuple(locator for locator, _ in prose_targets),
            skeleton="".join(out),
        )

    @staticmethod
    def _targets(text: str):
        parser = MinecraftTextComponentAdapter()
        root = parser._parse(text)
        if root.kind != "object":
            raise ValidationError("Oracle Index _meta.json must be a top-level object")

        targets = []
        for member in root.members:
            locator = parser._escape(member.key)
            value_node = member.value
            if value_node.kind == "string":
                assert isinstance(value_node.value, str)
                targets.append((locator, value_node, value_node.value))
                continue
            if value_node.kind != "object":
                continue
            nested = {item.key: item.value for item in value_node.members}
            name_node = nested.get("name")
            if name_node is None or name_node.kind != "string":
                continue
            assert isinstance(name_node.value, str)
            targets.append((locator + "/name", name_node, name_node.value))
        return parser, tuple(targets)
