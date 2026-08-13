"""Common typed document model shared by structured translation formats."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable
import re


PathPart = str | int


def _escape_path_part(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _unescape_path_part(value: str) -> str:
    return value.replace("~1", "/").replace("~0", "~")


@dataclass(frozen=True)
class DocumentPath:
    """A reversible path that keeps mapping keys distinct from list indices."""

    parts: tuple[PathPart, ...]

    def encode(self) -> str:
        encoded: list[str] = []
        for part in self.parts:
            if isinstance(part, int):
                encoded.append(str(part))
                continue
            escaped = _escape_path_part(part)
            if part.isdigit():
                escaped = "~s" + escaped
            encoded.append(escaped)
        return "/".join(encoded)

    @classmethod
    def decode(cls, value: str) -> "DocumentPath":
        parts: list[PathPart] = []
        for part in value.split("/"):
            if part.startswith("~s"):
                parts.append(_unescape_path_part(part[2:]))
            elif part.isdigit():
                parts.append(int(part))
            else:
                parts.append(_unescape_path_part(part))
        return cls(tuple(parts))


@dataclass(frozen=True)
class TextNode:
    """One user-visible text value inside an immutable format skeleton."""

    key: str
    path: DocumentPath
    source: str
    existing: str = ""
    translatable: bool = True
    context: str = ""
    metadata: dict[str, Any] = field(default_factory=dict, compare=False)


@dataclass
class StructuredDocument:
    """Format-neutral text AST with a lossless format-specific renderer."""

    source: Any
    nodes: tuple[TextNode, ...]
    renderer: Callable[[dict[str, str]], Any] | None = None

    @property
    def total_translatable(self) -> int:
        return sum(1 for node in self.nodes if node.translatable)

    def unique_translatable_sources(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                node.source
                for node in self.nodes
                if node.translatable
            )
        )

    def pending(self, mode: str) -> dict[str, str]:
        return {
            node.key: node.source
            for node in self.nodes
            if node.translatable
            and (
                mode == "force"
                or not node.existing.strip()
                or node.existing == node.source
            )
        }

    def pending_source_values(
        self,
        mode: str,
        target_regex: str,
        *,
        same_latin_script: bool,
        nodes: Iterable[TextNode] | None = None,
    ) -> tuple[str, ...]:
        selected_nodes = tuple(nodes) if nodes is not None else self.nodes
        return tuple(
            dict.fromkeys(
                node.source
                for node in selected_nodes
                if node.translatable
                and (
                    mode == "force"
                    or not node.existing.strip()
                    or node.existing == node.source
                    or (
                        not same_latin_script
                        and not re.search(target_regex, node.existing)
                    )
                )
            )
        )

    def preserved(self, mode: str) -> dict[str, str]:
        if mode == "force":
            return {}
        return {
            node.key: node.existing
            for node in self.nodes
            if node.existing.strip() and node.existing != node.source
        }

    def translated_count(
        self,
        target_regex: str,
        *,
        same_latin_script: bool = False,
    ) -> int:
        return sum(
            1
            for node in self.nodes
            if node.translatable
            and node.existing.strip()
            and node.existing != node.source
            and (
                same_latin_script
                or not target_regex
                or re.search(target_regex, node.existing)
            )
        )

    def render(self, translations: dict[str, str]) -> Any:
        if self.renderer is None:
            raise ValueError("Structured document does not define a renderer")
        return self.renderer(translations)


def node_map(nodes: Iterable[TextNode]) -> dict[str, TextNode]:
    return {node.key: node for node in nodes}
