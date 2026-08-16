"""MineAI host safety profile layered on the pinned public FormatKit SDK.

The vendored ``mineai_formatkit`` parser/reconstruction modules stay byte-for-byte
copies of the pinned upstream SDK.  MineAI-specific runtime policy lives here:
exact protected-marker order, per-unit candidate hooks, and the two corpus-proven
Patchouli guards retained from the v3.4 acceptance line.
"""

from __future__ import annotations

import re

from mineai_formatkit.core import ProtectedFragment, TranslationPlan, TranslationUnit, ValidationError
from mineai_formatkit.minecraft_lang import _PLACEHOLDER_RE
from mineai_formatkit.modonomicon import (
    ModonomiconAwareLocaleMergePlanner,
    ModonomiconAwareMinecraftLangJsonAdapter,
    ModonomiconBookJsonAdapter as _ModonomiconBookJsonAdapter,
)
from mineai_formatkit.patchouli_base import PatchouliFingerprint
from mineai_formatkit.patchouli_safe import PatchouliBookJsonAdapter as _PatchouliBookJsonAdapter


class MineAiMinecraftLangJsonAdapter(ModonomiconAwareMinecraftLangJsonAdapter):
    """Public locale stack plus the exact marker-order invariant proven by MineAI."""

    name = "minecraft-lang-json"

    def _protect(self, text: str) -> tuple[str, tuple[ProtectedFragment, ...]]:
        masked, protected = super()._protect(text)
        occurrence = {
            match.group(0): index
            for index, match in enumerate(_PLACEHOLDER_RE.finditer(masked))
        }
        return masked, tuple(
            sorted(
                protected,
                key=lambda fragment: occurrence.get(fragment.placeholder, 10**9),
            )
        )

    @staticmethod
    def _restore_protected(unit: TranslationUnit, translated: str) -> str:
        expected = [fragment.placeholder for fragment in unit.protected]
        actual = [match.group(0) for match in _PLACEHOLDER_RE.finditer(translated)]
        if actual != expected:
            raise ValidationError(
                f"Unit {unit.id} changed protected placeholder order: "
                f"expected {expected}, got {actual}"
            )
        restored = translated
        for fragment in unit.protected:
            restored = restored.replace(fragment.placeholder, fragment.value)
        return restored

    def validate_candidate(
        self, plan: TranslationPlan, unit_id: str, candidate: str
    ) -> None:
        if unit_id not in plan.by_id():
            raise ValidationError(f"Unknown translation unit id: {unit_id}")
        self.apply(plan, {unit_id: candidate})


class MineAiLocaleMergePlanner(ModonomiconAwareLocaleMergePlanner):
    """Public FormatKit merge planner using MineAI's exact-order locale adapter."""

    def __init__(self, adapter: MineAiMinecraftLangJsonAdapter | None = None) -> None:
        super().__init__(adapter or MineAiMinecraftLangJsonAdapter())


class MineAiModonomiconBookJsonAdapter(_ModonomiconBookJsonAdapter):
    """Public Modonomicon adapter plus a real-corpus DescriptionId boundary.

    Genetics Resequenced 1.21.1 uses translation DescriptionIds containing a
    slash inside a technical path segment, e.g. ``book.demo.guide.demo/entry.text``.
    Current FormatKit main recognises dotted DescriptionIds but not this proven
    slash form, so without this host compatibility guard the identifier itself
    can be exposed to the LLM. Keep the grammar deliberately narrow and
    whitespace-free; literal prose still falls through to the SDK classifier.
    """

    _DESCRIPTION_ID_WITH_PATH_RE = re.compile(
        r"^[A-Za-z0-9_-]+(?:[./][A-Za-z0-9_-]+){2,}$"
    )

    @staticmethod
    def _is_inline_prose(value: str) -> bool:
        if MineAiModonomiconBookJsonAdapter._DESCRIPTION_ID_WITH_PATH_RE.fullmatch(value):
            return False
        return _ModonomiconBookJsonAdapter._is_inline_prose(value)

    def validate_candidate(
        self, plan: TranslationPlan, unit_id: str, candidate: str
    ) -> None:
        if unit_id not in plan.by_id():
            raise ValidationError(f"Unknown translation unit id: {unit_id}")
        self.apply(plan, {unit_id: candidate})


class MineAiPatchouliBookJsonAdapter(_PatchouliBookJsonAdapter):
    """Public semantic-anchor adapter plus two previously proven MineAI invariants."""

    name = "patchouli-book-json"

    def fingerprint(self, text: str) -> PatchouliFingerprint:
        root = self._parse(text)
        targets = []
        self._collect(root, "", targets)
        selected = [(locator, node) for locator, node, _value in targets]
        out: list[str] = []
        cursor = 0
        for locator, node in sorted(selected, key=lambda item: item[1].start):
            out.append(text[cursor:node.start])
            out.append('"<mineai-patchouli-text>"')
            cursor = node.end
        out.append(text[cursor:])
        return PatchouliFingerprint(
            locators=tuple(locator for locator, _node in selected),
            skeleton="".join(out),
        )

    def validate(self, source_text: str, output_text: str) -> None:
        if self.fingerprint(source_text) != self.fingerprint(output_text):
            raise ValidationError("Patchouli JSON structure changed during reconstruction")

        before = self._values_by_locator(source_text)
        after = self._values_by_locator(output_text)
        if before.keys() != after.keys():
            raise ValidationError("Patchouli translatable field locations changed")
        for locator, original in before.items():
            translated = after[locator]
            if translated.count("\\") != original.count("\\"):
                raise ValidationError(
                    f"Patchouli field {locator} changed literal backslash structure"
                )

    def _values_by_locator(self, text: str) -> dict[str, str]:
        root = self._parse(text)
        targets = []
        self._collect(root, "", targets)
        return {locator: value for locator, _node, value in targets}


__all__ = [
    "MineAiLocaleMergePlanner",
    "MineAiMinecraftLangJsonAdapter",
    "MineAiModonomiconBookJsonAdapter",
    "MineAiPatchouliBookJsonAdapter",
]
