from __future__ import annotations

import re
from typing import Mapping

from .core import TranslationPlan, ValidationError
from .patchouli import PatchouliBookJsonAdapter as _BasePatchouliBookJsonAdapter


_TEMPLATE_SOURCE_PATH_RE = re.compile(
    r"(^|/)patchouli_books/[^/]+/en_us/templates/.+\.json$",
    re.IGNORECASE,
)
_TEMPLATE_PLACEHOLDER_RE = re.compile(r"#[A-Za-z_][A-Za-z0-9_.:-]*#?")


class PatchouliBookJsonAdapter(_BasePatchouliBookJsonAdapter):
    """Patchouli adapter with corpus-proven locale and template safety.

    Category/entry JSON exposes only proven player-visible fields. Patchouli's
    ``link_text`` button label is exposed only when it is literal human prose;
    dotted no-whitespace translation keys remain immutable.

    Patchouli ``templates`` are a different surface: real FTB Evolution mods
    use processor-owned variables such as ``#recipe``, ``#tier#``, ``#item1``
    and ``#energy`` inside otherwise ordinary JSON strings. Until a template
    grammar proves which literals are safely localizable, templates are owned
    structurally but expose zero translation units and are reconstructed
    byte-for-byte from the canonical English source.
    """

    name = "patchouli-book-json"

    def matches(self, path: str) -> bool:
        return super().matches(path) or self._is_template_path(path)

    def target_path(self, path: str, target_code: str) -> str:
        slash = path.replace("\\", "/")
        if self._is_template_path(slash):
            return re.sub(
                r"/en_us/templates/",
                f"/{target_code}/templates/",
                slash,
                count=1,
                flags=re.IGNORECASE,
            )
        return super().target_path(path, target_code)

    def prepare(self, path: str, source_text: str) -> TranslationPlan:
        if not self._is_template_path(path):
            return super().prepare(path, source_text)

        root = self._parse(source_text)
        if root.kind != "object":
            raise ValidationError("Patchouli template document must be a JSON object")
        placeholders = tuple(
            match.group(0) for match in _TEMPLATE_PLACEHOLDER_RE.finditer(source_text)
        )
        return TranslationPlan(
            path=path,
            source_text=source_text,
            units=(),
            metadata={
                "patchouli_template_immutable": True,
                "template_placeholders": placeholders,
            },
        )

    def apply(self, plan: TranslationPlan, translations: Mapping[str, str]) -> str:
        if plan.metadata.get("patchouli_template_immutable") is not True:
            return super().apply(plan, translations)
        if translations:
            raise ValidationError("Patchouli templates do not accept translation units")
        root = self._parse(plan.source_text)
        if root.kind != "object":
            raise ValidationError("Patchouli template document must be a JSON object")
        return plan.source_text

    def _collect(self, root, path: str, out) -> None:
        super()._collect(root, path, out)

        members = {member.key: member.value for member in root.members}
        pages = members.get("pages")
        if pages is None:
            return
        for index, page in enumerate(pages.items):
            for member in page.members:
                if member.key != "link_text" or member.value.kind != "string":
                    continue
                value = member.value.value
                assert isinstance(value, str)
                if self._is_literal_link_text(value):
                    out.append(
                        (
                            f"/pages/{index}/{self._escape('link_text')}",
                            member.value,
                            value,
                        )
                    )

    @classmethod
    def _is_literal_link_text(cls, value: str) -> bool:
        return any(char.isspace() for char in value) and cls._has_prose(value)

    @staticmethod
    def _is_template_path(path: str) -> bool:
        slash = "/" + path.replace("\\", "/").lstrip("/")
        return bool(_TEMPLATE_SOURCE_PATH_RE.search(slash))
